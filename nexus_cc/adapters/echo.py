"""Echo adapter — a deterministic reflection adapter for testing routing,
retries, and provider/model routing without any real compute.

Controlled failure injection via payload fields is enabled ONLY by an explicit
lab flag on the adapter, never via task input alone, so that task text can never
change execution policy.
"""

from __future__ import annotations

import time

from .base import (
    Adapter,
    AdapterError,
    AdapterResult,
    TaskContext,
    bounded_result,
    require_payload_fields,
    sanitize_text,
)


def register(reg) -> None:
    reg.register(Echo)


class Echo(Adapter):
    name = "echo"
    description = ("Deterministic echo adapter for testing routing/retry/usage. "
                   "Returns the bounded input back as a structured result.")
    capabilities = ("echo",)
    supported_models = ("local", "tiprouter/deepseek-v-4.1-flash")
    default_requires_approval = False
    # Lab-controlled failure injection: disabled unless set programmatically.
    fault_injection = {"fail_first": False, "sleep_s": 0.0, "retryable": True}

    @classmethod
    def validate_input(cls, payload: dict) -> dict:
        require_payload_fields(payload, ("message",))
        return {"message": sanitize_text(payload.get("message", ""), 500)}

    @classmethod
    def run(cls, ctx: TaskContext) -> AdapterResult:
        if cls.fault_injection.get("sleep_s"):
            time.sleep(cls.fault_injection["sleep_s"])
        if cls.fault_injection.get("fail_first") and ctx.attempts == 1:
            message = cls.fault_injection.get("reason", "injected transient failure")
            raise AdapterError(message,
                               retryable=cls.fault_injection.get("retryable", True))
        p = ctx.payload
        return AdapterResult(
            ok=True,
            data=bounded_result({
                "echo": p["message"],
                "attempts": ctx.attempts,
                "task_id": ctx.task_id,
                "model": ctx.model,
                "provenance": "SIMULATED",
            }),
            usage={"amount": max(1, len(p["message"])), "kind": "echo_chars"},
            kind="echo",
        )
