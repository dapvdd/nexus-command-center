# Deployment Plan

**NEXUS Command Center — local-first deployment plan.**

This app is deliberately local-only. It does **not** deploy, restart, or alter any
DavidLab service, and it does not call paid APIs. This plan covers running it as a
local host process (option A) and the gated path to running it on a DavidLab host
(option B), which requires explicit LENNOX approval and is out of scope for the
vertical slice.

---

## 1. Runtime prerequisites

| Item | Requirement |
|---|---|
| Python | 3.13+ |
| SQLite | 3.35+ (WAL, `STRICT`-compatible DDL) |
| OS | Windows / Linux / macOS (stdlib only; no npm, no network) |
| Port | 4820 (default) — bind `127.0.0.1`, never `0.0.0.0` by default |

No third-party Python packages are required. `pyproject.toml` declares only dev
deps (`pytest`).

## 2. Option A — local host process (the vertical slice)

```bash
git init && cd nexus-command-center        # or copy the tree
python -m pytest tests/ -q                  # 61 tests, all green
python main.py --seed --fresh                 # reset + seed identity + synthetic tasks
# UI/API: http://127.0.0.1:4820
```

| Flag | Meaning |
|---|---|
| `--fresh` | delete existing `state/nexus.db` first |
| `--seed` | register LENNOX/ALISTAIR/NEXUS + a few synthetic, idempotent tasks |
| `--db PATH` | custom SQLite path (default `state/nexus.db`) |
| `--workers N` | engine worker count (default 4) |
| `--host / --port` | bind address / port (default `127.0.0.1:4820`) |

Data layout: single SQLite file in WAL mode (`state/nexus.db` + `-wal`/`-shm`);
engine workspace at `state/workspace/` (artifact adapter output). Backing up is just
using SQLite's online backup API (or a consistent backup after a clean shutdown).
Copying only a live WAL-mode main `.db` file can omit committed data.

### Service supervision (local)
Use a process supervisor appropriate to the host (Windows: Task Scheduler / NSSM;
Linux: systemd). Configuration values map to `main.py` arguments.

Example `systemd` unit (illustrative, for a LENNOX-approved host):

```ini
[Unit]
Description=NEXUS Command Center
After=network.target

[Service]
WorkingDirectory=/opt/nexus-command-center
ExecStart=/usr/bin/python3 /opt/nexus-command-center/main.py --host 127.0.0.1 --port 4820
Restart=on-failure
User=nexus

[Install]
WantedBy=multi-user.target
```

## 3. Option B — DavidLab host (gated, NOT part of this slice)

Explicitly **blocked until LENNOX approval** (per the conversation decision: no
deployment/alter/privileged ops/paid usage without approval). When approved, the steps
are:

1. Copy tree to a dedicated, non-system directory (e.g. `/home/david/nexus-cc`).
2. Runtime: `python3 -m venv .venv` → never touch system site-packages.
3. Bind `127.0.0.1:4820`; put a read-only reverse proxy in front if external UI is
   required (TLS at the proxy; the app itself is plaintext-local).
4. Wire Phase 6 external adapters **only** after the approval gate is satisfied and a
   budget cap + audit trail is in place (they currently do not exist).
5. Any change to DavidLab services is performed only through the user's normal,
   separate, human-approved process — never from this application.

## 4. Recovery & operations

| Scenario | Behavior |
|---|---|
| App restart | Store reopens; claimed-but-stale tasks are recovered by the engine (`recover_orphans`); nothing is lost (WAL) |
| Worker crash mid-run | Token ownership lost; task requeued within `max_attempts`, with exponential backoff |
| Store disk full / lock | health check reports DEGRADED; engine workers back off and log |
| Duplicate submission | idempotency key → same task returned, no double execution |
| Cancellation race | atomic guarded transitions; exactly one terminal state wins |
| Adapter failure | retry (retryable) or fail (non-retryable); both audited |
| Approval gate | `waiting_approval` tasks are never auto-executed; scope must match |

## 5. Security hardening checklist

- [ ] Bind `127.0.0.1` only; never expose the control plane to a public interface.
- [ ] Review the audit log (`/api/audit`) periodically; every approve/cancel/retry is recorded.
- [ ] Backup `state/nexus.db` (plus `-wal` before the process is stopped cleanly).
- [ ] Keep `ArtifactBuilder.WORKSPACE_ROOT` private; never point it at a shared path.
- [ ] Do not enable external adapters until the Phase 6 approval gate passes.

## 6. Acceptance criteria (vertical slice)

- [x] Local app serves the 10-page dark ops console at `http://127.0.0.1:4820`.
- [x] Durable task state with atomic transitions, idempotent submission.
- [x] Approval-gated artifact flow verified end-to-end (scope match → queued → done → artifact written).
- [x] 61 automated tests pass (store, engine lifecycle, HTTP API).
- [x] SIMULATED provenance labeling on all builtin adapter output.
- [x] Architecture (`docs/ARCHITECTURE.md`), discovery (`docs/DISCOVERY.md`), contract
      (`docs/INTEGRATION.md`) — deployment plan (this file).
