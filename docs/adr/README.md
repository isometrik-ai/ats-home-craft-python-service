# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for **ats-home-craft-python-service**.

ADRs capture significant design choices, the context behind them, and their consequences. They complement detailed schema docs in `ats-home-craft-supabase/docs/`.

| ADR                                                  | Title                                                 | Status             |
| ---------------------------------------------------- | ----------------------------------------------------- | ------------------ |
| [0001](./0001-resident-onboarding.md)                | Resident onboarding uses `contacts` + junction tables | Accepted           |
| [0002](./0002-resident-onboarding-implementation.md) | Resident onboarding — implementation plan             | Accepted           |
| [0003](./0003-visitor-passes.md)                     | Visitor passes — schema and backend model             | Accepted (Phase 1) |
| [0004](./0004-pass-validation-gate.md)               | Pass validation — gate check-in/out and visitor logs  | Proposed           |

## Format

Each ADR follows:

1. **Status** — Proposed, Accepted, Deprecated, Superseded
1. **Context** — Problem and constraints
1. **Decision** — What we chose
1. **Consequences** — Positive, negative, and follow-ups

## Adding a new ADR

1. Copy the next number (`0002`, `0003`, …).
1. Add a row to the table above.
1. Link related migrations and schema docs.
