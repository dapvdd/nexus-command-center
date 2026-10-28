"""SQLite store for NEXUS Command Center.

Single connection, WAL mode, SQLite-native busy timeout, foreign keys enabled.
All state-changing operations use single guarded SQL statements so concurrent
processes cannot double-execute or lose transitions.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Sequence

from ..errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    PreconditionError,
    StoreUnavailableError,
    ValidationError,
    CREATED, QUEUED, WAITING_APPROVAL, RUNNING, DONE, FAILED, CANCELLED,
    TERMINAL_STATES, CLAIMABLE, OPEN_STATES, ALL_STATES,
)
from .. import errors as E

SCHEMA_VERSION = 1


def _now() -> float:
    return time.time()


def _utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _utcnow_future(seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime(time.time() + max(0.0, seconds)))


def _uuid() -> str:
    return uuid.uuid4().hex


class ConnectionLostError(sqlite3.OperationalError):
    """Raised when the underlying sqlite connection has been lost."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('lennox','alistair','nexus','service')),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'registered',
    capabilities TEXT NOT NULL DEFAULT '[]',
    requires_approval INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT,
    parent_id TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    objective TEXT NOT NULL DEFAULT '',
    acceptance TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '',
    forbidden TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    agent_id TEXT,
    adapter TEXT,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    priority INTEGER NOT NULL DEFAULT 500,
    timeout_s INTEGER NOT NULL DEFAULT 300,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    attempts INTEGER NOT NULL DEFAULT 0,
    run_token TEXT,
    run_started_at TEXT,
    heartbeats TEXT NOT NULL DEFAULT '[]',
    heartbeat_at TEXT,
    result TEXT,
    error TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    cancelled_by TEXT,
    approval_mode TEXT NOT NULL DEFAULT 'none',
    approval_status TEXT NOT NULL DEFAULT 'none',
    approved_by TEXT,
    approved_at TEXT,
    approval_scope TEXT,
    deadline TEXT,
    next_attempt_at TEXT,
    final_after INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (parent_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_queue
    ON tasks(status, next_attempt_at, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);

CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    actor_role TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    object_type TEXT NOT NULL DEFAULT '',
    object_id TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL DEFAULT 'system',
    adapter TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    task_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    task_id TEXT,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class Store:
    """Thread-safe (single-connection, locked) SQLite store."""

    def __init__(self, path: str | os.PathLike, *, create: bool = True,
                 timeout: float = 30.0):
        self.path = os.fspath(path)
        self._lock = threading.RLock()
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._create = create
        self._timeout = timeout
        self._conn: Optional[sqlite3.Connection] = None
        self._open()

    # -- lifecycle ---------------------------------------------------------

    def _open(self) -> None:
        try:
            conn = sqlite3.connect(
                self.path, timeout=self._timeout, check_same_thread=False
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=%d" % (int(self._timeout * 1000)))
            self._conn = conn
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            raise StoreUnavailableError(str(exc)) from exc
        if self._create:
            self._init_schema(conn)

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        try:
            with conn:
                # Always add columns that may be missing from an existing table.
                # SQLite ignores ADD COLUMN if the column already exists.
                # These are backward-compatible additions to the v1 schema.
                for add_col in (
                    "ALTER TABLE tasks ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'",
                    "ALTER TABLE audit ADD COLUMN actor_role TEXT NOT NULL DEFAULT ''",
                    "ALTER TABLE usage ADD COLUMN task_id TEXT",
                    "ALTER TABLE logs ADD COLUMN task_id TEXT",
                ):
                    try:
                        conn.execute(add_col)
                    except sqlite3.OperationalError:
                        pass  # column already present or ignore other errors
                conn.executescript(SCHEMA)
                row = conn.execute(
                    "SELECT value FROM schema_meta WHERE key='version'"
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO schema_meta(key,value) VALUES('version',?)",
                        (str(SCHEMA_VERSION),),
                    )
                elif int(row["value"]) > SCHEMA_VERSION:  # pragma: no cover
                    raise ValidationError(
                        "database schema newer than code", status=500
                    )
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            raise StoreUnavailableError("schema init failed: %s" % exc) from exc

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        conn = self._conn
        if conn is None:
            raise StoreUnavailableError("store is closed")
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            if "database is locked" in str(exc) or "is busy" in str(exc):
                raise StoreUnavailableError(
                    "store busy: %s" % exc, code="nexus.store_busy"
                ) from exc
            if "disk I/O error" in str(exc) or "database disk image is malformed" in str(exc):
                raise StoreUnavailableError(
                    "store degraded: %s" % exc, code="nexus.store_fault"
                ) from exc
            raise
        except sqlite3.DatabaseError as exc:  # pragma: no cover - defensive
            raise StoreUnavailableError(
                "store degraded: %s" % exc, code="nexus.store_fault"
            ) from exc

    def _commit(self) -> None:
        conn = self._conn
        if conn is None:
            raise StoreUnavailableError("store is closed")
        try:
            conn.commit()
        except sqlite3.OperationalError as exc:
            if "is locked" in str(exc) or "is busy" in str(exc):
                raise StoreUnavailableError(
                    "store busy committing: %s" % exc, code="nexus.store_busy"
                ) from exc
            raise

    # -- low-level helpers -------------------------------------------------

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        cur = self._execute(sql, params)
        try:
            return list(cur.fetchall())
        except sqlite3.OperationalError as exc:  # pragma: no cover - defensive
            if "closed" in str(exc):
                raise ConnectionLostError(str(exc)) from exc
            raise

    def audit(self, actor: str, action: str, *, actor_role: str = "",
              object_type: str = "", object_id: str = "",
              detail: str = "", commit: bool = True) -> None:
        with self._lock:
            self._execute(
                "INSERT INTO audit(actor, actor_role, action, object_type,"
                " object_id, detail, created_at) VALUES(?,?,?,?,?,?,?)",
                (actor, actor_role, action, object_type, object_id, detail,
                 _utcnow()),
            )
            if commit:
                self._commit()

    def log(self, message: str, *, level: str = "info", scope: str = "",
            task_id: str | None = None) -> None:
        with self._lock:
            try:
                self._execute(
                    "INSERT INTO logs(ts, level, scope, task_id, message)"
                    " VALUES(?,?,?,?,?)",
                    (_utcnow(), level, scope, task_id, message[:2000]),
                )
                self._commit()
            except (sqlite3.Error, StoreUnavailableError):
                # Structured logs are best-effort; never crash the engine.
                pass

    # -- agents -----------------------------------------------------------

    def register_agent(self, agent_id: str, role: str, name: str, *,
                       description: str = "", capabilities: Sequence[str] = (),
                       requires_approval: bool = False,
                       commit: bool = True) -> dict:
        with self._lock:
            self._execute(
                "INSERT INTO agents(id, role, name, description, status,"
                " capabilities, requires_approval, created_at, updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET"
                " role=excluded.role, name=excluded.name,"
                " description=excluded.description,"
                " capabilities=excluded.capabilities,"
                " requires_approval=excluded.requires_approval,"
                " updated_at=excluded.updated_at",
                (agent_id, role, name, description, "registered",
                 json.dumps(list(capabilities)), 1 if requires_approval else 0,
                 _utcnow(), _utcnow()),
            )
            if commit:
                self._commit()
            self.audit("system", "agent.registered",
                       actor_role="service", object_type="agent",
                       object_id=agent_id, detail=name, commit=False)
            if commit:
                self._commit()
        return self.get_agent(agent_id)

    def get_agent(self, agent_id: str) -> dict:
        with self._lock:
            row = self._rows(
                "SELECT * FROM agents WHERE id=?", (agent_id,)
            )
        if not row:
            raise NotFoundError("agent %s not found" % agent_id)
        return _agent_row(row[0])

    def list_agents(self) -> list[dict]:
        with self._lock:
            rows = self._rows("SELECT * FROM agents ORDER BY created_at")
        return [_agent_row(r) for r in rows]

    # -- task creation ----------------------------------------------------

    def create_task(self, *, title, description="", objective="",
                    acceptance="", scope="", forbidden="", payload=None,
                    agent_id=None, adapter=None, model=None, priority=500,
                    timeout_s=300, max_attempts=3, approval_mode="none",
                    idempotency_key=None, parent_id=None, creator="lennox") -> dict:
        """Create a task or return the existing one for the same idempotency key."""
        if not title:
            raise ValidationError("title is required")
        if len(title) > 300:
            raise ValidationError("title too long")
        if any(len(x) > 2000 for x in (description, objective, acceptance,
                                       scope, forbidden)):
            raise ValidationError("text field too long")
        if priority < 0 or priority > 1000:
            raise ValidationError("priority must be 0..1000")
        if timeout_s <= 0 or timeout_s > 3600 * 24:
            raise ValidationError("timeout_s out of range")
        if max_attempts <= 0 or max_attempts > 50:
            raise ValidationError("max_attempts out of range")
        if approval_mode not in ("none", "required", "review"):
            raise ValidationError("invalid approval_mode")
        if agent_id is not None:
            try:
                self.get_agent(agent_id)
            except NotFoundError:
                pass  # allow foreign agent refs in transient store
        if adapter is not None and len(adapter) > 200:
            raise ValidationError("adapter name too long")
        if parent_id is not None:
            parent = self.get_task(parent_id)
            if parent["status"] in TERMINAL_STATES and parent["status"] != DONE:
                raise ValidationError("parent is terminated without success")
            if parent["status"] == DONE:
                # Child of a done parent: still record parent, but child will
                # be eligible if parent remains done.
                pass

        tid = _uuid()
        now = _utcnow()
        payload_json = json.dumps(payload if payload is not None else {},
                                  separators=(",", ":"))
        if len(payload_json.encode("utf-8")) > 128 * 1024:
            raise ValidationError("task payload too large")
        with self._lock:
            if idempotency_key is not None:
                dup = self._rows(
                    "SELECT id FROM tasks WHERE parent_id IS ? AND"
                    " idempotency_key=?", (parent_id, idempotency_key)
                )
                if dup:
                    return self.get_task(dup[0]["id"])
            status = (
                WAITING_APPROVAL
                if approval_mode == "required" and agent_is_privileged(agent_id)
                else _initial_status(approval_mode, agent_id, adapter)
            )
            # 'review' mode: created → queued → waiting_approval at run time.
            self._execute(
                "INSERT INTO tasks(id, idempotency_key, parent_id, title,"
                " description, objective, acceptance, scope, forbidden, payload,"
                " agent_id, adapter, model, status, priority, timeout_s,"
                " max_attempts, approval_mode, approval_status, created_at,"
                " updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, idempotency_key, parent_id, title, description, objective,
                 acceptance, scope, forbidden, payload_json, agent_id, adapter,
                 model, status, priority, timeout_s, max_attempts, approval_mode,
                 "none" if approval_mode == "none" else status,
                 now, now),
            )
            self._commit()
        self.audit(creator, "task.created", actor_role=_role_for(creator),
                   object_type="task", object_id=tid,
                   detail=title, commit=False)
        with self._lock:
            self._commit()
        return self.get_task(tid)

    def get_task(self, task_id: str) -> dict:
        with self._lock:
            rows = self._rows("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not rows:
            raise NotFoundError("task %s not found" % task_id)
        return _task_row(rows[0])

    def list_tasks(self, *, status: str | None = None, agent_id: str | None = None,
                   limit: int = 200, offset: int = 0, only_open: bool = False) -> list[dict]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status=?")
            params.append(status)
        if agent_id:
            where.append("agent_id=?")
            params.append(agent_id)
        if only_open:
            where.append("status IN ('created','queued','waiting_approval','running')")
        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY priority DESC, created_at ASC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self._lock:
            rows = self._rows(sql, params)
        return [_task_row(r) for r in rows]

    def count_tasks(self, *, status: str | None = None) -> int:
        sql = "SELECT COUNT(*) AS n FROM tasks"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        with self._lock:
            row = self._rows(sql, params)[0]
        return int(row["n"])

    # -- atomic transitions -----------------------------------------------

    def _update(self, sql: str, params: Sequence[Any]) -> bool:
        with self._lock:
            cur = self._execute(sql, params)
            self._commit()
            return cur.rowcount == 1

    def claim_task(self, task_id: str, run_token: str,
                   agent_id: str | None = None) -> bool:
        """Atomically claim a queued task for a worker. Returns True on success."""
        where = "WHERE id=? AND status='queued' AND run_token IS NULL"
        if agent_id:
            where += " AND (agent_id IS NULL OR agent_id=?)"
        sql = ("UPDATE tasks SET status='running', run_token=?,"
               " run_started_at=?, heartbeat_at=?, updated_at=?,"
               " attempts=attempts+1 WHERE id=? AND status='queued' AND"
               " run_token IS NULL")
        params: list[Any] = [run_token, _utcnow(), _utcnow(), _utcnow(), task_id]
        if agent_id:
            # Only this named agent may claim; others must be rejected.
            row = self._rows("SELECT agent_id FROM tasks WHERE id=?", (task_id,))
            if not row:
                raise NotFoundError("task %s not found" % task_id)
            if row[0]["agent_id"] not in (None, agent_id):
                return False  # silently not ours to claim
        return self._update(sql, params)

    def heartbeat(self, task_id: str, run_token: str) -> bool:
        return self._update(
            "UPDATE tasks SET heartbeat_at=? WHERE id=? AND run_token=?",
            (_utcnow(), task_id, run_token),
        )

    def finish_task(self, task_id: str, run_token: str, *, success: bool,
                    result: dict | None = None, error: str | None = None) -> bool:
        """Atomically transition a running task to done/failed.

        Returns False if the run_token no longer owns the task (e.g. it was
        cancelled during execution) — the caller must treat that as aborted.
        """
        if success:
            sql = "UPDATE tasks SET status='done', result=?, error=NULL, heartbeat_at=?, updated_at=? WHERE id=? AND run_token=?"
            params = [json.dumps(result) if result is not None else None,
                      _utcnow(), _utcnow(), task_id, run_token]
        else:
            sql = "UPDATE tasks SET status='failed', result=?, error=?, heartbeat_at=?, updated_at=? WHERE id=? AND run_token=?"
            params = [json.dumps(result) if result is not None else None,
                      error, _utcnow(), _utcnow(), task_id, run_token]
        return self._update(sql, params)

    def mark_cancelled(self, task_id: str, *, actor: str, reason: str = "") -> bool:
        """Atomically cancel a task that is not already terminal."""
        return self._update(
            "UPDATE tasks SET status='cancelled', cancel_requested=1,"
            " cancelled_by=?, updated_at=? WHERE id=? AND status NOT IN"
            " ('done','failed','cancelled')",
            (actor, _utcnow(), task_id),
        )

    def request_cancel(self, task_id: str, *, actor: str, reason: str = "") -> dict:
        """Set cancel_requested on a running task; cancel immediately otherwise."""
        t = self.get_task(task_id)
        if t["status"] in TERMINAL_STATES:
            raise ConflictError("task already terminal")
        if t["status"] == RUNNING:
            with self._lock:
                self._execute(
                    "UPDATE tasks SET cancel_requested=1, updated_at=?"
                    " WHERE id=? AND status='running'",
                    (_utcnow(), task_id),
                )
                self._commit()
            self.audit(actor, "task.cancel_requested", object_type="task",
                       object_id=task_id, detail=reason, commit=False)
            with self._lock:
                self._commit()
            return self.get_task(task_id)
        ok = self.mark_cancelled(task_id, actor=actor, reason=reason)
        if not ok:
            # Someone else got there first; still valid final state.
            final = self.get_task(task_id)
            if final["status"] == "cancelled":
                self.audit(actor, "task.cancel_duplicate", object_type="task",
                           object_id=task_id, detail=reason)
                return final
            raise ConflictError("task state changed concurrently")
        self.audit(actor, "task.cancelled", object_type="task", object_id=task_id,
                   detail=reason)
        return self.get_task(task_id)

    def approve_task(self, task_id: str, *, approver: str, scope: str,
                     commit: bool = True) -> dict:
        """Record a durable, bound approval. Does not execute the task.

        The engine checks approval_status on any attempt to run. This method does
        not change execution state — it can only move a waiting_approval task.
        """
        t = self.get_task(task_id)
        if t["status"] != WAITING_APPROVAL:
            raise PreconditionError(
                "task %s is not awaiting approval (status=%s)" % (task_id, t["status"])
            )
        match_scope = t["approval_scope"] if t["approval_scope"] else t["scope"]
        if match_scope and scope.strip() != match_scope:
            raise ForbiddenError("approval scope does not match task scope")
        with self._lock:
            row = self._execute(
                "UPDATE tasks SET approval_status='approved', approved_by=?,"
                " approved_at=?, updated_at=?, status='queued' WHERE id=? AND"
                " status='waiting_approval' AND approval_status!='approved'",
                (approver, _utcnow(), _utcnow(), task_id),
            )
            self._commit()
            if row.rowcount != 1:
                raise ConflictError("task approval changed concurrently")
        self.audit(approver, "task.approved", object_type="task",
                   object_id=task_id, detail=scope, commit=False)
        with self._lock:
            self._commit()
        return self.get_task(task_id)

    def reject_task(self, task_id: str, *, approver: str,
                    reason: str = "") -> dict:
        t = self.get_task(task_id)
        if t["status"] != WAITING_APPROVAL:
            raise PreconditionError(
                "task %s is not awaiting approval" % task_id
            )
        with self._lock:
            self._execute(
                "UPDATE tasks SET status='cancelled', approval_status='rejected',"
                " cancelled_by=?, updated_at=?, cancel_requested=1"
                " WHERE id=? AND status='waiting_approval'",
                (approver_who := approver, _utcnow(), task_id),
            )
            self._commit()
        self.audit(approver, "task.rejected", object_type="task",
                   object_id=task_id, detail=reason, commit=False)
        with self._lock:
            self._commit()
        return self.get_task(task_id)

    def retry_task(self, task_id: str, *, actor: str) -> dict:
        """Requeue a FAILED/CANCELLED task (new run, reset attempt?).

        Design: retry preserves attempts and idempotency_key and run_token reset,
        backoff recorded; a manual retry bumps max_attempts effectively as a new
        authorization act.
        """
        t = self.get_task(task_id)
        if t["status"] not in (FAILED, CANCELLED):
            raise PreconditionError("only FAILED/CANCELLED tasks can be retried")
        if t["approval_mode"] == "required":
            raise ForbiddenError("requires_approval tasks must be re-approved")
        with self._lock:
            self._execute(
                "UPDATE tasks SET status='queued', run_token=NULL,"
                " result=NULL, error=NULL, cancel_requested=0,"
                " approval_status='none', updated_at=?, next_attempt_at=?"
                " WHERE id=? AND status IN ('failed','cancelled')",
                (_utcnow(), _utcnow(), task_id),
            )
            self._commit()
        self.audit(actor, "task.retried", object_type="task", object_id=task_id,
                   detail="manual retry", commit=False)
        with self._lock:
            self._commit()
        return self.get_task(task_id)

    def requeue_with_backoff(self, task_id: str, *, delay: float = 60.0,
                             error: str = "") -> None:
        """Return a failed task to QUEUED with exponential backoff.

        delay is seconds from now before the task becomes eligible again.
        Preserves attempts and run_token is cleared so a new worker can claim it.
        """
        with self._lock:
            self._execute(
                "UPDATE tasks SET status='queued', run_token=NULL, error=?,"
                " next_attempt_at=?, updated_at=? WHERE id=?",
                (error[:500], _utcnow_future(delay), _utcnow(), task_id),
            )
            self._commit()
        self.audit("nexus-engine", "task.requeued", actor_role="system",
                   object_type="task", object_id=task_id,
                   detail="backoff %.0fs" % delay, commit=False)
        with self._lock:
            self._commit()

    def requeue_stale(self, task_id: str, *, reason: str) -> None:
        """Reclaim an orphan run. Only called when heartbeat is confirmed stale."""
        with self._lock:
            self._execute(
                "UPDATE tasks SET status='queued', run_token=NULL,"
                " next_attempt_at=?, updated_at=? WHERE id=? AND status='running'",
                (_utcnow(), _utcnow(), task_id),
            )
            self._commit()

    def fail_stale(self, task_id: str, *, reason: str) -> None:
        """Mark an orphan as failed when retry budget is exhausted."""
        with self._lock:
            self._execute(
                "UPDATE tasks SET status='failed', error=?, updated_at=?"
                " WHERE id=? AND status='running'",
                ("orphan: %s" % reason[:300], _utcnow(), task_id),
            )
            self._commit()
        self.audit("nexus-engine", "task.orphan_failed", actor_role="system",
                   object_type="task", object_id=task_id, detail=reason[:300],
                   commit=False)
        with self._lock:
            self._commit()

    # -- usage / analytics ------------------------------------------------

    def record_usage(self, *, agent_id="system", adapter="", model="",
                     kind="token", amount=0, task_id=None) -> None:
        with self._lock:
            self._execute(
                "INSERT INTO usage(id, agent_id, adapter, model, kind, amount,"
                " task_id, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (_uuid(), agent_id, adapter, model, kind, max(0, int(amount)),
                 task_id, _utcnow()),
            )
            self._commit()

    def usage_summary(self, *, agent_id: str | None = None,
                      model: str | None = None, adapter: str | None = None,
                      since: str | None = None) -> dict:
        where = []
        params = []
        if agent_id:
            where.append("agent_id=?")
            params.append(agent_id)
        if model:
            where.append("model=?")
            params.append(model)
        if adapter:
            where.append("adapter=?")
            params.append(adapter)
        if since:
            where.append("created_at>=?")
            params.append(since)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        sql = ("SELECT kind, SUM(amount) AS total, COUNT(*) AS n FROM usage" + clause +
               " GROUP BY kind")
        with self._lock:
            rows = self._rows(sql, params)
        out = {"kinds": {}, "total": 0}
        for r in rows:
            out["kinds"][r["kind"]] = {"amount": int(r["total"]), "count": int(r["n"])}
            out["total"] += int(r["total"])
        return out

    def usage_by_model(self, *, since: str | None = None) -> list[dict]:
        clause = " WHERE created_at>=?" if since else ""
        params: list[Any] = [since] if since else []
        sql = ("SELECT model, adapter, SUM(amount) AS total, COUNT(*) AS n"
               " FROM usage" + clause + " GROUP BY model, adapter ORDER BY total DESC")
        with self._lock:
            rows = self._rows(sql, params)
        return [{"model": r["model"] or "system", "adapter": r["adapter"] or "",
                 "total": int(r["total"]), "count": int(r["n"])} for r in rows]

    # -- audit / logs ------------------------------------------------------

    def list_audit(self, *, limit: int = 200, actor: str | None = None,
                   action: str | None = None) -> list[dict]:
        where: list[str] = []
        params: list[Any] = []
        if actor:
            where.append("actor=?")
            params.append(actor)
        if action:
            where.append("action=?")
            params.append(action)
        sql = "SELECT * FROM audit"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._rows(sql, params)
        return [_audit_row(r) for r in rows]

    def list_logs(self, *, limit: int = 200, scope: str | None = None) -> list[dict]:
        where = ""
        params: list[Any] = []
        if scope:
            where = " WHERE scope=?"
            params.append(scope)
        sql = "SELECT * FROM logs" + where + " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._rows(sql, params)
        return [{"seq": r["seq"], "ts": r["ts"], "level": r["level"],
                 "scope": r["scope"], "task_id": r["task_id"],
                 "message": r["message"]} for r in rows]

    # -- health -----------------------------------------------------------

    def health(self) -> dict:
        """Real store reachability: a write-then-read self check."""
        with self._lock:
            try:
                row = self._rows("SELECT value FROM settings WHERE key='health'")
                cur = row[0]["value"] if row else None
                nxt = str(_tick(cur))
                self._execute(
                    "INSERT INTO settings(key, value) VALUES('health', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (nxt,),
                )
                self._commit()
                check = self._rows("SELECT value FROM settings WHERE key='health'")[0]
                ok = check["value"] == nxt
            except (sqlite3.Error, StoreUnavailableError) as exc:
                return {"ok": False, "error": str(exc)}
        return {"ok": ok, "version": SCHEMA_VERSION, "path": self.path}


def _tick(value) -> int:
    try:
        return int(value) + 1
    except (TypeError, ValueError):
        return 1


# ---------------------------------------------------------------------------
# Row → dict mappers and helpers
# ---------------------------------------------------------------------------

def _agent_row(r) -> dict:
    return {
        "id": r["id"], "role": r["role"], "name": r["name"],
        "description": r["description"], "status": r["status"],
        "capabilities": json.loads(r["capabilities"] or "[]"),
        "requires_approval": bool(r["requires_approval"]),
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def _task_row(r) -> dict:
    return {
        "id": r["id"], "idempotency_key": r["idempotency_key"],
        "parent_id": r["parent_id"], "title": r["title"],
        "description": r["description"], "objective": r["objective"],
        "acceptance": r["acceptance"], "scope": r["scope"],
        "forbidden": r["forbidden"], "payload": _load_json(r["payload"]),
        "agent_id": r["agent_id"],
        "adapter": r["adapter"], "model": r["model"], "status": r["status"],
        "priority": int(r["priority"]), "timeout_s": int(r["timeout_s"]),
        "max_attempts": int(r["max_attempts"]), "attempts": int(r["attempts"]),
        "run_token": r["run_token"], "run_started_at": r["run_started_at"],
        "heartbeat_at": r["heartbeat_at"],
        "heartbeats": json.loads(r["heartbeats"] or "[]"),
        "result": _load_json(r["result"]), "error": r["error"],
        "cancel_requested": bool(r["cancel_requested"]),
        "cancelled_by": r["cancelled_by"], "approval_mode": r["approval_mode"],
        "approval_status": r["approval_status"], "approved_by": r["approved_by"],
        "approved_at": r["approved_at"], "approval_scope": r["approval_scope"],
        "deadline": r["deadline"], "next_attempt_at": r["next_attempt_at"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def _audit_row(r) -> dict:
    return {
        "seq": r["seq"], "actor": r["actor"], "actor_role": r["actor_role"],
        "action": r["action"], "object_type": r["object_type"],
        "object_id": r["object_id"], "detail": r["detail"],
        "created_at": r["created_at"],
    }


def _load_json(text) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {"_malformed": True, "raw": str(text)[:500]}


def _initial_status(approval_mode: str, agent_id: str | None,
                    adapter: str | None) -> str:
    """Determine the initial durable status of a freshly created task."""
    if approval_mode == "required":
        return WAITING_APPROVAL
    # 'review' tasks become queued first; the engine routes them through an
    # approval gate at execution selection time.
    return QUEUED


def agent_is_privileged(agent_id: str | None) -> bool:
    """Whether the target agent holds execution authority in this build.

    LENNOX never executes. ALISTAIR may execute bounded, low-impact reads.
    NEXUS is the execution specialist; its tasks still pass approval gates when
    the policy says so. Because the caller-supplied agent_id is only a routing
    hint here (not authorization), the default is conservative: normal tasks are
    queued, and the policy layer decides whether a gate applies.
    """
    return False


def _role_for(actor: str) -> str:
    # Actor strings are normalized at the HTTP boundary to trusted identifiers.
    # 'lennox' is the human; 'alistair' / 'nexus' are agents; 'system' is the engine.
    if actor in ("lennox", "alistair", "nexus", "system"):
        return actor
    return "service"

