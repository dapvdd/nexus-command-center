"""Execution engine for NEXUS Command Center.

Responsibilities (Phase 4):
- Durable task queues backed by the store's atomic claims.
- Explicit task state transitions (never read-then-write).
- Retry with exponential backoff and persisted attempt counts.
- Worker heartbeats with orphan recovery.
- Failure recovery across process restarts.
- Tool permission boundaries via the adapter registry + approval gates.
- Structured execution logs and usage tracking.
- Safe cancellation: a running worker sees cancel_requested at boundaries.

The engine never executes a task that requires approval unless the store shows a
durable approval bound to that task. This is enforced in selection, not merely
in the UI.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from typing import Optional

from .. import errors as E
from ..store import Store
from ..store.db import _utcnow
from ..adapters.base import Adapter, AdapterError, AdapterResult, TaskContext
from ..adapters.registry import Registry

log = logging.getLogger("nexus.engine")

DB_TIMEOUT = 15.0
DEFAULT_BACKOFF_BASE = 2.0
DEFAULT_BACKOFF_CAP = 120.0

_LOCK = threading.Lock()
_ACTIVE_ENGINES = set()


def _run_token() -> str:
    return "run-" + uuid.uuid4().hex


def _backoff(attempt: int, *, base: float = DEFAULT_BACKOFF_BASE,
             cap: float = DEFAULT_BACKOFF_CAP,
             jitter: bool = True) -> float:
    """Exponential backoff; attempt is 1-based."""
    delay = min(cap, base * (2 ** (max(0, attempt - 1))))
    if jitter:
        delay = delay * (0.5 + ((time.time() % 1.0) * 0.5))
    return delay


class Engine:
    """Orchestrates workers over the store. One engine per store instance.

    The engine holds *no durable state of its own*: everything (status, attempts,
    run_token, heartbeats, approvals) lives in the SQLite store, so a crashed
    engine can recover by inspecting the store.
    """

    def __init__(self, store: Store, registry: Registry,
                 workers: int = 4, *, workspace: str | None = None,
                 poll_interval: float = 0.25,
                 heartbeat_interval: float = 1.0,
                 orphan_after: float = 12.0):
        self.store = store
        self.registry = registry
        self.workers_n = max(1, int(workers))
        self.poll_interval = max(0.05, float(poll_interval))
        self.heartbeat_interval = max(0.05, float(heartbeat_interval))
        self.orphan_after = max(1.0, float(orphan_after))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._idle = threading.Event()
        self._idle.set()
        self._busy = 0  # how many workers are currently executing
        self._busy_lim = self.workers_n
        self._busy_lim_lock = threading.Semaphore(self.workers_n)
        if workspace is None:
            workspace = tempfile.mkdtemp(prefix="nexus-ws-")
        else:
            os.makedirs(workspace, exist_ok=True)
        self.workspace = workspace
        # Wire the artifact adapter's private workspace.
        from ..adapters.artifact import ArtifactBuilder
        ArtifactBuilder.WORKSPACE_ROOT = self.workspace
        with _LOCK:
            _ACTIVE_ENGINES.add(id(self))

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._threads:
            return
        for i in range(self.workers_n):
            t = threading.Thread(target=self._worker_loop, name="nexus-worker-%d" % i,
                                 daemon=True)
            t.start()
            self._threads.append(t)
        self.store.log("engine started with %d workers" % self.workers_n,
                       scope="engine", level="info")

    def stop(self, *, timeout: float = 10.0) -> None:
        self._stop.set()
        self._idle.set()
        for t in self._threads:
            t.join(timeout=timeout / max(1, len(self._threads)))
        self._threads.clear()

    def close(self) -> None:
        self.stop()
        with _LOCK:
            _ACTIVE_ENGINES.discard(id(self))

    # -- submission helpers --------------------------------------------------

    def submit(self, *, title, description="", objective="", acceptance="",
               scope="", forbidden="", agent_id=None, adapter=None, model=None,
               priority=500, timeout_s=300, max_attempts=3,
               approval_mode="none", idempotency_key=None, parent_id=None,
               payload=None, creator="lennox") -> dict:
        """Create and queue a task (returns the durable task dict)."""
        payload = payload if payload is not None else {}
        if adapter is not None:
            cls = self.registry.resolve(adapter)
            try:
                payload = cls.validate_input(payload)
            except AdapterError as exc:
                raise E.ValidationError(exc.message) from exc
            if approval_mode == "auto":
                approval_mode = "required" if cls.default_requires_approval else "none"
        task = self.store.create_task(
            title=title, description=description, objective=objective,
            acceptance=acceptance, scope=scope, forbidden=forbidden,
            payload=payload, agent_id=agent_id, adapter=adapter,
            model=model, priority=priority, timeout_s=timeout_s,
            max_attempts=max_attempts, approval_mode=approval_mode,
            idempotency_key=idempotency_key, parent_id=parent_id,
            creator=creator,
        )
        return task

    # -- selection ---------------------------------------------------------

    def _eligible_tasks(self) -> list[str]:
        """Queued tasks whose deps are satisfied and that are not waiting on
        approval. Returns a bounded candidate list (never an unbounded scan)."""
        now = _utcnow()
        rows = self.store.list_tasks(status="queued", limit=100, only_open=False)
        out = []
        for r in rows:
            if r["approval_mode"] == "required" and \
                    r["approval_status"] != "approved":
                continue  # engine never runs an unapproved gated task
            if r["next_attempt_at"] and r["next_attempt_at"] > now:
                continue
            if r["parent_id"]:
                try:
                    parent = self.store.get_task(r["parent_id"])
                except E.NotFoundError:
                    continue
                if parent["status"] != "done":
                    continue
            out.append(r["id"])
        return out

    # -- worker loop --------------------------------------------------------

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            task_id = None
            try:
                task_id = self._pick_and_run()
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("worker fault: %s", exc)
                self.store.log("worker fault: %s" % exc, scope="engine",
                               level="error")
            if task_id is None:
                self._idle.set()
                self._stop.wait(self.poll_interval)
            else:
                self._idle.clear()

    def _pick_and_run(self) -> Optional[str]:
        tries = 0
        for candidate in self._eligible_tasks():
            if self._stop.is_set():
                return None
            tries += 1
            try:
                if self._claim_and_run(candidate):
                    return candidate
            except E.NotFoundError:
                continue
            except E.StoreUnavailableError:
                self._stop.wait(0.5)
                return None
            except Exception as exc:  # pragma: no cover - defensive
                self.store.log("claim fault %s: %s" % (candidate, exc),
                               scope="engine", level="error")
        return None

    def _claim_and_run(self, task_id: str) -> bool:
        if not self._busy_lim_lock.acquire(blocking=False):
            return False  # concurrency ceiling reached; leave task queued
        self._busy += 1
        try:
            token = _run_token()
            if not self.store.claim_task(task_id, token):
                return False
            task = self.store.get_task(task_id)
            self.store.log("worker claimed task %s" % task_id, scope="engine",
                           task_id=task_id)
            try:
                self._execute_claimed(task, token)
            finally:
                self._busy -= 1
                self._busy_lim_lock.release()
            return True
        except Exception:
            self._busy = max(0, self._busy - 1)
            self._busy_lim_lock.release()
            raise

    def _execute_claimed(self, task: dict, token: str) -> None:
        """Run one already-claimed task under its run_token and persist outcome."""
        adapter_name = task.get("adapter")
        adapter_cls = None
        if adapter_name:
            try:
                adapter_cls = self.registry.resolve(adapter_name)
            except E.NotFoundError:
                self._finish_failure(task, token, "unknown adapter %r" % adapter_name,
                                     fatal=True)
                return
        if task.get("approval_mode") == "required" and \
                task.get("approval_status") != "approved":
            # Belt and suspenders: never run a gated task without durable approval.
            self.store.log("refused to run %s without approval" % task["id"],
                           scope="engine", level="error", task_id=task["id"])
            self._finish_failure(task, token,
                                 "approval gate not satisfied; execution refused",
                                 fatal=True)
            return

        def is_cancelled() -> bool:
            try:
                return self.store.get_task(task["id"]).get("cancel_requested", False)
            except Exception:
                return False

        ctx = TaskContext(
            task_id=task["id"], title=task["title"], objective=task.get("objective") or "",
            acceptance=task.get("acceptance") or "", scope=task.get("scope") or "",
            forbidden=task.get("forbidden") or "", agent_id=task.get("agent_id"),
            model=task.get("model"), timeout_s=task.get("timeout_s") or 300,
            max_attempts=task.get("max_attempts") or 3,
            attempts=task.get("attempts") or 1, payload=task.get("payload") or {},
            is_cancelled=is_cancelled,
        )
        ctx._checkpoint_hook = self._on_checkpoint

        # Heartbeat thread for this run.
        stop_hb = threading.Event()

        def _heartbeats():
            while not stop_hb.wait(self.heartbeat_interval):
                if self._stop.is_set():
                    return
                try:
                    self.store.heartbeat(task["id"], token)
                except Exception:
                    return

        hb = threading.Thread(target=_heartbeats, name="hb-%s" % task["id"],
                              daemon=True)
        hb.start()
        started = time.time()
        outcome = None  # AdapterResult | None
        adapter_exc = None  # AdapterError | None
        try:
            if adapter_cls is None:
                raise AdapterError("no adapter bound to task", retryable=False)
            outcome = adapter_cls.run(ctx)
        except AdapterError as exc:
            adapter_exc = exc
        except Exception as exc:  # defensive: never leak a raw exception
            adapter_exc = AdapterError("adapter raised %s" % type(exc).__name__)
        finally:
            stop_hb.set()
            hb.join(timeout=0.5)
            elapsed = time.time() - started
            self._persist_outcome(task, token, outcome, adapter_exc, elapsed)

    def _persist_outcome(self, task: dict, token: str,
                         outcome, adapter_exc, elapsed: float) -> None:
        """Exactly one terminal persistence per claimed run."""
        if adapter_exc is not None:
            self._on_adapter_failure(task, token, adapter_exc)
            return
        if not isinstance(outcome, AdapterResult) or not outcome.ok:
            self._on_adapter_failure(
                task, token,
                AdapterError("adapter produced no outcome", retryable=False))
            return
        ok = self.store.finish_task(task["id"], token,
                                    success=True, result=outcome.data)
        if not ok:
            self.store.log("late worker %s: terminal state already owned"
                           % task["id"], scope="engine", level="info",
                           task_id=task["id"])
            return
        self.store.record_usage(
            agent_id=task.get("agent_id") or "system",
            adapter=task.get("adapter") or "",
            model=task.get("model") or "local",
            kind=outcome.usage.get("kind", "token"),
            amount=outcome.usage.get("amount", 0),
            task_id=task["id"])
        self.store.log("task %s done in %.2fs" % (task["id"], elapsed),
                       scope="engine", task_id=task["id"])
        self.store.audit("nexus-engine", "task.done", actor_role="system",
                         object_type="task", object_id=task["id"],
                         detail="completed in %.2fs" % elapsed)

    def _on_checkpoint(self, task_id: str, message: str) -> None:
        try:
            self.store.log("checkpoint: %s" % message[:300], scope="checkpoint",
                           task_id=task_id)
        except Exception:
            pass

    def _on_adapter_failure(self, task: dict, token: str, exc: AdapterError) -> None:
        msg = (exc.message or "adapter failure")[:500]
        if not exc.retryable:
            self._finish_failure(task, token, msg, fatal=True)
            return
        t = self.store.get_task(task["id"])
        if t.get("cancel_requested"):
            self._finish_failure(task, token, "cancelled during execution (after failure)",
                                 fatal=True, cancelled=True)
            return
        if (t.get("attempts") or 0) < (t.get("max_attempts") or 3):
            delay = _backoff(t.get("attempts") or 1)
            self.store.requeue_with_backoff(task["id"], delay=delay, error=msg)
            self.store.log("scheduled retry for %s in %.1fs" % (task["id"], delay),
                           scope="engine", task_id=task["id"])
        else:
            self._finish_failure(task, token, msg, fatal=True)

    def _finish_failure(self, task: dict, token: str, error: str, *,
                        fatal: bool = False, cancelled: bool = False) -> None:
        ok = self.store.finish_task(task["id"], token,
                                    success=False, error=error[:500])
        if not ok:
            # Token lost ownership (e.g. cancelled concurrently). Mark audit only.
            self.store.log("late worker %s: terminal state already owned" % task["id"],
                           scope="engine", level="info", task_id=task["id"])
            return
        status = "cancelled" if cancelled else "failed"
        self.store.audit("nexus-engine", "task.%s" % status,
                         actor_role="system", object_type="task",
                         object_id=task["id"], detail=error[:500])

    # -- recovery ------------------------------------------------------------

    def recover_orphans(self) -> int:
        """Reclaim runs whose heartbeat is stale. Returns count reclaimed.

        Safe under restarts: only tasks still in 'running' with an old heartbeat
        are requeued (retry budget permitting) or failed. A task whose terminal
        state is committed is never touched.
        """
        import sqlite3
        rows = self.store.list_tasks(status="running", limit=500, only_open=False)
        now_f = time.time()
        reclaimed = 0
        for r in rows:
            hb = r.get("heartbeat_at")
            if not hb:
                continue
            try:
                hb_f = time.mktime(time.strptime(hb, "%Y-%m-%dT%H:%M:%SZ"))
            except (ValueError, TypeError):
                continue
            if now_f - hb_f < self.orphan_after:
                continue
            attempts = r.get("attempts") or 0
            max_attempts = r.get("max_attempts") or 3
            if attempts < max_attempts:
                self.store.requeue_stale(task_id=r["id"],
                                         reason="worker heartbeat lost")
                self.store.log("recovered orphan %s (attempts %d)" % (r["id"], attempts),
                               scope="engine", task_id=r["id"])
            else:
                self.store.fail_stale(task_id=r["id"], reason="worker heartbeat lost; "
                                      "retry budget exhausted")
                self.store.log("orphan %s failed: retry budget exhausted" % r["id"],
                               scope="engine", level="error", task_id=r["id"])
            reclaimed += 1
        return reclaimed

    # -- state snapshots for the UI --------------------------------------------

    def system_status(self) -> dict:
        counts = {}
        for s in ("created", "queued", "waiting_approval", "running",
                  "done", "failed", "cancelled"):
            try:
                counts[s] = self.store.count_tasks(status=s)
            except Exception:
                counts[s] = -1
        hb = []
        for r in self.store.list_tasks(status="running", limit=100):
            hb.append({"task_id": r["id"], "heartbeat_at": r["heartbeat_at"],
                       "title": r["title"]})
        return {
            "state": "running" if self._threads else "stopped",
            "workers": self.workers_n,
            "busy": self._busy,
            "counts": counts,
            "running": hb,
            "workspace": self.workspace,
            "store_ok": self.store.health().get("ok", False),
        }

    def eligible_now(self) -> list[dict]:
        ids = self._eligible_tasks()
        return [self.store.get_task(i) for i in ids]


# Store-side helpers used by the engine live on the Store class; add them here
# as functions the engine calls via the store instance. To keep the store module
# focused, they are attached below.
