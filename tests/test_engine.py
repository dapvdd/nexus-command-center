"""Round-trip: full task lifecycle through the engine (store + worker + adapter)."""

from __future__ import annotations

import os
import time

from nexus_cc.store import Store
from nexus_cc.adapters.registry import load_builtin_registry
from nexus_cc.engine.engine import Engine
from nexus_cc.adapters.echo import Echo
from nexus_cc.adapters.artifact import ArtifactBuilder
from nexus_cc.errors import NotFoundError


def _wait_until(fn, timeout=6.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            last = fn()
        except NotFoundError:
            time.sleep(step)
            continue
        if last:
            return last
        time.sleep(step)
    raise AssertionError("condition not met; last=%r" % (last,))


def _run_to_terminal(store, eng, task):
    tid = task["id"]
    _wait_until(lambda: store.get_task(tid)["status"] not in
               ("created", "queued", "running"),
               timeout=8.0)
    return store.get_task(tid)


class TestEngineLifecycle:
    def test_echo_task_reaches_done(self, db_path):
        store = Store(db_path)
        store.register_agent("lennox", "lennox", "David")
        store.register_agent("nexus", "nexus", "Nexus")
        reg = load_builtin_registry()
        eng = Engine(store, reg, workers=1, poll_interval=0.05,
                     heartbeat_interval=0.05)
        eng.start()
        try:
            t = eng.submit(title="echo round trip", agent_id="nexus",
                           adapter="echo", payload={"message": "hi"},
                           creator="lennox")
            out = _run_to_terminal(store, eng, t)
            assert out["status"] == "done", out
            assert out["result"] and out["result"].get("echo") == "hi"
            # usage was recorded
            summary = store.usage_summary()
            assert summary.get("total", 0) >= 1
        finally:
            eng.close()
            store.close()

    def test_approval_gate_blocks_execution(self, db_path):
        store = Store(db_path)
        store.register_agent("lennox", "lennox", "David")
        store.register_agent("nexus", "nexus", "Nexus")
        reg = load_builtin_registry()
        eng = Engine(store, reg, workers=1, poll_interval=0.05,
                     heartbeat_interval=0.05)
        eng.start()
        try:
            t = eng.submit(title="needs approval", agent_id="nexus",
                           adapter="echo", payload={"message": "secret"},
                           approval_mode="required", creator="alistair")
            assert t["status"] == "waiting_approval"
            time.sleep(0.5)
            # The engine must NOT have executed it.
            now = store.get_task(t["id"])
            assert now["status"] == "waiting_approval", now
            assert now["attempts"] == 0, "execution happened without approval"
        finally:
            eng.close()
            store.close()

    def test_approval_then_execution(self, db_path):
        store = Store(db_path)
        store.register_agent("lennox", "lennox", "David")
        store.register_agent("nexus", "nexus", "Nexus")
        reg = load_builtin_registry()
        eng = Engine(store, reg, workers=1, poll_interval=0.05,
                     heartbeat_interval=0.05)
        eng.start()
        try:
            t = eng.submit(title="needs approval", agent_id="nexus",
                           adapter="echo", payload={"message": "now ok"},
                           approval_mode="required", scope="go",
                           creator="alistair")
            store.approve_task(t["id"], approver="lennox", scope="go")
            out = _run_to_terminal(store, eng, t)
            assert out["status"] == "done", out
        finally:
            eng.close()
            store.close()

    def test_retry_after_injected_failure(self, db_path):
        store = Store(db_path)
        store.register_agent("lennox", "lennox", "David")
        store.register_agent("nexus", "nexus", "Nexus")
        reg = load_builtin_registry()
        eng = Engine(store, reg, workers=1, poll_interval=0.05,
                     heartbeat_interval=0.05)
        eng.start()
        try:
            Echo.fault_injection = {"fail_first": True, "reason": "injected",
                                    "retryable": True}
            try:
                t = eng.submit(title="flaky", agent_id="nexus", adapter="echo",
                               max_attempts=3, payload={"message": "retry"},
                               creator="lennox")
                out = _run_to_terminal(store, eng, t)
            finally:
                Echo.fault_injection = {"fail_first": False}
            assert out["status"] == "done", out
            assert out["attempts"] >= 2, out
        finally:
            eng.close()
            store.close()

    def test_success_after_failure(self, db_path):
        """A non-retryable adapter error leaves the task FAILED after 1 attempt."""
        store = Store(db_path)
        store.register_agent("lennox", "lennox", "David")
        store.register_agent("nexus", "nexus", "Nexus")
        reg = load_builtin_registry()
        # Register the always-fail adapter (test-only)
        from nexus_cc.adapters.bomb import Bomb
        reg.register(Bomb)
        eng = Engine(store, reg, workers=1, poll_interval=0.05,
                     heartbeat_interval=0.05)
        eng.start()
        try:
            t = eng.submit(title="will-fail", agent_id="nexus", adapter="bomb",
                           max_attempts=1, creator="lennox")
            out = _run_to_terminal(store, eng, t)
            assert out["status"] == "failed", out
            assert out["attempts"] == 1, out
        finally:
            eng.close()
            store.close()
