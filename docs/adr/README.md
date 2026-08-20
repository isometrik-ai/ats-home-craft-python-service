# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for **ats-home-craft-python-service**.

ADRs capture significant design choices, the context behind them, and their consequences. They complement detailed schema docs in `ats-home-craft-supabase/docs/`.

| ADR                                                  | Title                                                 | Status             |
| ---------------------------------------------------- | ----------------------------------------------------- | ------------------ |
| [0001](./0001-resident-onboarding.md)                | Resident onboarding uses `contacts` + junction tables | Accepted           |
| [0002](./0002-resident-onboarding-implementation.md) | Resident onboarding — implementation plan             | Accepted           |
| [0003](./0003-visitor-passes.md)                     | Visitor passes — schema and backend model             | Accepted (Phase 1) |
| [0004](./0004-pass-validation-gate.md)               | Pass validation — gate check-in/out and visitor logs  | Proposed           |
| [0005](./0005-move-events.md)                        | Move events — move-in / move-out records              | Accepted           |
| [0006](./0006-project-fee-configuration.md)          | Project fee configuration — schema and backend model  | Accepted           |
| [0007](./0007-tenant-requests.md)                    | Tenant requests — owner submit, admin review          | Accepted (Phase 1) |
| [0008](./0008-walk-in-entries.md)                    | Walk-in entries — security request, resident approval | Accepted (Phase 1) |
| [0009](./0009-push-notifications-grpc.md)            | Push notifications via notification-service gRPC      | Accepted           |
| [0010](./0010-contact-roles.md)                      | Contact roles — unit-scoped role history              | Accepted           |
| [0011](./0011-project-membership.md)                 | Project membership — org layer + project layer        | Proposed           |
| [0012](./0012-notice-board.md)                       | Notice board — admin publish, resident feed           | Proposed           |
| [0013](./0013-daily-help.md)                         | Daily Help — project registry, gate pass linkage      | Proposed           |
| [0014](./0014-community-events.md)                   | Community events — admin create, resident book        | Accepted           |

See also: [membership-architecture.md](../membership-architecture.md) (full guide) and [membership-schema.md](../../../ats-home-craft-supabase/docs/membership-schema.md) (DB reference). Notice board: [notice-board-flow.md](../notice-board-flow.md), [notice-board-schema.md](../../../ats-home-craft-supabase/docs/notice-board-schema.md). Daily help: [daily-help-flow.md](../daily-help-flow.md). Community events: [events-flow.md](../events-flow.md), [community-events-schema.md](../../../ats-home-craft-supabase/docs/community-events-schema.md).

## Format

Each ADR follows:

1. **Status** — Proposed, Accepted, Deprecated, Superseded
1. **Context** — Problem and constraints
1. **Decision** — What we chose
1. **Consequences** — Positive, negative, and follow-ups

## Adding a new ADR

1. Copy the next number (`0007`, `0008`, …).
1. Add a row to the table above.
1. Link related migrations and schema docs.
