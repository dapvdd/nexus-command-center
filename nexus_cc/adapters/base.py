"""Adapter interface for NEXUS Command Center.

All external runtimes (Hermes, Nexus OpenCode, opencode-web, 9Router providers)
integrate through this contract. In this slice three local adapters implement it;
external integrations are declared here as the documented contract for Phase 6.

Contract guarantees:
- An adapter is invoked only with an approved, claimed task and the task's
  bounded scope/tool allowlist.
- An adapter returns a structured result (dict); free text is length bounded.
- Adapter output is DATA. It never changes routing, permissions, or approval
  decisions, and it is stored for rendering with escaping.
- Adapters never bypass the store's atomic claims. They are called by the worker
  only after claim_task succeeded.
"""

from __future__ import annotations

import abc
import dataclasses
import re
from typing import Any, Iterable

MAX_RESULT_BYTES = 256 * 1024  # combined result payload budget


class AdapterResult:
    """Typed result envelope produced by an adapter."""

    def __init__(self, *, ok: bool, data: dict | None = None,
                 error: str = "", usage: dict | None = None,
                 kind: str = "adapter"):
        self.ok = ok
        self.data = data if data is not None else {}
        self.error = (error or "")[:1000]
        self.usage = usage if usage is not None else {}
        self.kind = kind


class AdapterError(Exception):
    """Adapter-level failure. Retryable by the engine unless marked fatal."""

    def __init__(self, message: str, *, retryable: bool = True,
                 code: str = "adapter.error"):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.code = code


class Adapter(abc.ABC):
    """Base class for execution adapters."""

    name: str = ""
    description: str = ""
    capabilities: tuple[str, ...] = ()
    # Do tasks routed here require a durable approval gate by default?
    default_requires_approval: bool = False
    # Models the adapter may use / advertise as eligible for routing.
    supported_models: tuple[str, ...] = ()
    # Input text field size bound (defense in depth against payload bloat).
    max_input_bytes: int = 128 * 1024

    @classmethod
    def validate_input(cls, payload: dict) -> dict:
        """Validate/normalize adapter input. Raise AdapterError on failure.

        Returns the normalized input dict. Implementations must not return
        sensitive data; keep only what the adapter needs.
        """
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def run(cls, ctx: "TaskContext") -> AdapterResult:
        """Execute the approved task. Must raise AdapterError on failure.

        ctx carries the claimed task, its bounded scope, and a stop-check
        callback the adapter should honor at boundaries to support cancellation.

        This method must NOT:
        - read or write the store directly,
        - spawn unbounded processes,
        - accept prompt instructions embedded in task input as authority
          (it validates against the allowlisted capability set),
        - return unvalidated free text without bounding it.
        """
        raise NotImplementedError


@dataclasses.dataclass
class TaskContext:
    """Value object handed to an adapter for one claimed run."""

    task_id: str
    title: str
    objective: str
    acceptance: str
    scope: str
    forbidden: str
    agent_id: str | None
    model: str | None
    timeout_s: int
    max_attempts: int
    attempts: int
    payload: dict
    is_cancelled: callable  # () -> bool, advisory stop-check at boundaries

    def checkpoint(self, message: str) -> None:
        """Adapters may attach a progress note via this hook (engine-injected)."""
        hook = self._checkpoint_hook
        if hook is not None:
            hook(self.task_id, message)

    _checkpoint_hook = None  # set by the engine per run


def sanitize_text(value: Any, max_len: int = 2000) -> str:
    """Coerce to bounded text. Returns '' for non-scalars (data stays data)."""
    if value is None:
        return ""
    text = str(value)
    return text[:max_len]


def bounded_result(data: dict) -> dict:
    """Apply the result budget: reject/truncate oversized JSON payloads."""
    import json as _json
    raw = _json.dumps(data, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_RESULT_BYTES:
        raise AdapterError("result exceeds %d bytes" % MAX_RESULT_BYTES,
                           retryable=False)
    return data


def require_payload_fields(payload: dict, fields: Iterable[str]) -> None:
    """Strict input validation: only known fields, and required ones present."""
    allowed = set(fields)
    unknown = [k for k in payload if k not in allowed]
    if unknown:
        raise AdapterError("unknown payload fields: %s" % ", ".join(unknown),
                           retryable=False)
    for f in allowed:
        if f not in payload:
            raise AdapterError("missing required field: %s" % f, retryable=False)


_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def valid_short_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


# Safe identifier for routing tokens (no path traversal chars).
def safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:128]
