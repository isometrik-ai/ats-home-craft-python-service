# ADR 0011: Notice board — admin publish, resident feed

|                  |                                                                                                                                                                                                                                                    |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Proposed                                                                                                                                                                                                                                           |
| **Date**         | 2026-08-10                                                                                                                                                                                                                                         |
| **Authors**      | Home Craft platform team                                                                                                                                                                                                                           |
| **Depends on**   | [ADR 0010](./0010-project-membership.md) (project scoping), [ADR 0009](./0009-contact-roles.md), [ADR 0008](./0008-push-notifications-grpc.md) (push, Phase 2)                                                                                     |
| **Related docs** | [0011-notice-board-flow.md](../0011-notice-board-flow.md), [notice-board-schema.md](../../../../../ats-home-craft-supabase/docs/notice-board-schema.md)                                                                                            |
| **Migrations**   | `20260810120000_notice_board_enums.sql`, `20260810121000_notice_board_tables.sql`, `20260810122000_notice_board_rls_deferred.sql`; recurring follow-up: `20261022120000_notice_recurring_enums.sql`, `20261022121000_notice_recurring_columns.sql` |

______________________________________________________________________

## Context

Community admins publish notices for a **project** (`projects` = gated community). Product UI:
**Community → Notices** — create, schedule, list, pin to banner, soft-delete.

### Product rules (confirmed)

| Area                  | Behaviour                                                                                                                               |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **Content**           | Title ≤70, description ≤600, category, up to 4 images (JPG/PNG, ≤5 MB)                                                                  |
| **Recipients**        | Multi-select Owner, Tenant, Staff, Security — recipients never see other groups                                                         |
| **Scope**             | Whole society or by tower                                                                                                               |
| **Publishing**        | Draft, publish now → `live`, schedule → `scheduled` (**≤2 months** ahead); **Repeat** → recurring template on same `POST /notices` (§6) |
| **Live immutability** | Published notices **cannot be edited** — delete or copy via Create Notice                                                               |
| **Duplicate live**    | UI prefills Create Notice; **`POST /notices`**, not duplicate API                                                                       |
| **Banner pins**       | **6 generic slots**; **only live** notices; slot header = notice **category label**; click → detail                                     |
| **Soft delete**       | Audit retained; restore creates new draft copy (duplicate semantics)                                                                    |

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

**Recurring (§6):** extend the same prefix — optional `repeat` on `POST`/`PATCH`; optional
`POST .../notices/recurrence-preview`. No separate `/notice-series` router.

Resident Phase 2: **`/v1/notices`** — feed + banner + like (exclude recurring **templates**).

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

### Recurring columns on `notices` (follow-up migration)

Same six tables — no parallel series tables. Template rows: `is_recurring_template = true` plus
`series_status`, recurrence rule columns, `next_occurrence_at`, `occurrences_sent`. Instance rows:
`recurring_source_notice_id`, `series_occurrence_index`. Junction tables unchanged; copied per send.
Full column list: [notice-board-schema.md](../../../../../ats-home-craft-supabase/docs/notice-board-schema.md).

______________________________________________________________________

## 6. Recurring series (repeat on publish)

Product **Repeat this notice** uses the **same** admin API and `notices` table as one-time notices.

### 6.1 API (extend existing)

| UI action                        | Endpoint                                                     |
| -------------------------------- | ------------------------------------------------------------ |
| Save as Draft / **Start Series** | `POST /v1/projects/{project_id}/notices` + optional `repeat` |
| Edit template                    | `PATCH .../notices/{template_id}`                            |
| Pause / resume / cancel          | `PATCH .../notices/{template_id}` with `series_action`       |
| List / detail / summary          | Existing `GET .../notices*`                                  |
| Preview next sends               | Optional `POST .../notices/recurrence-preview` (same router) |

Extend **`NoticePublishMode`**: when `repeat` is present, use `draft` or `series_start` only — not
`now` or `schedule`.

### 6.2 Behaviour

Each send materializes a **new** live `notices` row (new `display_code`); instances stay immutable.
Template lifecycle: `series_status` ∈ `draft`, `active`, `paused`, `completed`, `cancelled` on the
template row. Job selects `is_recurring_template AND series_status = active AND next_occurrence_at <= now()`.

Weekly/monthly rules, end conditions, and UI examples: [0011-notice-board-flow.md §4.3 & §7.3](../0011-notice-board-flow.md).

### 6.3 Alternatives rejected (recurring)

| Alternative                         | Rejected because                                |
| ----------------------------------- | ----------------------------------------------- |
| `POST /notice-series`               | Same Create Notice drawer; extend `/notices`    |
| Separate `notice_series*` tables    | Duplicates junction tables already on `notices` |
| Materialize all occurrences upfront | Breaks immutability; row explosion              |

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
1. Recurring columns + extend create/PATCH + materialize job (§6).

______________________________________________________________________

## Alternatives considered

| Alternative                           | Rejected because                                        |
| ------------------------------------- | ------------------------------------------------------- |
| Banner slots by recipient group       | Product uses category label on slot, not Owner/Tenant/… |
| Fixed category columns (6 enum slots) | Slots are generic; any live notice fills a free slot    |
| Edit live notices                     | Confirmed immutable after publish                       |
| Duplicate API for live notices        | Must use explicit Create Notice flow                    |
| Pin draft/scheduled                   | Only live notices may be pinned                         |
