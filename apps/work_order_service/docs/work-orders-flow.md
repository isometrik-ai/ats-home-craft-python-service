# Work Orders Flow — Context & Change Guide

> **Status: V1 implemented.**
> Architecture: [ADR 0004 — Work orders and vendor portal](./adr/0004-work-orders-and-vendor-portal.md)
> Part of [Work Order Service](./README.md).

- **Service:** `apps/work_order_service` (port 5001)
- **Staff API:** `/v1/projects/{project_id}/work-orders`
- **DB table:** `work_order.work_orders`

______________________________________________________________________

## 1. What this flow does

A **work order** is a unit of maintenance work — created manually (ad hoc), from an **AMC contract**
(scheduler), or as a **recurring series**. FM staff assign a CRM **vendor** (`company_id`), track state
through a lifecycle, and maintain an **append-only timeline**. Each WO carries a **vendor portal token**
for external access.

### State machine

```
upcoming → in_progress → in_review → completed
    │           │            │
    └───────────┴────────────┴──→ terminated
```

| State         | Meaning                                                        |
| ------------- | -------------------------------------------------------------- |
| `upcoming`    | Scheduled; vendor not started                                  |
| `in_progress` | Vendor on site / work underway                                 |
| `in_review`   | Work done; awaiting FM review or invoice                       |
| `completed`   | Closed successfully                                            |
| `terminated`  | Cancelled or superseded (scheduler may set on contract change) |

Enum: `work_order.work_order_work_order_state`.

### Business rules (must enforce)

| Rule                     | Enforcement                                                                               |
| ------------------------ | ----------------------------------------------------------------------------------------- |
| **Tenancy**              | `organization_id` + `project_id` on every query                                           |
| **Vendor token**         | Raw token never stored; only SHA-256 hash in `vendor_token_hash`                          |
| **Token generation**     | `secrets.token_urlsafe(32)` on create (staff or scheduler)                                |
| **Timeline append-only** | Use `POST .../timeline`; do not PATCH `timeline` from clients (race)                      |
| **Timeline ownership**   | Server assigns `id` + `at` timestamp via `new_timeline_event()`                           |
| **Soft delete**          | `record_status = deleted`                                                                 |
| **Sources**              | `contract`, `ad_hoc`, `recurring` enum                                                    |
| **Recurring WOs**        | `is_recurring`, `recurring_parent_id`, `recurring_frequency` — scheduler manages children |
| **Audit + webhooks**     | All create/update/delete → `EventsService.record_and_dispatch`                            |
| **Push notification**    | Best-effort on create via `notify_work_order_event`                                       |

### Screen → capability map

| Screen / action     | Capability                                                                      |
| ------------------- | ------------------------------------------------------------------------------- |
| WO list + filters   | `GET /projects/{project_id}/work-orders?state=&company_id=`                     |
| WO detail           | `GET /projects/{project_id}/work-orders/{id}`                                   |
| Create ad hoc WO    | `POST /projects/{project_id}/work-orders`                                       |
| Update state/fields | `PATCH /projects/{project_id}/work-orders/{id}`                                 |
| Append timeline     | `POST /projects/{project_id}/work-orders/{id}/timeline`                         |
| Delete WO           | `DELETE /projects/{project_id}/work-orders/{id}`                                |
| Copy vendor link    | Raw token from create response / timeline note                                  |
| Vendor portal       | `/vendor/work-order/{token}` → [vendor-portal-flow.md](./vendor-portal-flow.md) |

______________________________________________________________________

## 2. Architecture

```
work_orders.py → WorkOrdersService → WorkOrderRepository → work_order.work_orders
                      │
                      ├── EventsService (audit + webhooks)
                      ├── generate_vendor_token() on create
                      └── notify_work_order_event() on create
```

### File map

| Concern         | File                                            |
| --------------- | ----------------------------------------------- |
| Staff API       | `app/api/work_orders.py`                        |
| Service         | `app/services/work_orders_service.py`           |
| Repository      | `app/db/repositories/work_orders_repository.py` |
| Token utils     | `app/utils/tokens.py`                           |
| Timeline helper | `app/utils/records.py` → `new_timeline_event()` |
| Domain enums    | `app/schemas/enums.py` → `WorkOrderState`       |

