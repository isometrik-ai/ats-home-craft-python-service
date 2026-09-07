# ADR 0001: Work order service — separate deployable app

|                |                                                                                         |
| -------------- | --------------------------------------------------------------------------------------- |
| **Status**     | Accepted (V1)                                                                           |
| **Date**       | 2026-09-02                                                                              |
| **Flow doc**   | [../README.md](../README.md)                                                            |
| **Depends on** | [ADR 0011 — Project membership](../../user_service/docs/adr/0011-project-membership.md) |
| **Migrations** | `20260902120000_*` … `20260902128000_work_order_management_permissions.sql`             |

______________________________________________________________________

## Context

Facility management (assets, AMC contracts, vendor work orders, invoices) is a distinct bounded context
from resident CRM, passes, and fee billing. The Cloud AI prototype proved the domain but used no auth,
SQLAlchemy, and a single `tenant_id` string — incompatible with Home Craft standards.

We need a production module that:

- Deploys independently (scale, release cadence)
- Uses `organization_id` + `project_id` on every row
- Reuses existing JWT, asyncpg, and shared libs
- Does not bloat `user_service`

______________________________________________________________________

## Decision

### Separate service on port 5001

```
apps/work_order_service/
  app/api/ → app/services/ → app/db/repositories/
```

Reuse `libs/shared_*` (config, JWT middleware, asyncpg pool, FastAPI factory, rate limits).

### Postgres schema `work_order`

Same Supabase instance; `search_path TO work_order, public`. Cross-schema FKs to `public.organizations`,
`public.projects`, `public.companies`, `public.facilities`.

Soft delete via `record_status`. RLS enabled; policies deferred (service_role).

### API surfaces

| Prefix                          | Auth                                                            |
| ------------------------------- | --------------------------------------------------------------- |
| `/v1/projects/{project_id}/...` | JWT + `work_order_management.*` + `ensure_staff_project_access` |
| `/v1/vendor/...`                | `X-Vendor-Token` only                                           |

### RBAC — `work_order_management` permission group

`view`, `edit`, `approve`, `pay` — seeded in `DEFAULT_PERMISSIONS` and org migration.

### Out of scope for this ADR

Domain details per flow → [0002](./0002-assets-and-custom-fields.md) … [0006](./0006-integrations.md).

______________________________________________________________________

## Consequences

**Positive:** Clear ownership; team knows the `user_service` pattern; independent Docker on 5001.

**Negative:** Two services to run; cross-service import of `CustomFieldService` from user_service.

**Follow-ups:** Integration tests on shared test DB; grant FM permissions to pilot roles.

______________________________________________________________________

## Alternatives considered

| Alternative                          | Rejected because                                    |
| ------------------------------------ | --------------------------------------------------- |
| Merge into `user_service`            | Service already large                               |
| `libs/work_order/` package at launch | YAGNI — one consumer                                |
| Separate database                    | Unnecessary ops cost; FKs to CRM need same Postgres |
