"""Smoke test: store state machine, idempotency, atomic claims."""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nexus_cc.store import Store
from nexus_cc import errors as E

tmp = tempfile.mkdtemp(prefix="nxsmoke-")
db = Store(os.path.join(tmp, "t.db"))

# register agents
lennox = db.register_agent("lennox", "lennox", "David")
alistair = db.register_agent("alistair", "alistair", "Alistair Investigator")
nexus = db.register_agent("nexus", "nexus", "Nexus Engineer")

# idempotent create
t1 = db.create_task(title="build widget", agent_id="nexus",
                    idempotency_key="k1", creator="lennox")
t1b = db.create_task(title="build widget", agent_id="nexus",
                     idempotency_key="k1", creator="lennox")
assert t1["id"] == t1b["id"], "idempotency failed"
assert t1["status"] == "queued"

# claim once atomically
tok1 = "tok1"; tok2 = "tok2"
assert db.claim_task(t1["id"], tok1) is True
assert db.claim_task(t1["id"], tok2) is False, "double claim allowed"
t = db.get_task(t1["id"])
assert t["status"] == "running" and t["attempts"] == 1

# heartbeat works for owner, not for stranger
assert db.heartbeat(t1["id"], tok1) is True
assert db.heartbeat(t1["id"], tok2) is False

# finish as owner -> done
assert db.finish_task(t1["id"], tok1, success=True, result={"ok": True}) is True
t = db.get_task(t1["id"])
assert t["status"] == "done"

# late finish after terminal is refused
assert db.finish_task(t1["id"], tok2, success=False, error="late") is False
assert db.get_task(t1["id"])["status"] == "done", "late worker changed terminal state"

# approval-gated task: n
t2 = db.create_task(title="high impact", agent_id="nexus",
                    approval_mode="required", scope="refactor", creator="alistair")
assert t2["status"] == "waiting_approval"
try:
    db.claim_task(t2["id"], "tok")
except AssertionError:
    pass
# engine would refuse claim: approval_status != approved
assert db.get_task(t2["id"])["status"] == "waiting_approval"

# wrong-scope approval refused
try:
    db.approve_task(t2["id"], approver="lennox", scope="other")
    raise SystemExit("wrong scope approved")
except E.ForbiddenError:
    pass

# correct approval then claim
db.approve_task(t2["id"], approver="lennox", scope="refactor")
t = db.get_task(t2["id"])
assert t["approval_status"] == "approved", t

# failed -> retry
t3 = db.create_task(title="flaky", agent_id="nexus", max_attempts=2, creator="nexus")
assert db.claim_task(t3["id"], "r1")
assert db.finish_task(t3["id"], "r1", success=False, error="boom")
assert db.get_task(t3["id"])["status"] == "failed"
db.retry_task(t3["id"], actor="lennox")
assert db.get_task(t3["id"])["status"] == "queued"

# cancel a queued task
t4 = db.create_task(title="cancel me", agent_id="nexus", creator="nexus")
db.request_cancel(t4["id"], actor="lennox", reason="nobody wants it")
assert db.get_task(t4["id"])["status"] == "cancelled"

# audit trail
audit = db.list_audit()
assert any(a["action"] == "task.created" for a in audit)
assert any(a["action"] == "task.approved" for a in audit)

# health is real write-then-read
h = db.health()
assert h["ok"] is True, h

print("SMOKE OK: idempotency, atomic claims, approval gate, retry, cancel, audit")
