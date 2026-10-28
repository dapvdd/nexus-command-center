# Integration Contract

**NEXUS Command Center — integration contract for the DavidLab agent ecosystem.**

Version: 0.1.0 (vertical slice)
Decides how external runtimes (Hermes gateway, nexus-opencode, 9Router) will plug
into the control plane **without** this slice wiring or touching them. External
integration is Phase 6; this document is the contract those adapters must satisfy.

---

## 1. Design fences (non-negotiable)

| Fence | Rule |
|---|---|
| Identity | LENNOX = human, final authority. ALISTAIR = investigator, hands off to NEXUS. NEXUS = execution specialist. No blurring. |
| Durable state | The SQLite store is the **only** owner of durable state. The engine holds no state. |
| Exactly-once | A claimed run may complete exactly once (run-token guarded terminal transition). Retried HTTP requests are deduplicated by `idempotency_key` + `parent_id`. |
| Provenance | Every result from builtin adapters is labeled `provenance: SIMULATED`. Nothing fake is ever presented as live telemetry. |
| Safety | Approval-gated tasks never auto-run. `ArtifactBuilder` refuses paths outside its private workspace. No privileged/remote operations from this app. |
| Local-first | External runtimes are adapters *behind* the interface, never called directly by the control plane. |

## 2. Runtime contract (adapters)

Every integration is an `Adapter` implementing `nexus_cc.adapters.base.Adapter`:

| Member | Contract |
|---|---|
| `name` | stable string id (`[a-z0-9_-]+`), bound to a task's `adapter` column |
| `validate_input(payload) -> dict` | synchronous, raises `AdapterError` (or returns sanitized payload); runs at submit time |
| `run(ctx: TaskContext) -> AdapterResult` | executes the unit of work; must be **bounded** (honor `ctx.is_cancelled()`, respect `timeout_s`) |
| `AdapterResult` | `{ok, data, usage, kind}` — `data` is the durable result; `usage` feeds analytics |
| `default_requires_approval` | if True, `approval_mode="auto"` requires LENNOX approval before the engine will run it |
| `capabilities` / `supported_models` | advertised to the Model Routing page; routing policy is store/engine-driven, not adapter-driven |

### Lifecycle the engine guarantees per run
```
queued (or waiting_approval -> approved -> queued)
  -> running  (claim_task: unique run_token, heartbeat thread started)
  -> done    |  failed (retryable backoff within max_attempts)  |  cancelled
```
- `claim_task` is atomic (guarded UPDATE). A worker that fails mid-run loses its token;
  the task is reclaimed or orphan-recovered after `orphan_after` seconds.
- `finish_task(success=...)` is the single terminal transition; exactly one worker owns it.
- Heartbeats (`heartbeat_at`) prove liveness; stale `running` tasks are reconciled
  (`recover_orphans`).

## 3. Adapter interface (builtins)

| Adapter | Capabilities | Approval | What it does | Provenance |
|---|---|---|---|---|
| `echo` | `echo` | none | deterministic round-trip (testing routing/retry/usage) | SIMULATED |
| `inspector` | `observe` | none | read-only symmetric inspection of synthetic input (ALISTAIR role) | SIMULATED |
| `artifact` | `artifact:write` | required | writes a bounded `.md` artifact into the engine workspace + sha256 | SIMULATED |
| `bomb` | `test:fault` | none | deterministic non-retryable failure (test-only, **not** in default registry) | SIMULATED |

Summary of engine-usage attribution: `record_usage(agent_id, adapter, model, kind, amount, task_id)`.
External adapters report their own `usage` dict in `AdapterResult`.

## 4. HTTP API contract

Base: `/api` · Every response is `{"ok": true, "data": ...}` or
`{"ok": false, "error": ..., "code": ...}`. Errors map `NexusError.status`.

