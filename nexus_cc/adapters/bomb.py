"""Bomb adapter — a deterministic always-fail adapter for recovery testing.

This adapter intentionally raises a NON-retryable AdapterError on every run. It
is registered only when the engine is constructed with a lab flag; it is NOT part
of the default production registry. Its purpose is to prove that a run-time
adapter failure (after a successful claim) leaves the task FAILED correctly.
"""

from __future__ import annotations

from .base import (
    Adapter,
    AdapterError,
    AdapterResult,
    TaskContext,
    require_payload_fields,
    sanitize_text,
)


class Bomb(Adapter):
    name = "bomb"
    description = ("Test-only adapter that always fails non-retryably. "
                   "Not registered in the default registry.")
    capabilities = ("test:fault",)
    default_requires_approval = False
    fail_message = "injected non-retryable adapter failure"

    @classmethod
    def validate_input(cls, payload: dict) -> dict:
        require_payload_fields(payload, ())
        return {}

    @classmethod
    def run(cls, ctx: TaskContext) -> AdapterResult:
        raise AdapterError(cls.fail_message, retryable=False,
                           code="adapter.bomb")
