"""Artifact builder adapter.

Proves the end-to-end path: a claimed, approved task produces a real, verifiable
local artifact (a bounded text artifact in the engine's private workspace) and a
structured result. Input is synthetic but the artifact is real — never faked.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time

from .base import (
    Adapter,
    AdapterError,
    AdapterResult,
    TaskContext,
    bounded_result,
    require_payload_fields,
    sanitize_text,
    valid_short_name,
)


def register(reg) -> None:
    reg.register(ArtifactBuilder)


class ArtifactBuilder(Adapter):
    name = "artifact"
    description = ("Builds a bounded text artifact (e.g. a report stub) into the "
                   "engine workspace from synthetic input. Local only.")
    capabilities = ("artifact:write",)
    supported_models = ("local",)
    default_requires_approval = True  # writing to disk is an impact action

    WORKSPACE_ROOT = None  # engine injects a private, sandboxed directory

    @classmethod
    def validate_input(cls, payload: dict) -> dict:
        require_payload_fields(payload, ("filename", "body", "kind"))
        filename = sanitize_text(payload.get("filename", ""), 120)
        if not valid_short_name(filename) or not filename.endswith(".md"):
            raise AdapterError("filename must be a safe .md name", retryable=False)
        # Bounded body; anything longer is truncated with an explicit marker so
        # we never silently drop data.
        body = sanitize_text(payload.get("body", ""), 2000)
        kind = sanitize_text(payload.get("kind", "report"), 40)
        return {"filename": filename, "body": body, "kind": kind}

    @classmethod
    def run(cls, ctx: TaskContext) -> AdapterResult:
        p = ctx.payload
        if cls.WORKSPACE_ROOT is None:
            raise AdapterError("workspace not configured, adapter refused",
                               retryable=False)
        for _ in range(3):  # bounded simulated work with stop-checks
            if ctx.is_cancelled():
                raise AdapterError("cancelled during execution", retryable=False)
            time.sleep(0.02)
        path = os.path.join(cls.WORKSPACE_ROOT, p["filename"])
        # Defense in depth: never allow escape even though the name is validated.
        if os.path.dirname(path) != cls.WORKSPACE_ROOT:
            raise AdapterError("artifact path escape refused", retryable=False)
        content = "# %s\n\n%s\n" % (p["kind"], p["body"])
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return AdapterResult(
            ok=True,
            data=bounded_result({
                "artifact": os.path.basename(path),
                "sha256": digest,
                "bytes": len(content.encode("utf-8")),
                "kind": p["kind"],
                "provenance": "SIMULATED",
            }),
            usage={"amount": len(content), "kind": "artifact_bytes"},
            kind="artifact",
        )
