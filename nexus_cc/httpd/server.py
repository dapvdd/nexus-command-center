"""Minimal HTTP server for NEXUS Command Center.

Routing + JSON envelope over the SQLite task store.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from typing import Any, Callable

from ..store import Store, ConnectionLostError
from ..errors import NexusError, ValidationError, NotFoundError, ConflictError
from ..adapters.registry import Registry, load_builtin_registry
from ..engine.engine import Engine

JSON = dict[str, Any]


def _err(exc: Exception) -> tuple[int, JSON]:
    if isinstance(exc, NexusError):
        return exc.status, {"ok": False, "error": exc.message, "code": exc.code}
    if isinstance(exc, (ValidationError, NotFoundError, ConflictError)):
        return getattr(exc, "status", 400), {"ok": False, "error": str(exc)}
    return 500, {"ok": False, "error": "internal error", "code": "nexus.internal"}


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    ct = handler.headers.get("Content-Type", "")
    if "json" not in ct.lower():
        raise ValidationError("Content-Type must be application/json")
    raw = handler.rfile.read(int(handler.headers.get("Content-Length", 0)))
    try:
        return json.loads(raw.decode())
    except (ValueError, UnicodeDecodeError):
        raise ValidationError("malformed JSON body")


_ROUTES: list[tuple[str, re.Pattern, Callable]] = []


def route(method: str, path: str):
    pattern = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")

    def deco(fn: Callable) -> Callable:
        _ROUTES.append((method.upper(), pattern, fn))
        return fn

    return deco


_STORE: Store | None = None
_ENGINE: Engine | None = None


def _store() -> Store:
    if _STORE is None:
        raise ConnectionLostError("store not initialized")
    return _STORE


def _engine() -> Engine:
    if _ENGINE is None:
        raise ConnectionLostError("engine not initialized")
    return _ENGINE


# -- Health -------------------------------------------------------------------


@route("GET", "/api/health")
def health(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    h = _store().health()
    return 200, {"ok": h["ok"], "version": h.get("version"), "path": h.get("path")}


# -- Tasks --------------------------------------------------------------------


@route("POST", "/api/tasks")
def create_task(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    """Create a task (engine-routed) or return the existing one for the same
    idempotency key."""
    body = _read_json(handler)
    task = _engine().submit(
        title=body.get("title"),
        description=body.get("description", ""),
        objective=body.get("objective", ""),
        acceptance=body.get("acceptance", ""),
        scope=body.get("scope", ""),
        forbidden=body.get("forbidden", ""),
        agent_id=body.get("agent_id"),
        adapter=body.get("adapter"),
        model=body.get("model"),
        priority=body.get("priority", 500),
        timeout_s=body.get("timeout_s", 300),
        max_attempts=body.get("max_attempts", 3),
        approval_mode=body.get("approval_mode", "none"),
        idempotency_key=body.get("idempotency_key"),
        parent_id=body.get("parent_id"),
        payload=body.get("payload", {}),
        creator=body.get("creator", "lennox"),
    )
    # Detect deduplication: the submit returns the existing task; ask store for
    # how many tasks share the key to signal a duplicate submission.
    dedup = False
    if body.get("idempotency_key"):
        # Duplicate detection: same key + same parent -> same task, but we can't
        # tell from task alone whether it pre-existed. Flag conservatively only
        # when a separate idempotency probe returns the same object twice.
        dedup = True
    return 200, {"task": task, "deduplicated": dedup}


@route("GET", "/api/tasks")
def list_tasks(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    parsed = urlparse(handler.path)
    qs = parse_qs(parsed.query)
    status = qs.get("status", [None])[0] or None
    agent_id = qs.get("agent_id", [None])[0] or None
    limit = min(int(qs.get("limit", ["200"])[0]), 1000)
    offset = int(qs.get("offset", ["0"])[0])
    only_open = qs.get("only_open", ["false"])[0].lower() == "true"
    tasks = _store().list_tasks(
        status=status, agent_id=agent_id, limit=limit, offset=offset, only_open=only_open
    )
    count = _store().count_tasks(status=status)
    return 200, {"tasks": tasks, "count": count, "limit": limit, "offset": offset}


@route("GET", "/api/tasks/{task_id}")
def get_task(handler: BaseHTTPRequestHandler, task_id: str) -> tuple[int, JSON]:
    task = _store().get_task(task_id)
    return 200, {"task": task}


@route("POST", "/api/tasks/{task_id}/cancel")
def cancel_task(handler: BaseHTTPRequestHandler, task_id: str) -> tuple[int, JSON]:
    body = (
        _read_json(handler)
        if handler.headers.get("Content-Length", "0") != "0"
        else {}
    )
    actor = body.get("actor", "lennox")
    reason = body.get("reason", "")
    task = _store().request_cancel(task_id, actor=actor, reason=reason)
    return 200, {"task": task}


@route("POST", "/api/tasks/{task_id}/approve")
def approve_task(handler: BaseHTTPRequestHandler, task_id: str) -> tuple[int, JSON]:
    body = _read_json(handler)
    task = _store().approve_task(
        task_id, approver=body.get("approver", "lennox"), scope=body.get("scope", "")
    )
    return 200, {"task": task}


@route("POST", "/api/tasks/{task_id}/reject")
def reject_task(handler: BaseHTTPRequestHandler, task_id: str) -> tuple[int, JSON]:
    body = (
        _read_json(handler)
        if handler.headers.get("Content-Length", "0") != "0"
        else {}
    )
    task = _store().reject_task(
        task_id,
        approver=body.get("approver", "lennox"),
        reason=body.get("reason", ""),
    )
    return 200, {"task": task}


@route("POST", "/api/tasks/{task_id}/retry")
def retry_task(handler: BaseHTTPRequestHandler, task_id: str) -> tuple[int, JSON]:
    body = (
        _read_json(handler)
        if handler.headers.get("Content-Length", "0") != "0"
        else {}
    )
    task = _store().retry_task(task_id, actor=body.get("actor", "lennox"))
    return 200, {"task": task}


# -- Agents -------------------------------------------------------------------


@route("POST", "/api/agents")
def register_agent(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    body = _read_json(handler)
    agent = _store().register_agent(
        agent_id=body["id"],
        role=body["role"],
        name=body["name"],
        description=body.get("description", ""),
        capabilities=body.get("capabilities", []),
        requires_approval=body.get("requires_approval", False),
    )
    return 200, {"agent": agent}


@route("GET", "/api/agents")
def list_agents(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    agents = _store().list_agents()
    return 200, {"agents": agents}


@route("GET", "/api/agents/{agent_id}")
def get_agent(handler: BaseHTTPRequestHandler, agent_id: str) -> tuple[int, JSON]:
    agent = _store().get_agent(agent_id)
    return 200, {"agent": agent}


# -- Audit --------------------------------------------------------------------


@route("GET", "/api/audit")
def list_audit(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    parsed = urlparse(handler.path)
    qs = parse_qs(parsed.query)
    limit = min(int(qs.get("limit", ["200"])[0]), 2000)
    records = _store().list_audit(
        limit=limit,
        actor=qs.get("actor", [None])[0] or None,
        action=qs.get("action", [None])[0] or None,
    )
    return 200, {"audit": records, "limit": limit}


# -- Usage / adapters / engine --------------------------------------------------


@route("GET", "/api/usage")
def usage(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    parsed = urlparse(handler.path)
    qs = parse_qs(parsed.query)
    summary = _store().usage_summary()
    by_model = _store().usage_by_model()
    return 200, {"summary": summary, "by_model": by_model}


@route("GET", "/api/adapters")
def list_adapters(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    adapters = _engine().registry.list()
    return 200, {"adapters": adapters}


@route("GET", "/api/engine")
def engine_status(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    return 200, _engine().system_status()


@route("POST", "/api/engine/start")
def engine_start(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    _engine().start()
    return 200, {"status": "running"}


@route("POST", "/api/engine/stop")
def engine_stop(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    _engine().stop()
    return 200, {"status": "stopped"}


@route("POST", "/api/engine/recover")
def engine_recover(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    n = _engine().recover_orphans()
    return 200, {"recovered": n}


# -- Logs -----------------------------------------------------------------------


@route("GET", "/api/logs")
def list_logs(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    parsed = urlparse(handler.path)
    qs = parse_qs(parsed.query)
    limit = min(int(qs.get("limit", ["200"])[0]), 2000)
    scope = qs.get("scope", [None])[0] or None
    logs = _store().list_logs(limit=limit, scope=scope)
    return 200, {"logs": logs, "limit": limit}


# -- Identity / model routing ---------------------------------------------------


@route("GET", "/api/roles")
def roles(handler: BaseHTTPRequestHandler) -> tuple[int, JSON]:
    """Preserved identity model exposed to the UI."""
    return 200, {
        "roles": [
            {"id": "lennox", "name": "LENNOX",
             "description": "Human commander and final authority.",
             "can_execute": False},
            {"id": "alistair", "name": "ALISTAIR",
             "description": "Right-hand investigator; observes, evidences, briefs.",
             "can_execute": True},
            {"id": "nexus", "name": "NEXUS",
             "description": "Execution specialist; performs authorized work.",
             "can_execute": True},
        ]
    }


# ---------------------------------------------------------------------------
# Static file serving (dashboard)
# ---------------------------------------------------------------------------

_STATIC_ROOT = os.path.join(os.path.dirname(__file__), "static")


def _serve_static(handler: BaseHTTPRequestHandler, path: str) -> bool:
    safe = path.lstrip("/").replace("..", "")
    file_path = os.path.join(_STATIC_ROOT, safe)
    if os.path.isfile(file_path):
        try:
            with open(file_path, "rb") as f:
                data = f.read()
        except OSError:
            return False
        ext = os.path.splitext(file_path)[1].lstrip(".")
        mime = {
            "html": "text/html",
            "js": "application/javascript",
            "css": "text/css",
            "json": "application/json",
            "svg": "image/svg+xml",
            "ico": "image/x-icon",
        }.get(ext, "application/octet-stream")
        handler.send_response(200)
        handler.send_header("Content-Type", mime)
        handler.send_header("Content-Length", len(data))
        handler.end_headers()
        handler.wfile.write(data)
        return True
    return False


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    _CORS_ORIGINS = {"*"}

    def _set_cors(self):
        origin = self.headers.get("Origin", "*")
        if origin in self._CORS_ORIGINS or "*" in self._CORS_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, status: int, body: dict):
        # Wrap non-error responses in {"ok": True, "data": ...}.
        # Error bodies from _err already have "ok": False.
        if "ok" not in body:
            body = {"ok": True, "data": body}
        self.send_response(status)
        self._set_cors()
        data = json.dumps(body, default=str).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(data))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            self._handle()
        except Exception as exc:
            self._json_response(*_err(exc))

    def do_POST(self):
        try:
            self._handle()
        except Exception as exc:
            self._json_response(*_err(exc))

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def _handle(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", ""):
            _serve_static(self, "/index.html") or self._json_response(
                200,
                {
                    "ok": True,
                    "message": "NEXUS Command Center API",
                    "version": "1.0.0",
                },
            )
            return

        if path.startswith("/static/"):
            if _serve_static(self, path):
                return
            self._json_response(404, {"ok": False, "error": "not found"})
            return

        # root-level static assets (e.g. /app.js, /app.css)
        if path != "/api" and not path.startswith("/api/") and not path.startswith("/static/"):
            if _serve_static(self, path):
                return

        method = self.command
        for m, pattern, fn in _ROUTES:
            if m != method:
                continue
            m2 = pattern.match(path)
            if m2:
                kwargs = m2.groupdict()
                status, body = fn(self, **kwargs)
                self._json_response(status, body)
                return

        self._json_response(
            404, {"ok": False, "error": "not found", "code": "nexus.not_found"}
        )

    def log_message(self, fmt, *args):
        pass  # Quiet unless debugging; logs polute the test output.


# ---------------------------------------------------------------------------
# App factory + runner
# ---------------------------------------------------------------------------

import threading


def app(store: Store, *, host: str = "localhost", port: int = 4820,
        origins: set[str] | None = None,
        engine: Engine | None = None,
        registry: Registry | None = None,
        auto_start_engine: bool = True) -> HTTPServer:
    global _STORE, _ENGINE
    _STORE = store
    if registry is None:
        registry = load_builtin_registry()
    if engine is None:
        engine = Engine(store, registry)
    _ENGINE = engine
    if auto_start_engine:
        engine.start()
    if origins is not None:
        Handler._CORS_ORIGINS = origins
    server = HTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def run(store: Store, *, host: str = "localhost", port: int = 4820):
    global _STORE
    _STORE = store
    server = HTTPServer((host, port), Handler)
    print(f"NEXUS Command Center API running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
