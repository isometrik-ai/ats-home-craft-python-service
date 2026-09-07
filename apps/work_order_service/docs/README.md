# Work Order Service — Documentation

> **Service:** `apps/work_order_service` (port **5001**)
> **Status:** V1 implemented

All Work Order Management documentation lives in this folder. Top-level index: [docs/README.md](../../../docs/README.md).

______________________________________________________________________

## Start here

| Document                                         | Audience            | Purpose                                   |
| ------------------------------------------------ | ------------------- | ----------------------------------------- |
| [overview.md](./overview.md)                     | Engineering team    | High-level summary, gaps, timeline        |
| [integration-review.md](./integration-review.md) | Integrators / leads | Prototype review, API mapping, checklists |
| [README.md](./README.md)                         | Backend developers  | Flow index, quick reference, file map     |

______________________________________________________________________

## Flow guides (Context & Change Guide)

| Flow                              | Guide                                                        | ADR                                                 |
| --------------------------------- | ------------------------------------------------------------ | --------------------------------------------------- |
| Assets, categories, custom fields | [assets-flow.md](./assets-flow.md)                           | [0002](./adr/0002-assets-and-custom-fields.md)      |
| AMC contracts & scheduler         | [contracts-scheduler-flow.md](./contracts-scheduler-flow.md) | [0003](./adr/0003-contracts-and-scheduler.md)       |
| Work orders                       | [work-orders-flow.md](./work-orders-flow.md)                 | [0004](./adr/0004-work-orders-and-vendor-portal.md) |
| Vendor portal                     | [vendor-portal-flow.md](./vendor-portal-flow.md)             | [0004](./adr/0004-work-orders-and-vendor-portal.md) |
| Invoices & payments               | [invoices-payments-flow.md](./invoices-payments-flow.md)     | [0005](./adr/0005-invoices-and-payments.md)         |
| Audit, webhooks, API keys         | [integrations-flow.md](./integrations-flow.md)               | [0006](./adr/0006-integrations.md)                  |

Service-wide architecture: [adr/0001-work-order-service.md](./adr/0001-work-order-service.md)

Full ADR index: [adr/README.md](./adr/README.md)

______________________________________________________________________

## External references

| Doc                | Location                                                                                                                                                                                                  |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DB schema          | [work-order-management-schema.md](../../../../ats-home-craft-supabase/docs/work-order-management-schema.md)                                                                                               |
| Platform ADRs      | [ADR 0011](../../user_service/docs/adr/0011-project-membership.md), [ADR 0009](../../user_service/docs/adr/0009-push-notifications-grpc.md) · [top-level user_service doc](../../../docs/user-service.md) |
| Project membership | [ADR 0011](../../user_service/docs/adr/0011-project-membership.md)                                                                                                                                        |
| Push notifications | [ADR 0009](../../user_service/docs/adr/0009-push-notifications-grpc.md)                                                                                                                                   |

______________________________________________________________________

## Quick reference

| Item            | Value                                                                   |
| --------------- | ----------------------------------------------------------------------- |
| Staff API       | `/v1/projects/{project_id}/...` (JWT + `work_order_management.*`)       |
| Vendor API      | `/v1/vendor/...` (`X-Vendor-Token`)                                     |
| Postgres schema | `work_order`                                                            |
| Run locally     | `uvicorn apps.work_order_service.app.main:app --port 5001 --reload`     |
| Tests           | `ENVIRONMENT=test PYTHONPATH=. pytest apps/work_order_service/tests -q` |

See [../README.md](../README.md) for Docker.
