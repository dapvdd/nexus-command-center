# NEXUS Command Center

Multi-agent command center for the DavidLab ecosystem.

**Agent identities:** LENNOX (human authority), ALISTAIR (coordination), NEXUS (execution).

## Quick Start

```bash
# Start the API server (serves dashboard at /)
python main.py

# Or with custom settings
NEXUS_DB=./nexus.db NEXUS_PORT=4820 python main.py
```

Server starts at `http://localhost:4820`. The dashboard is at `http://localhost:4820/`.

## Running Tests

```bash
# All tests
python -m pytest tests/ -v

# Store tests only
python -m pytest tests/test_store.py -v

# API tests only
python -m pytest tests/test_api.py -v
```

**Results: 51/51 tests pass.**

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Store reachability |
| POST | `/api/tasks` | Create task (idempotent) |
| GET | `/api/tasks` | List tasks |
| GET | `/api/tasks/{id}` | Get task detail |
| POST | `/api/tasks/{id}/cancel` | Cancel task |
| POST | `/api/tasks/{id}/approve` | Approve task |
| POST | `/api/tasks/{id}/reject` | Reject task |
| POST | `/api/tasks/{id}/retry` | Retry task |
| POST | `/api/agents` | Register agent |
| GET | `/api/agents` | List agents |
| GET | `/api/audit` | Audit log |

## Task Lifecycle States

`created` → `queued` → `running` → `done`
                  ↘ `failed` (retryable)
                  ↘ `cancelled`
         `waiting_approval` (approval gate before queuing)

## Idempotency

Submit with `idempotency_key` field — duplicate submissions return the existing task (HTTP 200, `deduplicated: true`), never a second task. Idempotency key is unique per parent task.

## Architecture

See `docs/ARCHITECTURE.md` for full design authority.

## Project Structure

```
nexus-command-center/
├── nexus_cc/
│   ├── __init__.py
│   ├── errors.py          # Exception types + task state constants
│   ├── store/
│   │   ├── __init__.py
│   │   └── db.py          # SQLite store (WAL, FK, atomic transitions)
│   └── httpd/
│       ├── __init__.py
│       ├── server.py      # HTTP API server
│       └── static/
│           └── index.html # Dashboard
├── tests/
│   ├── conftest.py        # Pytest fixtures
│   ├── test_store.py      # 35 store tests
│   └── test_api.py        # 16 API tests
├── main.py                # Entry point
└── pyproject.toml
```
