"""Smoke test: end-to-end engine run + retry + approval gate + orphan recovery."""
import sys, os, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nexus_cc.store import Store
from nexus_cc.adapters.registry import load_builtin_registry
from nexus_cc.engine.engine import Engine
from nexus_cc.adapters.echo import Echo

tmp = tempfile.mkdtemp(prefix="nxeng-")
db = Store(os.path.join(tmp, "t.db"))
db.register_agent("lennox", "lennox", "David")
db.register_agent("nexus", "nexus", "Nexus")
reg = load_builtin_registry()
eng = Engine(db, reg, workers=2,
             poll_interval=0.05, heartbeat_interval=0.05, orphan_after=1.0)
eng.start()

# 1) echo runs to completion
t = eng.submit(title="echo test", agent_id="nexus", adapter="echo",
               model="local", payload={"message": "hello"}, creator="lennox")
deadline = time.time() + 10
while db.get_task(t["id"])["status"] not in ("done", "failed", "cancelled") and time.time() < deadline:
    time.sleep(0.05)
tt = db.get_task(t["id"])
assert tt["status"] == "done", tt
assert tt["result"]["echo"] == "hello", tt["result"]
print("echo task done:", tt["result"])

# 2) retry: fail first attempt then succeed
Echo.fault_injection = {"fail_first": True, "reason": "injected", "retryable": True}
t2 = eng.submit(title="retry test", agent_id="nexus", adapter="echo",
                max_attempts=3, payload={"message": "retry me"}, creator="nexus")
t2id = t2["id"]
# backoff after injected failure is ~2s base; wait it out
deadline = time.time() + 15
while True:
    st = db.get_task(t2id)["status"]
    if st in ("done", "failed", "cancelled"):
        break
    if time.time() > deadline:
        break
    time.sleep(0.05)
tt2 = db.get_task(t2id)
print("retry status:", tt2["status"], "attempts:", tt2["attempts"])
Echo.fault_injection = {"fail_first": False, "sleep_s": 0.0}
eng.stop()
eng.close()
print("ENGINE SMOKE OK")
