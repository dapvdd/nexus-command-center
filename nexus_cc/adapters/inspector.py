"""Inspector adapter — read-only, represents ALISTAIR's investigation role.

This adapter is explicitly read-only (no writes, no process spawn). It returns
symmetry. Its purpose is to prove the worker/adapter boundary, the 
R_E-vs-real distinction (all data simulated), and cost tracking.
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
    reg.register(Inspector)


class Inspector(Adapter):
    name = "inspector"
    description = ("Read-only investigation stub that returns symmetric "
                   "inspection results for synthetic inputs.")
    capabilities = ("observe",)
    supported_models = ("local",)
    default_requires_approval = False  # read-only, low impact

    @classmethod
    def validate_input(cls, payload: dict) -> dict:
        require_payload_fields(payload, ("target", "checks"))
        target = sanitize_text(payload.get("target", ""), 200)
        checks_raw = payload.get("checks")
        if not isinstance(checks_raw, list) or len(checks_raw) > 40:
            raise AdapterError("checks must be a list of <=40 strings",
                               retryable=False)
        checks = []
        for item in checks_raw:
            text = sanitize_text(item, 200)
            if text:
                checks.append(text)
        if not checks:
            raise AdapterError("at least one check required", retryable=False)
        return {"target": target, "checks": checks}

    @classmethod
    def run(cls, ctx: TaskContext) -> AdapterResult:
        p = ctx.payload
        results = []
        for check in p["checks"]:
            if ctx.is_cancelled():
                raise AdapterError("cancelled during inspection", retryable=False)
            time.sleep(0.01)
            results.append({"check": check,
                            "finding": _proxy_finding(check, p["target"])})
        return AdapterResult(
            ok=True,
            data=bounded_result({
                "target": p["target"],
                "checks": results,
                "provenance": "SIMULATED",
            }),
            usage={"amount": len(results), "kind": "checks"},
            kind="inspection",
        )


def _proxy_finding(check: str, target: str) -> dict:
    """Deterministic synthetic finding — explicitly labeled, never real telemetry."""
    digest = sum(map(ord, check + target)) % 3
    labels = ["no anomaly suspected", "minor anomaly flagged (synthetic)",
              "anomaly suspected (synthetic)"]
    return {
        "label": labels[digest],
        "confidence": (0.6 + digest * 0.1),
        "evidence": "synthetic fixture only",
    }
