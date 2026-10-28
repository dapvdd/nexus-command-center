# NEXUS Command Center

Multi-agent command center for the DavidLab ecosystem — a **local** dark-mode
operations console with a durable control plane over the LENNOX / ALISTAIR / NEXUS
agent identity model.

**Agent identities (fixed):**
- **LENNOX** — human commander, final authority (approves, decides).
- **ALISTAIR** — investigator; observes, evidences, hands off to NEXUS.
- **NEXUS** — execution specialist; performs authorized bounded work.

## Quick Start

```bash
python -m pytest tests/ -q     # 61 tests, all green
python main.py --seed          # start engine + HTTP API + 10-page console
# http://127.0.0.1:4820
```

| Flag | Meaning |
|---|---|
| `--fresh` | remove existing database first |
| `--seed` | register identity agents + a few synthetic tasks |
| `--db PATH` | custom database path (default `state/nexus.db`) |
| `--workers N` | engine worker count (default 4) |
| `--host / --port` | bind address / port (default `127.0.0.1:4820`) |

No third-party packages. No npm build (blocked on this host by design).

## The Console — 10 pages

Overview · Agent Registry · Mission Control · Task Queue · Execution Timeline ·
Model Routing · Usage Analytics · System Health · Audit Logs · Settings.
Every page renders **real** data from the store/engine — no fake operational data.
Builtin adapter output is labeled `SIMULATED` provenance.

## Testing

```bash
python -m pytest tests/                 # store + engine + API (61 tests)
python tools/smoke_store.py             # store-level smoke
python tools/smoke_engine.py            # engine end-to-end smoke
tools/diag_payload.py                   # payload round-trip diagnostic
```

Covered: idempotent submission, atomic claims, approval gates, retry/backoff,
cancellation races, orphan recovery, usage/audit recording, API + UI serving.

## API

Base `/api`, envelope `{"ok": true, "data": ...}`. See `docs/INTEGRATION.md` for the
full contract. Highlights: idempotent task create, guarded approve/reject/cancel/retry,
agent registry, adapters, engine status/start/stop/recover, audit, logs, usage.

## Task Lifecycle

```
queued → running → done          (or failed → retry-with-backoff → done)
waiting_approval → (LENNOX approve, scope match) → queued → running → done
cancel → cancelled (guarded)     ·   retry → queued
```

## Architecture & plans

- `docs/ARCHITECTURE.md` — design authority (state machine, idempotency, worker model).
- `docs/DISCOVERY.md` — DavidLab runtimes discovered (read-only), never mutated.
- `docs/INTEGRATION.md` — adapter + API contract; Phase 6 external adapters await approval.
- `docs/DEPLOYMENT.md` — local deploy + gated DavidLab host plan (not performed).

## Project Structure

```
nexus-command-center/
├── nexus_cc/
│   ├── errors.py            # state constants + error taxonomy
│   ├── store/db.py          # SQLite store (WAL, FK, atomic transitions)
│   ├── engine/engine.py     # worker pool, retries, heartbeats, recovery
│   ├── adapters/            # base + registry + echo/inspector/artifact/bomb(test)
│   └── httpd/               # server.py + static/ 10-page SPA (index.html, app.js)
├── tests/                   # store, engine lifecycle, API (61 tests)
├── tools/                   # smoke + diagnostics
├── docs/                    # architecture, discovery, integration, deployment
└── main.py                  # launcher + seed
```
