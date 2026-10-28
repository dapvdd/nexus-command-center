"""Tests for the SQLite task store.

Focus:
- Schema init, WAL mode, foreign keys
- Task creation + idempotency
- Task retrieval and listing
- State transitions
- Invalid input handling
- Restart recovery (store re-open)
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

from nexus_cc.store import Store, ConnectionLostError
from nexus_cc.errors import (
    ValidationError, NotFoundError, ConflictError, PreconditionError
)


class TestSchemaInit:
    def test_wal_mode(self, db_path):
        s = Store(db_path)
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            assert row[0].lower() == "wal", "WAL mode should be persisted"
        finally:
            conn.close()
            s.close()

    def test_health_check(self, store):
        h = store.health()
        assert h["ok"] is True
        assert "version" in h
        assert "path" in h


class TestTaskCreation:
    def test_create_minimal_task(self, store):
        t = store.create_task(title="Test task")
        assert t["id"] is not None
        assert t["title"] == "Test task"
        assert t["status"] == "queued"
        assert t["priority"] == 500
        assert t["attempts"] == 0
        assert t["idempotency_key"] is None

    def test_create_full_task(self, store):
        t = store.create_task(
            title="Full task",
            description="A task with all fields",
            objective="Achieve X",
            acceptance="X is achieved",
            scope="read:data write:results",
            agent_id="nexus",
            adapter="echo",
            model="gpt-4",
            priority=800,
            timeout_s=600,
            max_attempts=5,
            approval_mode="required",
            idempotency_key="full-task-1",
        )
        assert t["title"] == "Full task"
        assert t["agent_id"] == "nexus"
        assert t["adapter"] == "echo"
        assert t["priority"] == 800
        assert t["status"] == "waiting_approval"  # required approval
        assert t["approval_mode"] == "required"

    def test_create_idempotency(self, store):
        t1 = store.create_task(
            title="Idempotent task",
            idempotency_key="idem-1",
        )
        t2 = store.create_task(
            title="Idempotent task (retry)",
            idempotency_key="idem-1",
        )
        assert t1["id"] == t2["id"], "Same idempotency key returns same task"
        assert t1["title"] == t2["title"]

    def test_idempotency_key_uniqueness_per_parent(self, store):
        parent = store.create_task(title="Parent", idempotency_key="parent-1")
        t1 = store.create_task(
            title="Child 1", parent_id=parent["id"], idempotency_key="child-key"
        )
        t2 = store.create_task(
            title="Child 2", parent_id=parent["id"], idempotency_key="child-key"
        )
        assert t1["id"] == t2["id"]

        # Different parent with same idempotency key = different task
        other_parent = store.create_task(title="Other parent", idempotency_key="other-p")
        t3 = store.create_task(
            title="Child 3", parent_id=other_parent["id"], idempotency_key="child-key"
        )
        assert t3["id"] != t1["id"]


class TestValidation:
    def test_title_required(self, store):
        with pytest.raises(ValidationError):
            store.create_task(title="")

    def test_title_too_long(self, store):
        with pytest.raises(ValidationError):
            store.create_task(title="A" * 301)

    def test_priority_out_of_range(self, store):
        with pytest.raises(ValidationError):
            store.create_task(title="t", priority=1001)

    def test_timeout_out_of_range(self, store):
        with pytest.raises(ValidationError):
            store.create_task(title="t", timeout_s=0)
        with pytest.raises(ValidationError):
            store.create_task(title="t", timeout_s=86401)


class TestTaskRetrieval:
    def test_get_task(self, store):
        t = store.create_task(title="Get me")
        g = store.get_task(t["id"])
        assert g["id"] == t["id"]
        assert g["title"] == "Get me"

    def test_get_task_not_found(self, store):
        with pytest.raises(NotFoundError):
            store.get_task("nonexistent-id")

    def test_list_tasks(self, store):
        store.create_task(title="Task 1", priority=100)
        store.create_task(title="Task 2", priority=200)
        store.create_task(title="Task 3", priority=300)
        tasks = store.list_tasks()
        assert len(tasks) == 3

    def test_list_tasks_with_status_filter(self, store):
        t = store.create_task(title="Filtered")
        tasks = store.list_tasks(status=t["status"])
        assert all(t["status"] == "queued" for t in tasks)

    def test_list_tasks_pagination(self, store):
        for i in range(10):
            store.create_task(title=f"Task {i}", priority=i)
        page1 = store.list_tasks(limit=3, offset=0)
        page2 = store.list_tasks(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3
        assert page1[0]["id"] != page2[0]["id"]

    def test_count_tasks(self, store):
        assert store.count_tasks() == 0
        store.create_task(title="One")
        assert store.count_tasks() == 1
        store.create_task(title="Two")
        assert store.count_tasks() == 2
        assert store.count_tasks(status="queued") == 2


class TestStateTransitions:
    def test_claim_and_finish(self, store):
        t = store.create_task(title="Claimable")
        assert t["status"] == "queued"
        token = "run-token-abc"
        ok = store.claim_task(t["id"], token)
        assert ok is True
        retrieved = store.get_task(t["id"])
        assert retrieved["status"] == "running"
        assert retrieved["run_token"] == token
        assert retrieved["attempts"] == 1

        ok = store.finish_task(t["id"], token, success=True, result={"ok": True})
        assert ok is True
        final = store.get_task(t["id"])
        assert final["status"] == "done"
        assert final["result"] == {"ok": True}

    def test_claim_wrong_token(self, store):
        t = store.create_task(title="Claimable")
        store.claim_task(t["id"], "token-1")
        ok = store.finish_task(t["id"], "wrong-token", success=True)
        assert ok is False
        assert store.get_task(t["id"])["status"] == "running"

    def test_finish_unclaimed_task(self, store):
        t = store.create_task(title="Never claimed")
        ok = store.finish_task(t["id"], "random-token", success=True)
        assert ok is False

    def test_heartbeat(self, store):
        t = store.create_task(title="Running task")
        token = "token-xyz"
        store.claim_task(t["id"], token)
        ok = store.heartbeat(t["id"], token)
        assert ok is True
        ok = store.heartbeat(t["id"], "wrong-token")
        assert ok is False

    def test_cancel(self, store):
        t = store.create_task(title="Cancellable")
        ok = store.mark_cancelled(t["id"], actor="lennox", reason="user request")
        assert ok is True
        assert store.get_task(t["id"])["status"] == "cancelled"

    def test_cancel_terminal_task(self, store):
        t = store.create_task(title="Done task")
        store.claim_task(t["id"], "tok")
        store.finish_task(t["id"], "tok", success=True)
        ok = store.mark_cancelled(t["id"], actor="lennox")
        assert ok is False
        assert store.get_task(t["id"])["status"] == "done"

    def test_request_cancel_running(self, store):
        t = store.create_task(title="Running")
        token = "rt"
        store.claim_task(t["id"], token)
        t2 = store.request_cancel(t["id"], actor="lennox", reason="stop")
        assert t2["cancel_requested"] is True

    def test_retry_failed(self, store):
        t = store.create_task(title="Retry me")
        store.claim_task(t["id"], "tok")
        store.finish_task(t["id"], "tok", success=False, error="failed")
        assert store.get_task(t["id"])["status"] == "failed"
        t2 = store.retry_task(t["id"], actor="lennox")
        assert t2["status"] == "queued"


class TestApproval:
    def test_approve_waiting_task(self, store):
        t = store.create_task(title="Needs approval", approval_mode="required",
                             scope="full")
        assert t["status"] == "waiting_approval"
        t2 = store.approve_task(t["id"], approver="lennox", scope="full")
        assert t2["approval_status"] == "approved"
        assert t2["status"] == "queued"  # Approval releases it to the engine

    def test_approve_non_waiting_task(self, store):
        t = store.create_task(title="No approval needed")
        with pytest.raises(PreconditionError):
            store.approve_task(t["id"], approver="lennox", scope="full")

    def test_reject_task(self, store):
        t = store.create_task(title="Reject me", approval_mode="required")
        t2 = store.reject_task(t["id"], approver="lennox", reason="out of scope")
        assert t2["status"] == "cancelled"
        assert t2["approval_status"] == "rejected"


class TestRestartRecovery:
    def test_store_reopens_on_restart(self, db_path):
        s1 = Store(db_path)
        t = s1.create_task(title="Restart test", idempotency_key="restart-1")
        s1.close()

        s2 = Store(db_path)
        g = s2.get_task(t["id"])
        assert g["id"] == t["id"]
        assert g["title"] == "Restart test"
        assert g["status"] == "queued"
        s2.close()

    def test_claimed_task_persists_across_restart(self, db_path):
        s1 = Store(db_path)
        t = s1.create_task(title="Persistent claim")
        token = "persist-token"
        s1.claim_task(t["id"], token)
        s1.close()

        s2 = Store(db_path)
        g = s2.get_task(t["id"])
        assert g["status"] == "running"
        assert g["run_token"] == token
        s2.close()


class TestAuditLog:
    def test_audit_on_create(self, store):
        store.create_task(title="Audit test", creator="lennox")
        records = store.list_audit(limit=10)
        assert any(r["action"] == "task.created" and r["actor"] == "lennox" for r in records)

    def test_audit_on_cancel(self, store):
        t = store.create_task(title="Cancel audit")
        store.request_cancel(t["id"], actor="lennox", reason="test")
        records = store.list_audit(limit=10, action="task.cancelled")
        assert any(r["object_id"] == t["id"] for r in records)


class TestAgents:
    def test_register_and_list_agent(self, store):
        agent = store.register_agent("nexus", "nexus", "NEXUS execution specialist")
        assert agent["id"] == "nexus"
        assert agent["role"] == "nexus"
        agents = store.list_agents()
        assert any(a["id"] == "nexus" for a in agents)

    def test_get_agent(self, store):
        store.register_agent("alistair", "alistair", "Alistair right-hand agent")
        agent = store.get_agent("alistair")
        assert agent["name"] == "Alistair right-hand agent"


class TestUsage:
    def test_record_and_summary(self, store):
        store.record_usage(agent_id="nexus", adapter="echo", model="gpt-4", kind="token", amount=1000, task_id="task-1")
        store.record_usage(agent_id="nexus", adapter="echo", model="gpt-4", kind="token", amount=500, task_id="task-2")
        summary = store.usage_summary()
        assert summary["total"] == 1500
        assert summary["kinds"]["token"]["amount"] == 1500
