# NEXUS Command Center — Architecture (Design Authority)

**Status:** v0 design authority for the first functional vertical slice.
**Date:** 2026-10-28
**Environment scope:** local Windows workstation, standard-library Python 3.13,
SQLite (WAL), pytest. Server-side integration is **designed but not deployed** —
see [INTEGRATION.md](INTEGRATION.md).

This document is the primary design authority. Where prose and code disagree, code
is evidence but this document defines intent; fix the disagreement, do not paper
over it.

---

## 1. Identity model (preserved from `~/knights-training`)

Three roles are hard boundaries. No single implementation may blur them.

| Role | Nature | Authority |
|---|---|---|
| **LENNOX** | Human commander (David) | Final approval authority. Not an execution worker. |
| **ALISTAIR** | Owner's right-hand agent | Investigates: observe → evidence → correlate → brief → hand off. Cannot execute high-impact engineering. |
| **NEXUS** | Execution specialist | Performs authorized bounded engineering. Cannot grant its own approvals. |

**Invariants:**
- No agent may bypass an approval gate. Approval is a durable, traceable,
  signed-in-audit action by an authorized actor (`lennox` role), bound to an exact
  task/scope, distinct from mere acknowledgment.
- ALISTAIR's handoff is *context*, never *proof* (per NEXUS.md: "Use Alistair's
  compact handoff as a head start, not proof").
- Capability ≠ decision authority. The engine never classifies an action as
  `green` merely because it *could* execute it.

## 2. System boundaries

```text
┌─────────────────────────────── LOCAL WORKSTATION (this repo) ───────────────────────────────┐
│                                                                                             │
│  Web UI (dark ops console) ── HTTP ─▶ HTTP server ─▶ Authorizer (roles / CSRF / secrets)    │
│                                                │                                            │
│                                                ▼                                            │
│  Engine: durable task orchestration, approval gates, scheduling, retries, heartbeats       │
│      │                                                                                      │
│      ▼                                                                                      │
│  Store: SQLite (WAL, single-writer, PRAGMA foreign_keys) — schemas up                   │
│      │                                                                                      │
│      ▼                                                                                      │
│  Worker pool  ─ tool permission boundary ─▶ Adapter interface ─▶ integration adapters      │
│                                                                                             │
│  (seeded demo adapters: artifact builder, inspector, echo — all local, sandboxed)           │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

External agent runtimes (Hermes, Nexus OpenCode :4098, opencode-web :4096, 9Router
provider) are **integrations over a documented adapter contract**, implemented in
Phase 6 with explicit authorization only. The engine is runtime-agnostic.

## 3. Durable task state machine

Every task transitions through an explicit, validated state graph. Transitions are
**atomic compare-and-swap** on the persisted row (single SQLite UPDATE with a
guarded `WHERE`), preventing duplicate execution and lost transitions.

```text
              requests (post/retry) ── idempotency key ─▶ returns existing task
                                     │
                                     ▼
    ┌────────────┐   submit   ┌──────────────┐  deps done   ┌──────────┐  claim     ┌─────────┐
    │  CREATED   │───────────▶│   QUEUED     │──────────────▶│ RUNNING  │───────────▶│ DONE    │
    └────────────┘            └──────────────┘               └─────────┘            └─────────┘
         │ cancel/                     │ cancel/                    │ failed/         (or FAILED)
         │ retry-exhausted             │ retry-exhausted             │ timeout/        (or CANCELLED)
         ▼                             ▼                             ▼ error
   ┌────────────┐               ┌────────────┐               ┌────────────┐
   │ CANCELLED  │               │ CANCELLED  │               │  FAILED    │
   └────────────┘               └────────────┘               └────────────┘
   (terminal)                    (terminal)                    (retryable → QUEUED with backoff)
