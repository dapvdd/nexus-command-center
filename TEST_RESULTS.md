# Test Results — NEXUS Command Center

**Executed:** `python -m pytest tests/ -q`
**Result: 61 passed, 0 failed**
**Platform:** Windows (Python 3.13.7, pytest 8.4.2, SQLite 3.50.4)

## Breakdown

| Suite | Tests | Result |
|---|---|---|
| `tests/test_store.py` | 34 | ✅ PASS |
| `tests/test_engine.py` | 5 | ✅ PASS |
| `tests/test_api.py` | 22 | ✅ PASS |
| **Total** | **61** | **✅ 61 passed** |

## Suites

### Store (`tests/test_store.py`) — 34 tests
Covers: WAL mode, health, task creation/idempotency/validation, retrieval,
atomic state transitions (claim/finish/heartbeat/cancel/retry), approval &
rejection (scope-match), agent registry, usage, audit, restart recovery.

### Engine (`tests/test_engine.py`) — 5 tests
Full engine lifecycle over a real temp DB:
- echo task reaches `done` with structured result + usage recorded
- approval gate blocks execution (attempts stay 0)
- approvocale → engine picks up and completes
- retry-after-injected-failure reaches `done` with attempts >= 2
- non-retryable failure stays `failed` with attempts == 1

### HTTP API (`tests/test_api.py`) — 22 tests
Live HTTP server (ephemeral port) exercising the real store. Covers task
create/idempotency/deduplication, list/filter/pagination/detail/404, cancel,
retry, agents, audit, health, roles, adapters, engine status, usage, engine
operator endpoints (start/stop/recover), root HTML, and root-level static
asset serving (`/app.js`).

## Smoke tools

| Tool | Result |
|---|---|
| `python tools/smoke_store.py` | ✅ SMOKE OK |
| `python tools/smoke_engine.py` | ✅ ENGINE SMOKE OK |
| `python tools/diag_payload.py` | ✅ payload round-trip |

## Notes

- Tests use ephemeral temp SQLite files (WAL enabled); no network access, no
  DavidLab services touched.
- `tokens/bomb` adapter is test-only and **not** in the default registry.
