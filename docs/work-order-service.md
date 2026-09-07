# Work Order Service

> **Path:** `apps/work_order_service` · **Port:** 5001 · **Postgres schema:** `work_order`

Facility management — assets, asset categories, AMC contracts, scheduled work-order generation, vendor portal, invoices, and payments.

______________________________________________________________________

## Full documentation

All flow guides, integration review, and ADRs:

**[apps/work_order_service/docs/README.md](../apps/work_order_service/docs/README.md)**

Quick links:

| Topic             | Doc                                                                            |
| ----------------- | ------------------------------------------------------------------------------ |
| Team overview     | [overview.md](../apps/work_order_service/docs/overview.md)                     |
| Integration guide | [integration-review.md](../apps/work_order_service/docs/integration-review.md) |
| Work orders       | [work-orders-flow.md](../apps/work_order_service/docs/work-orders-flow.md)     |
| Vendor portal     | [vendor-portal-flow.md](../apps/work_order_service/docs/vendor-portal-flow.md) |
| ADRs              | [adr/README.md](../apps/work_order_service/docs/adr/README.md)                 |

______________________________________________________________________

## API surfaces

| Audience | Prefix                          | Auth                                        |
| -------- | ------------------------------- | ------------------------------------------- |
| Staff    | `/v1/projects/{project_id}/...` | JWT + `work_order_management.*` permissions |
| Vendor   | `/v1/vendor/...`                | `X-Vendor-Token` header                     |

______________________________________________________________________

## Run & test

```bash
uvicorn apps.work_order_service.app.main:app --host 0.0.0.0 --port 5001 --reload
ENVIRONMENT=test PYTHONPATH=. pytest apps/work_order_service/tests -q
```

See [apps/work_order_service/README.md](../apps/work_order_service/README.md) for Docker.

______________________________________________________________________

## Platform dependencies

WOM relies on [user_service](./user-service.md) for staff permission checks, custom fields on assets, and presigned URL schemas. Platform ADRs:

- [ADR 0011 — Project membership](../apps/user_service/docs/adr/0011-project-membership.md)
- [ADR 0009 — Push notifications](../apps/user_service/docs/adr/0009-push-notifications-grpc.md)

DB reference: [work-order-management-schema.md](../../ats-home-craft-supabase/docs/work-order-management-schema.md)

______________________________________________________________________

## Related

- [Top-level docs index](./README.md)