| Method | Path | Role intent | Notes |
|---|---|---|---|
| GET | `/api/health` | store | real write-then-read check + latency |
| POST | `/api/tasks` | lennox/alistair | idempotent by `idempotency_key`+`parent_id`; returns existing task on duplicate |
| GET | `/api/tasks` | all | filters `status`, `agent_id`, `only_open`, pagination |
| GET | `/api/tasks/{id}` | all | full task row (payload, result, run_token, attempts) |
| POST | `/api/tasks/{id}/cancel` | lennox | guarded (queued→cancelled; running→cancel_requested) |
| POST | `/api/tasks/{id}/approve` | lennox | scope must match; releases waiting_approval → queued |
| POST | `/api/tasks/{id}/reject` | lennox | terminal |
| POST | `/api/tasks/{id}/retry` | lennox | failed/cancelled → queued (attempt budget reset on manual retry) |
| GET | `/api/agents` · POST `/api/agents` | all / lennox | identity registry |
| GET | `/api/agents/{id}` | all | agent detail |
| GET | `/api/adapters` | all | builtin adapter catalogue for Model Routing |
| GET | `/api/roles` | all | fixed LENNOX/ALISTAIR/NEXUS identity map for the UI |
| GET | `/api/audit` | all | immutable trail |
| GET | `/api/logs` | all | engine log stream |
| GET | `/api/usage` | all | `summary` (kinds, total) + `by_model` |
| GET | `/api/engine` | all | live engine state + counts |
| POST | `/api/engine/start` `/api/engine/stop` `/api/engine/recover` | lennox | operator controls |

Static: `/` → `index.html`, `/app.js` → SPA (also served under `/static/`).

## 5. Task submission contract

```json
POST /api/tasks
{
  "title": "...", "objective": "...", "acceptance": "...", "scope": "...",
  "forbidden": "...", "agent_id": "alistair|nexus", "adapter": "echo|inspector|artifact",
  "model": "local", "priority": 500, "timeout_s": 300, "max_attempts": 3,
  "approval_mode": "none|required|review", "idempotency_key": "unique",
  "payload": { "message": "hi" }
}
```
- Omitted fields get engine defaults.
- When `approval_mode="auto"`, resolve from the adapter's `default_requires_approval`.
- Adapter `validate_input` runs **before** the durable insert; a validation failure
  returns 400 and creates nothing.

## 6. Idempotency contract

- `(parent_id, idempotency_key)` is `UNIQUE`. Re-submission returns the existing row
  with `deduplicated: true` — never a second task, never double execution.
- A `run` is executed once because the terminal transition requires the unique
  `run_token` minted at claim time.
- Retried `POST`s (network retry) are therefore safe.

## 7. Security contract (what this app will/won't do)

- **Will**: validate & sanitize inputs (length caps, safe filenames, bounded results).
- **Will**: refuse escaping the engine workspace; require approval for impact actions.
- **Will**: record every control-plane decision in `audit`.
- **Won't**: ever perform privileged/remote DavidLab operations, deploy/restart
  services, or call paid APIs. External integration must be explicitly approved
  (Phase 6 gate).
- **Won't**: present SIMULATED provenance as live data.

## 8. Phase 6 — external adapter stubs (documented, not wired)

Discovered runtimes (see `docs/DISCOVERY.md`) are candidates for adapters behind the
same interface:

| Runtime | Endpoint (discovered) | Suggested adapter name | Role constraint |
|---|---|---|---|
| Hermes gateway | `:4097` armor | `hermes` | read-only inspect first |
| nexus-opencode | `:4098` | `opencode-exec` | execution, approval default required |
| 9Router provider | `tuf-f15-david…ts.net:20128/v1` | `router-llm` | external model calls, budget+approval |
| opencode-web | `:4096` | (UI only) | no direct task use |

Wiring these is **blocked by the approval gate**: no deployment or paid usage without
explicit LENNOX approval, and nothing that mutates DavidLab is reachable from this app.
