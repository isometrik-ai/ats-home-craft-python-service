# ADR 0011: Notice board — admin publish, resident feed

|                  |                                                                                                                                                                                                                                              |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Proposed                                                                                                                                                                                                                                     |
| **Date**         | 2026-08-10                                                                                                                                                                                                                                   |
| **Authors**      | Home Craft platform team                                                                                                                                                                                                                     |
| **Depends on**   | [ADR 0010](./0010-project-membership.md) (project scoping), [ADR 0009](./0009-contact-roles.md), [ADR 0008](./0008-push-notifications-grpc.md) (push, Phase 2)                                                                               |
| **Related docs** | [0011-notice-board-flow.md](../0011-notice-board-flow.md) (incl. §14 recurring), [notice-board-schema.md](../../../../../ats-home-craft-supabase/docs/notice-board-schema.md)                                                                |
| **Migrations**   | `20260810120000_notice_board_enums.sql`, `20260810121000_notice_board_tables.sql`, `20260810122000_notice_board_rls_deferred.sql`; recurring TBD — `20261023120000_notice_recurring_enums.sql`, `20261023121000_notice_recurring_tables.sql` |

______________________________________________________________________

## Context

Community admins publish notices for a **project** (`projects` = gated community). Product UI:
**Community → Notices** — create, schedule, list, pin to banner, soft-delete.

### Product rules (confirmed)

| Area                  | Behaviour                                                                                                                             |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| **Content**           | Title ≤70, description ≤600, category, up to 4 images (JPG/PNG, ≤5 MB)                                                                |
| **Recipients**        | Multi-select Owner, Tenant, Staff, Security — recipients never see other groups                                                       |
| **Scope**             | Whole society or by tower                                                                                                             |
| **Publishing**        | Draft, publish now → `live`, schedule → `scheduled` (**≤2 months** ahead)                                                             |
| **Live immutability** | Published notices **cannot be edited** — delete or copy via Create Notice                                                             |
| **Duplicate live**    | UI prefills Create Notice; **`POST /notices`**, not duplicate API                                                                     |
| **Banner pins**       | **6 generic slots**; **only live** notices; slot header = notice **category label**; click → detail                                   |
| **Soft delete**       | Audit retained; restore creates new draft copy (duplicate semantics)                                                                  |
| **Recurring series**  | Repeat on create drawer — weekly/monthly; **Start series** via same `POST /notices`; **same notice row** refreshed each delivery (§6) |

### Membership alignment ([ADR 0010](./0010-project-membership.md))

- Notices are **project-scoped** (`organization_id` + `project_id`).
- **Staff admin** routes: org RBAC + `ensure_staff_project_access(project_id)`.
- **Resident feed** (Phase 2): `contacts` + `contact_units` / `contact_roles`; Security via `organization_members`.
- Session is org-scoped; client passes `project_id` on staff routes.

______________________________________________________________________

## Decision

### 1. Six new tables

| Table                    | Purpose                                                                 |
| ------------------------ | ----------------------------------------------------------------------- |
| **`notices`**            | Header: content, category, status, scope, schedule, stats, display code |
| **`notice_recipients`**  | Audience groups (Owner, Tenant, Staff, Security)                        |
| **`notice_towers`**      | Tower scope when `by_tower`                                             |
| **`notice_attachments`** | Up to 4 images                                                          |
| **`notice_pins`**        | Banner slot assignment (`slot_index` 1–6) for **live** notices only     |
| **`notice_likes`**       | One like per contact (Phase 2)                                          |

### 2. Status model

| Status      | Meaning                                               |
| ----------- | ----------------------------------------------------- |
| `draft`     | Not visible; incomplete targeting allowed             |
| `scheduled` | `publish_at` ≤ now()+2 months; editable until go-live |
| `live`      | Visible; **immutable** (`PATCH` → `409`)              |
| `deleted`   | Soft-deleted; restorable to draft                     |

### 3. Banner slots (pinning)

- **Six generic slots** per project (`slot_index` 1–6) — not keyed by recipient group or fixed category column.
- **Only `live` notices** may be pinned (at publish-now or via `POST .../pin`).
- Slot **header** in UI = pinned notice's **category label** (Maintenance, Event, …) — replaces Owner/Tenant/Staff/Security labels on the banner row.
- **Click slot** → notice detail drawer.
- Constraints: one active pin per slot; one active pin per notice; max 6 active pins per project.

Category on `notices` is for tagging and banner **display** only — not a slot key.

### 4. API (Phase 1 admin)

Prefix: **`/v1/projects/{project_id}/notices`**

