# Architecture Decision Records — Work Order Service

ADRs for **`apps/work_order_service`**. Each record covers one flow domain: context, decision, and consequences.

Platform ADRs this module depends on (monorepo root):

- [ADR 0011 — Project membership](../../user_service/docs/adr/0011-project-membership.md)
- [ADR 0009 — Push notifications gRPC](../../user_service/docs/adr/0009-push-notifications-grpc.md)

WOM ADRs live **only** in this folder (`apps/work_order_service/docs/adr/`).

| ADR                                             | Title                                                  | Status        |
| ----------------------------------------------- | ------------------------------------------------------ | ------------- |
| [0001](./0001-work-order-service.md)            | Separate service, `work_order` schema, RBAC            | Accepted (V1) |
| [0002](./0002-assets-and-custom-fields.md)      | Assets, categories, CRM custom field reuse             | Accepted (V1) |
| [0003](./0003-contracts-and-scheduler.md)       | AMC contracts, lazy scheduler, advisory lock           | Accepted (V1) |
| [0004](./0004-work-orders-and-vendor-portal.md) | Work order lifecycle, vendor token portal              | Accepted (V1) |
| [0005](./0005-invoices-and-payments.md)         | Vendor invoices, payments, R2 uploads                  | Accepted (V1) |
| [0006](./0006-integrations.md)                  | Audit, webhooks, API keys, push (deferred: public API) | Accepted (V1) |

## Related documentation

| Doc                                                                                                            | Purpose                              |
| -------------------------------------------------------------------------------------------------------------- | ------------------------------------ |
| [../overview.md](../overview.md)                                                                               | Team summary                         |
| [../integration-review.md](../integration-review.md)                                                           | Prototype review & integration guide |
| [../README.md](../README.md)                                                                                   | Flow index + quick reference         |
| [work-order-management-schema.md](../../../../../ats-home-craft-supabase/docs/work-order-management-schema.md) | DB reference                         |

## Format

1. **Status** — Proposed, Accepted, Deprecated, Superseded
1. **Context** — Problem and constraints
1. **Decision** — What we chose
1. **Consequences** — Positive, negative, follow-ups

## Adding a new ADR

1. Use the next number (`0007`, …).
1. Add a row to the table above.
1. Link the matching flow doc in `../`.
