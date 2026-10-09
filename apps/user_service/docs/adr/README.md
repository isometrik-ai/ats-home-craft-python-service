# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) for **`user_service`** (`apps/user_service`).

ADRs capture significant design choices, the context behind them, and their consequences. They complement detailed schema docs in `ats-home-craft-supabase/docs/`.

| ADR                                                  | Title                                                 | Status             |
| ---------------------------------------------------- | ----------------------------------------------------- | ------------------ |
| [0001](./0001-resident-onboarding.md)                | Resident onboarding uses `contacts` + junction tables | Accepted           |
| [0002](./0002-resident-onboarding-implementation.md) | Resident onboarding — implementation plan             | Accepted           |
| [0003](./0003-visitor-passes.md)                     | Visitor passes — schema and backend model             | Accepted (Phase 1) |
| [0004](./0004-pass-validation-gate.md)               | Pass validation — gate check-in/out and visitor logs  | Proposed           |
| [0005](./0005-move-events.md)                        | Move events — move-in / move-out records              | Accepted           |
| [0006](./0006-tenant-requests.md)                    | Tenant requests — owner submit, admin review          | Accepted (Phase 1) |
| [0007](./0007-walk-in-entries.md)                    | Walk-in entries — security request, resident approval | Accepted (Phase 1) |
| [0008](./0008-push-notifications-grpc.md)            | Push notifications via notification-service gRPC      | Accepted           |
| [0009](./0009-contact-roles.md)                      | Contact roles — unit-scoped role history              | Accepted           |
| [0010](./0010-project-membership.md)                 | Project membership — org layer + project layer        | Proposed           |
| [0011](./0011-notice-board.md)                       | Notice board — admin publish, resident feed           | Proposed           |
| [0012](./0012-daily-help.md)                         | Daily Help — project registry, gate pass linkage      | Proposed           |
| [0013](./0013-community-events.md)                   | Community events — admin create, resident book        | Accepted           |
| [0014](./0014-project-level-rbac.md)                 | Project-level RBAC — per-project roles                | Proposed           |
| [0015](./0015-pets.md)                               | Household pets — unit profiles, static catalog        | Accepted           |
| [0016](./0016-facility-booking.md)                   | Facility booking — configs, ledger, wallets, invoices | Accepted           |
| [0017](./0017-fee-configuration.md)                  | Fee configuration — three seeded fee heads, dunning   | Proposed           |
| [0018](./0018-buy-and-sell.md)                       | Buy & sell — resident classifieds inside a society    | Proposed           |

See also: [0010-membership-architecture.md](../0010-membership-architecture.md) (full guide) and [membership-schema.md](../../../../../ats-home-craft-supabase/docs/membership-schema.md) (DB reference). Notice board: [0011-notice-board-flow.md](../0011-notice-board-flow.md), [notice-board-schema.md](../../../../../ats-home-craft-supabase/docs/notice-board-schema.md). Daily help: [0012-daily-help-flow.md](../0012-daily-help-flow.md). Community events: [0013-events-flow.md](../0013-events-flow.md), [community-events-schema.md](../../../../../ats-home-craft-supabase/docs/community-events-schema.md). Pets: [0015-pets-flow.md](../0015-pets-flow.md). Facility booking: [0016-facility-booking-flow.md](../0016-facility-booking-flow.md). Fee configuration: [0017-fee-configuration-flow.md](../0017-fee-configuration-flow.md). Buy & sell: [0018-buy-and-sell-flow.md](../0018-buy-and-sell-flow.md).

## Format

Each ADR follows:

1. **Status** — Proposed, Accepted, Deprecated, Superseded
1. **Context** — Problem and constraints
1. **Decision** — What we chose
1. **Consequences** — Positive, negative, and follow-ups

## Adding a new ADR

1. Copy the next number (`0019`, …).
1. Add a row to the table above.
1. Link related migrations and schema docs.
