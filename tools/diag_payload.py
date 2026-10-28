"""Diagnostic: where does payload get lost in create_task?"""
import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nexus_cc.store import Store
from nexus_cc.adapters.registry import load_builtin_registry
import json

tmp = tempfile.mkdtemp(prefix="nxdiag-")
db = Store(os.path.join(tmp, "t.db"))
db.register_agent("lennox", "lennox", "David")
db.register_agent("nexus", "nexus", "Nexus")

# Test create_task directly with payload
from nexus_cc.store.db import _utcnow
p = {"message": "hello"}
print("input payload:", p)

task = db.create_task(
    title="diag", agent_id="nexus", adapter="echo",
    payload=p, creator="lennox"
)
print("task after create_task:")
print("  status:", task["status"])
print("  payload:", task["payload"])
print("  payload repr:", repr(task["payload"]))

# Now check raw DB
conn = db._conn
row = conn.execute("SELECT id, payload FROM tasks WHERE id=?", (task["id"],)).fetchone()
print("raw DB payload col:", repr(row["payload"]))

# Check adapter validation works
reg = load_builtin_registry()
Echo = reg.resolve("echo")
norm = Echo.validate_input(p)
print("validated payload:", norm)

print("DIAG done")