```

- **Idempotency:** a caller-supplied `idempotency_key` is unique per namespace. A
  retried submission returns the existing task (200 with `deduplicated`, or a
  `409`-style distinct response), never a second task.
- **Dependencies:** a task is only eligible to run when all declared parents are
  in a successful terminal state (`DONE`); a failed parent blocks the child.
- **Approval gates:** a task flagged `requires_approval` stops before execution.
  It enters `QUEUED → awaiting approval` if the `approval_mode` value marks it, or
  a distinct `WAITING_APPROVAL` state (see `store` state enum). The engine never
  auto-executes it: an authorized approver must persist an explicit approval
  bound to the task ID + fingerprint before any worker may claim it.
- **Retry:** `attempts` and `max_attempts` are persisted. A failure within budget
  returns the task to `QUEUED` with exponential backoff (`next_attempt_at`).
- **Heartbeats:** a running worker persists `heartbeat_at` every interval. An
  orphaned claim (heartbeat older than `orphan_after`) is recoverable: the worker's
  claimed run is marked interrupted, the task is returned to `QUEUED` when within
  retry budget, respecting idempotency of the *run*.
- **Cancellation semantics:** cancel is only honored from a non-terminal state.
  If a worker is currently running, cancel sets a `cancel_requested` flag; the
  worker checks the flag at boundary points and self-aborts. The terminal `CANCELLED`
  transition is atomic and cannot be reversed ("late worker cannot change a
  terminal state").

## 4. Idempotency and duplicate-execution prevention (the core guarantee)

The engine guarantees **at most one successful execution per run**:

1. A run is claimed atomically: `UPDATE tasks SET state='RUNNING', run_token=?, ...`
   guarded by `WHERE state='QUEUED' AND run_token IS NULL`. Only the claimant
   holding the token may write the terminal result.
2. On process restart, a claimed-but-never-finished run has its outcome treated as
   *indeterminate*: the transport effect is unknown, so the task is recoverable only
   through the operator-approved retry policy, and the audit records the
   interruption explicitly rather than pretending it never happened.
3. Every prepare/claim/complete/cancel is a single guarded statement, not
   read-then-write.

## 5. Worker model

- Fixed bounded worker pool (configurable, default 4) — never unbounded threads.
- Each worker loop: poll eligible tasks (`state=QUEUED`, deps satisfied,
  `next_attempt_at <= now`, no approval pending) → claim → dispatch to the resolved
  adapter → validate structured result → persist terminal outcome.
- Global concurrency ceiling per adapter class (defense in depth against a single
  provider cluster).
- Structured execution logs (machine-readable) + human audit entries persisted to
  SQLite, never fabricated.

## 6. Tool permission boundaries

- Adapters are registered in a fixed **registry**, not arbitrary functions.
- Each adapter declares a capability set and an optional `requires_approval` policy.
- If a task's tool usage exceeds its approval scope, execution is refused at the
  boundary even if a worker is available.
- Adapters run with the same OS user and explicit sandbox rules; the boundary is
  **policy-enforced**, matching the honest claim in the DCC audit: application
  guarantee, not OS capability isolation.
- **Prompt-injection defense:** adapter output is treated as *data*. Structured
  verification parses strictly; free-text fields are length-bounded and rendered
  escaped; no output content ever changes routing, permissions, or approval
  decisions.

## 7. Observability

- Every page is backed by real, persisted, queryable state. There is **no fake
  operational data**. Demo adapters produce real artifacts from real (synthetic)
  inputs and are labeled `SIMULATED` provenance.
- One consistent envelope separates **transport health** from **fact freshness**
  (per the DCC audit's most frequent critique).
- Lifecycle: live health endpoint; operational readiness reports store reachability.

## 8. Phase mapping

| Phase | Delivered in this slice |
|---|---|
| 1 Discovery | Done — see `docs/DISCOVERY.md` |
| 2 Control plane | This architecture + `store` (registry, tasks, approval, audit, usage) |
| 3 Command Center UI | 10-page dark ops console over the real engine (static SPA, no build step) |
| 4 Execution engine | Worker pool, state transitions, retries, heartbeats, idempotency, recovery |
| 5 Security | Auth/roles, CSRF, validation, approval gates, injection-safe rendering, audit-only tokens |
| 6 Integration | Adapter interface contract + 3 local adapters; external adapters are stubs awaiting authorization |
| 7 Testing | pytest suite: idempotency, crashes, retry, cancellation races, auth, recovery |