______________________________________________________________________

## 3. Data model (key columns)

| Column                                 | Notes                               |
| -------------------------------------- | ----------------------------------- |
| `title`, `description`                 | Display                             |
| `state`                                | Lifecycle enum                      |
| `priority`                             | `low`, `medium`, `high`, `urgent`   |
| `scheduled_date`                       | Planned visit date                  |
| `company_id`                           | Assigned vendor (CRM)               |
| `asset_ids`                            | `uuid[]`                            |
| `contract_id`                          | Set when source = contract          |
| `form_template_id`                     | Checklist schema reference          |
| `form_values`, `pre_start_form_values` | jsonb — vendor/FM filled            |
| `line_items`                           | jsonb — vendor completion breakdown |
| `vendor_token_hash`                    | SHA-256 of portal token             |
| `timeline`                             | jsonb array — append-only events    |
| `is_recurring`, `recurring_*`          | Recurring series metadata           |

______________________________________________________________________

## 4. Staff flow (step by step)

### 4.1 Create ad hoc work order

```http
POST /v1/projects/{project_id}/work-orders
Authorization: Bearer <jwt>

{
  "title": "Lift entrapment — emergency check",
  "company_id": "<vendor-uuid>",
  "asset_ids": ["<asset-uuid>"],
  "priority": "urgent",
  "scheduled_date": "2026-09-05",
  "source": "ad_hoc",
  "form_template_id": "<uuid>"
}
```

Service:

1. Generates vendor token pair `(raw, hash)`.
1. Inserts WO with `state=upcoming`, `vendor_token_hash`.
1. Timeline note includes raw token for FM to share (consider masking in production UI).
1. Records audit event + enqueues matching webhooks.
1. Sends push notification (best-effort).

Share vendor link: `{frontend}/vendor/work-order/{raw_token}`.

### 4.2 Update work order

```http
PATCH /v1/projects/{project_id}/work-orders/{id}
{ "state": "in_progress", "started_at": "2026-09-05T10:00:00Z" }
```

**Do not** send `timeline` in PATCH — use append endpoint.

Status change to `state` triggers `status_changed` webhook event when diff detected.

### 4.3 Append timeline event

```http
POST /v1/projects/{project_id}/work-orders/{id}/timeline
Authorization: Bearer <jwt>

{
  "type": "vendor_alerted",
  "by": "Rajesh Kumar",
  "note": "Reminder sent via WhatsApp"
}
```

Returns full updated `timeline` array. Server adds `id` and `at`.

### 4.4 Delete work order

```http
DELETE /v1/projects/{project_id}/work-orders/{id}
```

Soft delete + audit `deleted` event. Scheduler-generated upcoming WOs may also be bulk-cancelled when
contract terms change (see [contracts-scheduler-flow.md](./contracts-scheduler-flow.md)).

______________________________________________________________________

## 5. Recurring work orders

Recurring templates (`is_recurring = true`) generate child WOs on a schedule similar to contracts.
Scheduler method: `SchedulerService.generate_recurring_work_orders()`.

Child rows reference `recurring_parent_id`. Cancelling a series terminates upcoming children via
`cancel_recurring_children()`.

______________________________________________________________________

## 6. Where to change things

| Change               | Edit                                                                       |
| -------------------- | -------------------------------------------------------------------------- |
| Allowed PATCH fields | `work_orders_repository.update()` SET clause                               |
| New state value      | Supabase enum + `WorkOrderState` enum + UI maps                            |
| Timeline event types | Convention only (jsonb); document in UI                                    |
| Vendor token policy  | `tokens.py`; consider separate `vendor_token_issued_at` column if rotating |
| Notification payload | `app/adapters/notifications.py`                                            |

______________________________________________________________________

## 7. Related flows

- Contract-generated WOs → [contracts-scheduler-flow.md](./contracts-scheduler-flow.md)
- Vendor actions → [vendor-portal-flow.md](./vendor-portal-flow.md)
- Invoices linked via `work_order_id` → [invoices-payments-flow.md](./invoices-payments-flow.md)
