# ADR 0004: Work orders and vendor portal — token-scoped access

|               |                                                                                                        |
| ------------- | ------------------------------------------------------------------------------------------------------ |
| **Status**    | Accepted (V1)                                                                                          |
| **Date**      | 2026-09-02                                                                                             |
| **Flow docs** | [../work-orders-flow.md](../work-orders-flow.md), [../vendor-portal-flow.md](../vendor-portal-flow.md) |
| **Schema**    | `work_order.work_orders`                                                                               |

______________________________________________________________________

## Context

Work orders track vendor tasks with state lifecycle, append-only timeline, form values, and line items.
**External vendors** must access exactly one assigned WO without org JWT credentials.

The prototype exposed all work orders on an unauthenticated API and used vendor tokens only in the UI.

______________________________________________________________________

## Decision

### Work order lifecycle

States: `upcoming` → `in_progress` → `in_review` → `completed`; any → `terminated`.

Sources: `contract`, `ad_hoc`, `recurring`. Staff routes under `/v1/projects/{project_id}/work-orders`.

Timeline: **append-only** via `POST .../work-orders/{id}/timeline` — server assigns `id` and `at`.

PATCH must not accept `timeline` (lost-update race with vendor/FM concurrent appends).

### Vendor token model

| Step         | Rule                                                     |
| ------------ | -------------------------------------------------------- |
| Generate     | `secrets.token_urlsafe(32)` on staff or scheduler create |
| Store        | SHA-256 hex in `vendor_token_hash` only                  |
| Authenticate | Header `X-Vendor-Token` → hash → single row lookup       |
| Scope        | All `/v1/vendor/*` ops bound to that work order          |

Dependency: `get_work_order_from_vendor_token` in `app/dependencies/vendor_auth.py`.

### Vendor portal API

| Route                         | Purpose                                       |
| ----------------------------- | --------------------------------------------- |
| `GET /v1/vendor/work-order`   | View assigned WO                              |
| `PATCH /v1/vendor/work-order` | Whitelist: `state`, `form_values`, `timeline` |
| `POST /v1/vendor/invoices`    | Submit invoice for this WO                    |
| `GET /v1/vendor/invoices`     | List invoices for this WO                     |

Audit mutations with `source=vendor_portal` when frontend sets `X-ATS-Source`.

### Recurring work orders

Template row (`is_recurring=true`) with child WOs via `recurring_parent_id`; scheduler manages children
similar to contract generation.

______________________________________________________________________

## Consequences

**Positive:** Prototype security gap closed; vendors need no accounts; FM shares one link per WO.

**Negative:** No token expiry/rotation in V1; compromised link valid until WO deleted or token rotated (future).

**Follow-ups:** Token rotation endpoint; optional expiry column; vendor portal fetch WO from API on mount (not mock store).

______________________________________________________________________

## Alternatives considered

| Alternative            | Rejected because                           |
| ---------------------- | ------------------------------------------ |
| JWT for vendors        | External users; account provisioning heavy |
| Shared portal password | Not per-WO scoped                          |
| Query param token only | Header + query support; header preferred   |