Staff access: permission (e.g. `projects_management.*` or dedicated `notices_management.*`) **and** [ADR 0010 staff project access](./0010-project-membership.md#2-staff-access-formula).

Key endpoints: summary, list, CRUD, pin/unpin, reach-estimate, duplicate (draft/scheduled only).

Resident Phase 2: **`/v1/notices`** — feed + banner + like.

**No separate notice-series API.** Recurring behaviour is an extension of the same routes:

| Action                      | Route                                            | Change                                                               |
| --------------------------- | ------------------------------------------------ | -------------------------------------------------------------------- |
| Create draft / start series | `POST .../notices`                               | Optional `repeat` object; `publish_mode` adds **`start_series`**     |
| Edit / end series           | `PATCH .../notices/{id}`                         | Update `repeat`; promote with `start_series`; stop with `end_series` |
| List / detail / summary     | Existing GET                                     | `is_recurring`, `next_send_at`, `repeats_label` on same notice rows  |
| Delete recurring            | `POST .../notices/{id}/delete`                   | Same row; sets `repeat_ended_at`                                     |
| Deliver recurring sends     | Existing **`POST .../notices/publish-due`** cron | Same job as one-time scheduled notices                               |
| Reach / pin                 | Unchanged                                        | Pin the **same** notice when live                                    |

### 5. Display code

`display_code` e.g. `NTC-1042` — monotonic per `project_id`, immutable.

______________________________________________________________________

## Schema (proposed)

See [notice-board-schema.md](../../../../../ats-home-craft-supabase/docs/notice-board-schema.md).

### `notice_pins` (banner)

| Column                                   | Notes                              |
| ---------------------------------------- | ---------------------------------- |
| `project_id`, `notice_id`                | Notice must be `live` when pinning |
| `slot_index`                             | 1–6                                |
| `pin_duration`                           | manual, 24h, 72h                   |
| `is_active`, `expires_at`, `unpinned_at` |                                    |

Partial uniques: `(project_id, slot_index) WHERE is_active`; `(notice_id) WHERE is_active`.

### Recurring series (proposed — [flow §14](../0011-notice-board-flow.md#14-recurring-series-repeat-on-publish))

**Model:** One **`notices`** row per recurring notice. Content, recipients, towers, and attachments stay
on that id. Recurrence lives in **`notice_series`** (1:1 `notice_id`). Each automated send **reuses the
same notice**: update `published_at`, set `status = live`, append to **`notice_series.occurrences`**, advance
`next_send_at` or set **`repeat_ended_at`**. **No `notice_series.status`** (or occurrence status enum) —
lifecycle uses **`notices.status`** + `repeat_ended_at` / `next_send_at`.

| Table               | Purpose                                                                                         |
| ------------------- | ----------------------------------------------------------------------------------------------- |
| **`notice_series`** | Frequency, `weekly_days[]`, cursor, and send history jsonb (`occurrences`) — one row per notice |

**Job:** Recurring delivery is bundled with existing **`publish-due`** / `publish_scheduled_notices`
(one-time `publish_at` first, then due `notice_series.next_send_at`).

**Immutability:** Recurring notices remain **`PATCH`-able** while active (content applies to next delivery).
One-time live notices stay immutable.

______________________________________________________________________

## Consequences

### Positive

- Aligns with project membership and existing community features (passes, move events).
- Six-slot banner matches product cap; category label clarifies slot content without fixed category columns.
- Live immutability simplifies resident trust and caching.

### Follow-ups

1. Migrations in `ats-home-craft-supabase`.
1. Admin API + service in `user_service`.
1. Scheduled publish + pin expiry jobs.
1. Phase 2 resident feed + push ([ADR 0008](./0008-push-notifications-grpc.md)).
1. Recurring migrations + recurring delivery inside existing `publish-due`.

______________________________________________________________________

## Alternatives considered

| Alternative                           | Rejected because                                                       |
| ------------------------------------- | ---------------------------------------------------------------------- |
| Banner slots by recipient group       | Product uses category label on slot, not Owner/Tenant/…                |
| Fixed category columns (6 enum slots) | Slots are generic; any live notice fills a free slot                   |
| Edit live notices                     | Immutable for one-time; **recurring may PATCH** while active           |
| New notice row per recurring send     | Product reuses one notice id for all deliveries                        |
| Series status column                  | Use `notices.status` + `repeat_ended_at` only                          |
| Separate `process-recurring` HTTP     | Reuse existing `publish-due` cron so ops schedule one endpoint         |
| Duplicate API for live notices        | Must use explicit Create Notice flow                                   |
| Pin draft/scheduled                   | Only live notices may be pinned                                        |
| Separate `/notice-series` REST tree   | Product uses one Create Notice drawer — extend `POST /notices` only    |
| Duplicate junction tables for series  | Reuse `notice_recipients` / towers / attachments on the one notice row |
