# NEXUS Command Center — Discovery Report

## Identity / prior work

- `~/knights-training` on david-lab is the authoritative source of the identity
  model: `NEXUS.md` (Nexus frontline engineering doctrine), `ALISTAIR.append.md`
  (Alistair frontline investigator/sentinel), `scenarios.json` (8 training
  scenarios). These **are implemented** and additive; this project preserves them
  without overwriting.
- `RESULTS.md` records TRAINING PARTIAL: Alistair live-profile integration BLOCKED
  by `0700` profile access; Nexus timeout at 180s; end-to-end handoff NOT verified.
  This project does **not** claim those as working integration.

## Agent runtimes (SSH-verified on david-lab, 2026-10-28)

| Runtime | Evidence | State |
|---|---|---|
| Hermes gateway | `ps` user `hermes`, `/opt/hermes-native`, HERMES_HOME `~/.hermes` (0700) | running |
| Nexus OpenCode controller | `nexus-opencode.service`, `opencode serve :4098`, armor profile, UMask 0077 | active |
| Nexus Telegram API gate | `nexus-gateway.service`, `gateway.mjs :4097` | active |
| OpenCode Web | `opencode-web.service :4096` (v1.19.0) | active |
| Alistair health timer | hardened user timer (per ecosystem audit) | present |

## Model providers

- 9Router on `tuf-f15-david...ts.net:20128/v1`; models `oc/big-pickle`,
  `oc/longcat-2.5-preview-free`, `oc/step-5-preview-free`, `cx/gpt-6.1-sol`
  (from nexus `opencode.json`). Local workstation uses TipRouter
  `deepseek-v-4.1-flash` and free 9router models.
- **No paid usage; no provider calls made from this slice.**

## Existing orchestration/control-plane adjacent

- `davidlab-control-center` (DCC): Directorate missions + Unix bridge. Audited as
  **not race-safe**, no durable approval grant, timed-out daemon workers continue.
  Legacy; this project does not modify it.
- `breakwater/`: durable edge custody, exact-batch handoff, fault injection —
  a reference for idempotency rigor, not reused code.
- `atlas/`: approval-gated local collectors — reference for honest provenance.

## Databases / logging

- SQLite throughout (WAL cultures). DCC events/audit SQLite; this project uses one
  WAL SQLite store. No central log aggregator; this project persists structured
  logs in the same store + rotating file logs.

## Decision

Greenfield local application in `C:\Users\user\nexus-command-center`. External
runtimes are **documented adapters, not wired** until explicit authorization, per
Execution Rules and `davidlab-remote-ops` (investigation ≠ authorization).

Nothing was created, modified, or deployed on david-lab during this phase.
