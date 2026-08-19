# Notice Board Flow — Context & Change Guide

> **Status: Not yet implemented (ADR + flow spec).** Migrations and schema doc exist; admin API,
> resident feed, push, and background jobs are follow-ups. This document describes the **Notices**
> feature — community admin publish/schedule on dashboard, resident read on mobile — in the same
> style as [`tenant-requests-flow.md`](./tenant-requests-flow.md), [`walk-in-flow.md`](./walk-in-flow.md),
> and [`move-events-flow.md`](./move-events-flow.md).
>
> Schema and architecture rationale: [ADR 0012](./adr/0012-notice-board.md), [ADR 0011](./adr/0011-project-membership.md) (project access), [ADR 0010](./adr/0010-contact-roles.md) (recipient roles).

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **Admin API prefix:** `/v1/projects/{project_id}/notices`
- **Resident API prefix (Phase 2):** `/v1/notices`
- **DB schema:** `ats-home-craft-supabase` (migrations not yet added — see [notice-board-schema.md](../../ats-home-craft-supabase/docs/notice-board-schema.md))

______________________________________________________________________

## 1. What this flow does

A **community admin** (staff with project access per [ADR 0011](./adr/0011-project-membership.md))
creates **notices** for a **project** (gated community): maintenance alerts, events, billing
reminders, security updates, etc.

Each notice has:

- **Content** — title (≤70 chars), description (≤600), category chip, optional images (≤4).
- **Recipients** — one or more of **Owner**, **Tenant**, **Staff**, **Security** (recipients never
  see which other groups were targeted).
- **Scope** — **whole society** or **by tower** (one or more towers in the project).
- **Publishing** — save as **draft**, **publish now** → `live`, or **schedule** → `scheduled`
  (max **2 months** ahead).

On the **Live** tab, a **banner row** shows up to **6 pin slots**. Only **live (published)**
notices may be pinned. Each occupied slot shows the notice's **category name** as the header label
(e.g. Maintenance, Event) — **not** Owner/Tenant/Staff/Security. Clicking a slot opens the full
**notice detail** drawer.

**Live notices are immutable** — content and targeting cannot be edited after publish. Admins may
**delete** (soft) or **copy** via the **Create Notice** flow (prefilled). The duplicate API works
only for **draft** and **scheduled** sources.

Residents (Phase 2) see notices matching their role, unit tower, and scope; can view, like, and
see pinned banners on the home feed.

### Business rules (must enforce)

| Rule                                       | Enforcement                                                          |
| ------------------------------------------ | -------------------------------------------------------------------- |
| **Title ≤ 70 chars, description ≤ 600**    | Pydantic + DB CHECK on `notices`                                     |
| **≥1 recipient group to publish/schedule** | Service validation; drafts may omit                                  |
| **By tower → ≥1 tower selected**           | `notice_towers` rows required when `scope_type = by_tower`           |
| **≤4 attachments, JPG/PNG, ≤5 MB each**    | Service count + mime/size validation                                 |
| **Schedule ≤ 2 months ahead**              | `publish_at <= now() + interval '2 months'` → else `422`             |
| **Live notices immutable**                 | `PATCH` rejected when `status = live` → `409 notice_not_editable`    |
| **Only live notices can pin**              | Pin on create (publish now) or `POST .../pin`; reject otherwise      |
| **Max 6 banner slots**                     | `slot_index` 1–6; partial unique index per active slot               |
| **One notice → one slot**                  | Partial unique index on active `notice_id`                           |
| **Slot header = category label**           | UI derives from `notices.category`; not recipient group              |
| **Click banner → detail**                  | Same `GET .../notices/{id}` as card click                            |
| **Soft delete only**                       | `status = deleted`, `deleted_at` set; row retained for audit         |
| **Restore → draft**                        | Clears `deleted_at`; does not re-pin                                 |
| **Display code immutable**                 | `NTC-{n}` assigned at create per `project_id`                        |
| **Duplicate draft/scheduled**              | `POST .../duplicate` → new draft                                     |
| **Duplicate live**                         | UI → Create Notice prefilled → `POST .../notices` (no duplicate API) |
| **Staff project access**                   | `ensure_staff_project_access(project_id)` on all admin routes        |

### Screen → capability map

**Admin dashboard (Community → Notices)**

| Screen / action                                | Capability                                                                                               |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Page load + tab counts                         | `GET /projects/{project_id}/notices/summary`                                                             |
| Status tabs (All / Live / Scheduled / Deleted) | `GET /projects/{project_id}/notices?status=`                                                             |
| Banner slots row (Live tab only)               | `GET /projects/{project_id}/notices?status=live` (use `pinned`, `slot_index`, `category_label` on items) |
| Click pinned banner slot                       | `GET /projects/{project_id}/notices/{id}` (detail drawer)                                                |
| Group filter chips (Live tab)                  | `GET /projects/{project_id}/notices?status=live&group=Owner`                                             |
| Search by title                                | `GET /projects/{project_id}/notices?search=`                                                             |
| Notice card grid                               | List response                                                                                            |
| Notice detail drawer                           | `GET /projects/{project_id}/notices/{id}`                                                                |
| Create notice drawer                           | `POST /projects/{project_id}/notices`                                                                    |
| Save as draft                                  | `POST` with `{ "publish_mode": "draft" }`                                                                |
| Publish now (+ optional pin)                   | `POST` with `{ "publish_mode": "now", "pin_to_banner": true }`                                           |
| Schedule notice                                | `POST` with `{ "publish_mode": "schedule", "publish_at": "..." }`                                        |
| Reach estimate                                 | `GET /projects/{project_id}/notices/reach-estimate?groups=&scope=&tower_ids=`                            |
| Edit notice (draft / scheduled)                | `PATCH /projects/{project_id}/notices/{id}`                                                              |
| Pin live notice                                | `POST /projects/{project_id}/notices/{id}/pin`                                                           |
| Unpin                                          | `POST /projects/{project_id}/notices/{id}/unpin`                                                         |
| Duplicate (draft / scheduled)                  | `POST /projects/{project_id}/notices/{id}/duplicate`                                                     |
| Copy published notice                          | Create Notice prefilled → `POST /projects/{project_id}/notices`                                          |
| Delete                                         | `POST /projects/{project_id}/notices/{id}/delete`                                                        |
| Restore (Deleted tab)                          | `POST /projects/{project_id}/notices/{id}/restore`                                                       |
| Upload images                                  | Presigned URL → paths in create/PATCH body                                                               |

**Resident mobile (Phase 2)**

| Screen / action      | Capability                                             |
| -------------------- | ------------------------------------------------------ |
| Home banner carousel | `GET /notices/banner?project_id=`                      |
| Notice feed          | `GET /notices?project_id=`                             |
| Notice detail        | `GET /notices/{id}` — increment `view_count`           |
| Like / unlike        | `POST /notices/{id}/like`, `DELETE /notices/{id}/like` |

______________________________________________________________________

## 2. Architecture (layers)

Same 3-layer FastAPI pattern as the rest of the service:

```
HTTP → API router → NoticesService (business rules) → NoticesRepository (SQL) → Postgres
         ↓
  ensure_staff_project_access(project_id)     [admin routes]
  extract_notice_viewer_context()           [resident routes, Phase 2]
```

### File map (to create)

| Concern                     | File                                                                                          |
| --------------------------- | --------------------------------------------------------------------------------------------- |
| Admin API endpoints         | `app/api/notices.py`                                                                          |
| Resident API (Phase 2)      | `app/api/notices_resident.py`                                                                 |
| Route registration          | `app/api/routes.py`                                                                           |
| Orchestration               | `app/services/notices_service.py`                                                             |
| Recipient / reach count     | `app/services/notice_recipient_resolution_service.py`                                         |
| Persistence                 | `app/db/repositories/notices_repository.py`                                                   |
| Request/response models     | `app/schemas/notices.py`                                                                      |
| Enums (mirror Postgres)     | `app/schemas/enums.py`                                                                        |
| Admin RBAC + project access | Reuse `projects_management.*` + `ensure_staff_project_access`                                 |
| Presigned uploads           | Reuse `app/api/presigned_url.py`                                                              |
| Scheduled publish job       | `app/jobs/publish_scheduled_notices.py` (or cron script)                                      |
| Pin expiry job              | `app/jobs/expire_notice_pins.py`                                                              |
| Audit logging               | `@audit_api_call` + `set_audit_context`                                                       |
| i18n                        | `app/locales/en.json` (`notices.*`)                                                           |
| Push (Phase 2)              | Hook `PushNotificationService` per [push-notifications-flow.md](./push-notifications-flow.md) |

`NoticesService` composes `NoticeRecipientResolutionService` for reach estimates and (Phase 2) push
recipient lists — it does not duplicate `contact_roles` / `contact_units` query logic inline.

______________________________________________________________________

## 3. Data model

### New tables

| Table                    | Purpose                                                                   |
| ------------------------ | ------------------------------------------------------------------------- |
| **`notices`**            | Header: content, category, status, scope, schedule, stats, `display_code` |
| **`notice_recipients`**  | Audience groups (Owner, Tenant, Staff, Security)                          |
| **`notice_towers`**      | Tower scope when `scope_type = by_tower`                                  |
| **`notice_attachments`** | Up to 4 image rows (`file_path`, metadata, `sort_order`)                  |
| **`notice_pins`**        | Banner slot assignment (`slot_index` 1–6) for live notices                |
| **`notice_likes`**       | One row per contact like (Phase 2)                                        |

Full column reference: [notice-board-schema.md](../../ats-home-craft-supabase/docs/notice-board-schema.md).

### Reused tables

| Table                  | Role                                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------ |
| `projects`             | Community scope; all notices under `project_id`                                      |
| `towers`               | Tower picker when scope = by tower                                                   |
| `units`                | Tower linkage for Owner/Tenant reach + resident feed filter                          |
| `contacts`             | Like actor (Phase 2); admin `created_by` via user linkage                            |
| `contact_units`        | Active residency for reach + resident visibility                                     |
| `contact_roles`        | Owner / Tenant recipient resolution via units in project scope                       |
| `project_members`      | Staff / Security recipient resolution ([ADR 0011](./adr/0011-project-membership.md)) |
| `organization_members` | Staff admin access gate only — not notice audience for Staff/Security groups         |

### Status lifecycle

```text
                    ┌──────────┐
                    │  draft   │◄──── duplicate / restore (new draft copy)
                    └────┬─────┘
           save draft    │
                         │    publish now (+ optional pin if live)
                         ├──────────────────► live ──delete──► deleted
                         │
                         │    schedule (publish_at ≤ now+2mo)
                         ▼
                    ┌──────────┐
                    │ scheduled│──── publish_at reached ────► live
                    └────┬─────┘         (cron/job)
                         │
           edit/cancel   │    delete
                         ▼
                    ┌──────────┐
    duplicate API   │ deleted  │  (audit retained; pins cleared)
   (draft/sched)    └──────────┘
         ──► draft
         live copy ──► Create Notice (POST /notices) — not duplicate API
```

### Tab behaviour (admin list)

| Tab           | Query filter              | Banner row      |
| ------------- | ------------------------- | --------------- |
| **All**       | `status NOT IN (deleted)` | Hidden          |
| **Live**      | `status = live`           | Shown (6 slots) |
| **Scheduled** | `status = scheduled`      | Hidden          |
| **Deleted**   | `status = deleted`        | Hidden          |

**All** tab count excludes deleted. **Live** tab may additionally filter by recipient group chip
and search title.

### Category values (`notice_category`)

| Key           | UI label    | Icon hint |
| ------------- | ----------- | --------- |
| `maintenance` | Maintenance | Wrench    |
| `security`    | Security    | Shield    |
| `event`       | Event       | Calendar  |
| `billing`     | Billing     | Card      |
| `emergency`   | Emergency   | Alert     |
| `general`     | General     | Clock     |

Category appears on notice cards and as the **banner slot header label** when that notice is pinned.

______________________________________________________________________

## 4. Admin flow (step by step)

### 4.1 Preconditions

- Staff logged in (JWT → `organization_members`).
- Session org matches project's `organization_id`.
- `ensure_staff_project_access(project_id)` passes (org-wide project view **or** active
  `project_members` row).
- Permission: `projects_management.*` or dedicated `notices_management.*` (TBD — align with other
  community modules).
- Client passes `project_id` from staff `activeProjectId` ([frontend-membership-flow.md](./frontend-membership-flow.md)).

### 4.2 List page load

1. Admin opens **Community → Notices** for active project.
1. `GET /v1/projects/{project_id}/notices/summary` → tab counts + per-group live counts for filter chips.

**Summary response (example):**

```json
{
  "all": 8,
  "live": 6,
  "scheduled": 2,
  "deleted": 1,
  "live_by_group": {
    "Owner": 4,
    "Tenant": 4,
    "Staff": 2,
    "Security": 1
  }
}
```

3. Default tab **All** → `GET /notices?status=all&page=1&page_size=20`.
1. If tab **Live** → `GET /notices?status=live`; build banner row from list items with `pinned = true`.

**List query parameters:**

| Param               | Values                                 | Notes                                    |
| ------------------- | -------------------------------------- | ---------------------------------------- |
| `status`            | `all`, `live`, `scheduled`, `deleted`  | Required for tab                         |
| `group`             | `Owner`, `Tenant`, `Staff`, `Security` | Live tab only; notice must include group |
| `search`            | string                                 | Case-insensitive title substring         |
| `page`, `page_size` | int                                    | Standard pagination                      |

**Sort order (Live tab):** pinned notices first (active `notice_pins`), then `published_at DESC`.
Other tabs: status-appropriate timestamp DESC (`publish_at` for scheduled, `deleted_at` for deleted).

### 4.3 Create notice

#### Step 1 — Content

- Title, category chip, description.
- Optional attachments: client uploads via presigned URL, then sends paths in create body.

#### Step 2 — Recipients & scope

- Multi-select recipient groups.
- Scope: `whole_society` (default) or `by_tower` → tower checkboxes from project setup.
- Call reach estimate as selections change (see §4.10).

#### Step 3 — Publishing & placement

| Mode              | Result status | Pin allowed?            |
| ----------------- | ------------- | ----------------------- |
| **Save as draft** | `draft`       | No                      |
| **Publish now**   | `live`        | Yes (`pin_to_banner`)   |
| **Schedule**      | `scheduled`   | No (UI disables toggle) |

Schedule date must be **≤ 2 months** from now.

#### Create request (publish now + pin)

```http
POST /v1/projects/{project_id}/notices
Authorization: Bearer <staff_jwt>
Content-Type: application/json

{
  "title": "Elevator maintenance in Tower A",
  "description": "Please note that Elevator 2 in Tower A is undergoing emergency repairs...",
  "category": "maintenance",
  "recipient_groups": ["Owner", "Tenant"],
  "scope_type": "by_tower",
  "tower_ids": ["tower-a-uuid"],
  "publish_mode": "now",
  "pin_to_banner": true,
  "slot_index": 2,
  "pin_duration": "manual",
  "confirm_pin_replace": true,
  "attachments": [
    {
      "file_path": "org/{org_id}/projects/{project_id}/notices/temp/img1.jpg",
      "file_name": "elevator.jpg",
      "mime_type": "image/jpeg",
      "size_bytes": 245000,
      "sort_order": 0
    }
  ]
}
```

#### Create request (schedule)

```http
POST /v1/projects/{project_id}/notices
{
  "title": "Independence Day flag hoisting at 8 AM",
  "description": "Join us at the central lawn...",
  "category": "event",
  "recipient_groups": ["Owner", "Tenant", "Staff", "Security"],
  "scope_type": "whole_society",
  "publish_mode": "schedule",
  "publish_at": "2026-08-15T09:00:00+05:30"
}
```

#### Create request (draft)

```http
POST /v1/projects/{project_id}/notices
{
  "title": "Fire drill for all residents",
  "description": "A mandatory fire evacuation drill is being planned...",
  "category": "security",
  "publish_mode": "draft"
}
```

Drafts may omit recipients and scope until ready.

#### Service transaction (create)

1. `ensure_staff_project_access(project_id)`.
1. Validate content lengths, attachment count/mime/size.
1. If `publish_mode` in (`now`, `schedule`): validate ≥1 recipient, scope/towers, schedule horizon.
1. Allocate `sequence_number` + `display_code` (`NTC-{n}`) in transaction (per project).
1. Insert `notices` row with appropriate `status`, `published_at` (if now), `publish_at` (if schedule).
1. Replace `notice_recipients`, `notice_towers`, `notice_attachments` junction rows.
1. If `publish_mode = now` and `pin_to_banner`: call pin logic (§5) — notice is live.
1. Return full notice detail shape.

### 4.4 Edit notice (draft / scheduled only)

```http
PATCH /v1/projects/{project_id}/notices/{notice_id}
{
  "title": "Updated title",
  "description": "...",
  "category": "maintenance",
  "recipient_groups": ["Owner", "Tenant"],
  "scope_type": "by_tower",
  "tower_ids": ["tower-a-uuid", "tower-b-uuid"],
  "publish_mode": "schedule",
  "publish_at": "2026-09-01T08:00:00+05:30"
}
```

Rules:

- Rejected with **`409 notice_not_editable`** when `status = live` or `deleted`.
- Rescheduled `publish_at` must stay within **2-month** window.
- Cannot pin via PATCH — use `POST .../pin` after live, or pin at publish-now create.
- Replacing attachments: send full `attachments[]` array (same pattern as create).

### 4.5 Notice detail drawer

```http
GET /v1/projects/{project_id}/notices/{notice_id}
```

Opened from: card body click, banner slot click, or edit flow.

**Detail sections (UI):**

| Section            | Fields                                                                                  |
| ------------------ | --------------------------------------------------------------------------------------- |
| Header             | `display_code`, status badge, title, category                                           |
| Body               | Full `description`                                                                      |
| Attachments        | Image previews from `notice_attachments`                                                |
| Notice information | Recipients, scope, published/scheduled/deleted date, pin slot if any                    |
| Stats              | `view_count`, `like_count`                                                              |
| Actions            | Edit (if draft/scheduled), Duplicate, Delete, Restore (if deleted), Pin/Unpin (if live) |

**Detail response (example):**

```json
{
  "id": "uuid",
  "display_code": "NTC-1042",
  "status": "live",
  "title": "Elevator maintenance in Tower A",
  "description": "Please note that Elevator 2...",
  "category": "maintenance",
  "category_label": "Maintenance",
  "recipient_groups": ["Owner", "Tenant"],
  "scope_type": "by_tower",
  "scope_label": "Tower A",
  "tower_ids": ["tower-a-uuid"],
  "tower_names": ["Tower A"],
  "published_at": "2026-07-16T09:12:00+05:30",
  "attachments": [
    { "id": "uuid", "file_path": "...", "file_name": "elevator.jpg", "sort_order": 0 }
  ],
  "pinned": true,
  "slot_index": 2,
  "pin_duration": "manual",
  "view_count": 388,
  "like_count": 12,
  "editable": false,
  "created_at": "2026-07-16T09:00:00+05:30",
  "created_by_user_id": "uuid"
}
```

### 4.6 Duplicate

**Draft or scheduled source:**

```http
POST /v1/projects/{project_id}/notices/{notice_id}/duplicate
```

Service:

1. Assert source `status` in (`draft`, `scheduled`) — else **`409`**.
1. Copy title, description, category, recipients, towers, attachments (new attachment rows pointing
   to same storage paths or copy paths — product decision: **reuse paths** in Phase 1).
1. New `display_code`; optional `duplicate_of_id` on new row.
1. New row `status = draft`; no pin copied.
1. Return new notice detail.

**Live source — Create Notice flow (no duplicate API):**

1. User clicks Duplicate on live card or detail.
1. Frontend opens Create Notice drawer prefilled from `GET .../notices/{id}`.
1. User may change fields; submits **`POST .../notices`** as new create/publish/draft.
1. Optional `source_notice_id` in body for audit only.

### 4.7 Delete & restore

**Delete (soft):**

```http
POST /v1/projects/{project_id}/notices/{notice_id}/delete
{
  "reason": "Duplicate of NTC-1041"
}
```

Service:

1. Set `status = deleted`, `deleted_at = now()`, `deleted_reason`.
1. Deactivate all `notice_pins` for this notice (`is_active = false`, `unpinned_at = now()`).
1. Row remains in **Deleted** tab for audit.

Allowed from `draft`, `scheduled`, and `live`.

**Restore:**

```http
POST /v1/projects/{project_id}/notices/{notice_id}/restore
```

1. Assert `status = deleted`.
1. **Same as duplicate:** copy title, description, category, recipients, towers, attachments into a **new** draft row with a new `display_code` and `duplicate_of_id` pointing at the deleted source.
1. Original deleted row **stays deleted** (audit retained in Deleted tab).
1. Does **not** automatically re-pin.

### 4.8 Publish draft / promote scheduled (optional explicit endpoints)

Phase 1 may use `PATCH` with `publish_mode` or dedicated endpoints:

```http
POST /v1/projects/{project_id}/notices/{notice_id}/publish
{
  "publish_mode": "now",
  "pin_to_banner": false
}
```

For scheduled notice: change `publish_mode` to `now` → sets `live`, `published_at = now()`.

### 4.9 List card field mapping

| UI field       | Source                                                |
| -------------- | ----------------------------------------------------- |
| `NTC-1042`     | `notices.display_code`                                |
| Status badge   | `notices.status`                                      |
| Category chip  | `notices.category` → label                            |
| Title, snippet | `title`, truncated `description`                      |
| Image count    | `COUNT(notice_attachments)`                           |
| Recipients     | `notice_recipients.recipient_group[]`                 |
| Scope          | `scope_label` from `scope_type` + tower names         |
| Date line      | See below                                             |
| Pinned ribbon  | `pinned = true` when active pin exists                |
| Views / likes  | `view_count` (eye), `like_count` (heart) — see §4.11  |
| Edit icon      | Hidden when `status = live`                           |
| Duplicate      | Live → Create Notice; draft/scheduled → duplicate API |
| Restore        | Shown only on Deleted tab                             |

**Date line by status:**

| Status      | Display                    |
| ----------- | -------------------------- |
| `live`      | `published_at` formatted   |
| `scheduled` | `Publishes {publish_at}`   |
| `draft`     | `Last edited {updated_at}` |
| `deleted`   | `Deleted {deleted_at}`     |

### 4.10 Reach estimate

```http
GET /v1/projects/{project_id}/notices/reach-estimate
  ?groups=Owner,Tenant
  &scope_type=by_tower
  &tower_ids=tower-a-uuid,tower-b-uuid
```

Returns approximate recipient count for the create form ("**N** people will receive this notice").

| Group              | Resolution sketch                                                                                                                                                                          |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Owner / Tenant** | Distinct `contact_id` from active `contact_roles` on units in the project; tower scope applies when `scope_type = by_tower`. Resolved to portal `user_id` via `contacts.user_id` for push. |
| **Staff**          | Active non-`security` `project_members` **plus** all active `security` project members (security also receives staff notices)                                                              |
| **Security**       | Active `project_members` with `role = security` only — staff roles do **not** receive security-only notices                                                                                |

Staff and Security counts ignore tower scope in Phase 1 (org/project scoped). Owner/Tenant scale
by tower selection.

### 4.11 Engagement metrics — views & likes

Admins see two independent counters on every notice **card** and in the **detail drawer** (eye =
views, heart = likes / acknowledgements). They measure different resident behaviour.

```text
Admin notice card (Live tab)
┌──────────────────────────────────────┐
│ NTC-1037                        Live │
│ Maintenance                          │
│ Water supply shut-off…               │
│ …                                    │
│ 👁 441    ♥ 18                       │  ← view_count / like_count from notices row
└──────────────────────────────────────┘

Scheduled / draft notices show 0 / 0 until residents interact after go-live.
```

#### What each counter means

| Metric           | Admin UI   | Meaning                                              | Who increments it                     |
| ---------------- | ---------- | ---------------------------------------------------- | ------------------------------------- |
| **`view_count`** | Eye icon   | Times residents **opened** the notice detail         | Resident `GET /v1/notices/{id}`       |
| **`like_count`** | Heart icon | Residents who **explicitly acknowledged** the notice | Resident `POST /v1/notices/{id}/like` |

**View without like:** A resident opens the notice → `view_count` increases, `like_count` unchanged
(e.g. 441 views, 18 likes).

**View with like:** Opening still increments views; tapping like additionally increments likes (once
per contact).

**Unlike:** `DELETE /v1/notices/{id}/like` decrements `like_count`; views are never decremented.

#### Data model

| Store                | Role                                                                 |
| -------------------- | -------------------------------------------------------------------- |
| `notices.view_count` | Denormalized total; default `0`; shown on admin list + detail        |
| `notices.like_count` | Denormalized total; default `0`; shown on admin list + detail        |
| `notice_likes`       | One row per `(notice_id, contact_id)`; source of truth for who liked |

Admin APIs are **read-only** for both counters — staff never POST views or likes.

#### Resident flow (write path)

```mermaid
sequenceDiagram
  participant ResidentApp
  participant ResidentAPI as GET/POST /v1/notices
  participant DB as Postgres

  ResidentApp->>ResidentAPI: GET /notices/{id}
  ResidentAPI->>DB: view_count += 1
  ResidentAPI-->>ResidentApp: detail + view_count + like_count + liked_by_me

  ResidentApp->>ResidentAPI: POST /notices/{id}/like
  ResidentAPI->>DB: INSERT notice_likes; like_count += 1
  ResidentAPI-->>ResidentApp: updated counts + liked_by_me true
```

1. **Open notice** — `GET /v1/notices/{notice_id}` after visibility check →
   `notices_repository.increment_view_count`.
1. **Like** — `POST /v1/notices/{notice_id}/like` → insert `notice_likes` (idempotent per contact)
   → bump `like_count`.
1. **Unlike** — `DELETE .../like` → remove row → decrement `like_count` (floor at 0).

Feed and banner list endpoints return current counts but **do not** increment views; only detail
GET does.

#### Admin read path

| Surface                            | Fields                     | API                                  |
| ---------------------------------- | -------------------------- | ------------------------------------ |
| Notice cards (All / Live / … tabs) | `view_count`, `like_count` | `GET .../notices?status=` list items |
| Detail drawer — Stats section      | same                       | `GET .../notices/{id}`               |

Counts update on next list/detail fetch after resident activity (no realtime push to admin in
Phase 1).

#### Product rules

| Rule                                              | Behaviour                                                         |
| ------------------------------------------------- | ----------------------------------------------------------------- |
| Only **live** notices accept resident views/likes | Draft / scheduled / deleted → resident detail returns 404         |
| Views ≠ likes                                     | High views with low likes is expected (read but not acknowledged) |
| Scheduled cards                                   | Show `0` / `0` until published and opened                         |
| Duplicate / restore                               | New notice row starts at `0` / `0`                                |
| Admin actions                                     | Create, edit, pin, delete do not change engagement counts         |

#### Phase 1 vs future dedupe

**Phase 1 (current):** Every resident detail GET increments `view_count` (repeat opens by the
same contact each add +1). Likes remain one-per-contact via `notice_likes` unique constraint.

**Optional later:** `notice_views` junction table to count **unique** viewers or dedupe views per
contact per notice; admin would still read `view_count` from `notices`.

Implementation: `notices_resident_service.get_notice`, `notices_repository.increment_view_count`,
`upsert_like` / `delete_like`.

______________________________________________________________________

## 5. Banner slots & pinning

### 5.1 UI layout

```text
BANNER SLOTS — UP TO 6 PINNED NOTICES

┌─────────────┬─────────────┬─────────────┬─────────────┬─────────────┬─────────────┐
│ Maintenance │ Event       │  (empty)    │  (empty)    │  (empty)    │  (empty)    │
│ NTC-1042    │ NTC-1040    │ No banner   │ No banner   │ No banner   │ No banner   │
│ Elevator…   │ Independence│             │             │             │             │
└─────────────┴─────────────┴─────────────┴─────────────┴─────────────┴─────────────┘
     slot 1        slot 2        slot 3        slot 4        slot 5        slot 6

• Slot header = category label from pinned notice (NOT Owner/Tenant/Staff/Security)
• Click occupied slot → notice detail drawer
• Only live notices may occupy slots
```

Slots are **generic** — not reserved per category or recipient group. Two Maintenance notices
cannot both be pinned unless they occupy different slot indices (max one pin per notice).

### 5.2 Banner slots (admin UI)

Build the Live-tab banner grid from **`GET /notices?status=live`** list items where `pinned = true`, keyed by `slot_index` (1–6). Render empty placeholders for unoccupied slots. Resident mobile banner still uses **`GET /v1/notices/banner?project_id=`**.

### 5.3 Pin (live only)

At **publish now** (create) or after publish:

```http
POST /v1/projects/{project_id}/notices/{notice_id}/pin
{
  "slot_index": 3,
  "pin_duration": "72h",
  "confirm_pin_replace": false
}
```

| Field                 | Notes                                      |
| --------------------- | ------------------------------------------ |
| `slot_index`          | Optional 1–6; default = lowest free slot   |
| `pin_duration`        | `manual`, `24h`, `72h`                     |
| `confirm_pin_replace` | Required when target slot already occupied |

**Pin duration → `expires_at`:**

| `pin_duration` | `expires_at`                           |
| -------------- | -------------------------------------- |
| `manual`       | `NULL` (until unpin or notice deleted) |
| `24h`          | `pinned_at + 24 hours`                 |
| `72h`          | `pinned_at + 72 hours`                 |

**Service steps:**

1. Assert notice `status = live` — else **`409 notice_not_pinnable`**.
1. Assert notice not already pinned (active row) — else **`409 notice_already_pinned`** or idempotent unpin+re-pin.
1. Resolve `slot_index`: explicit or first free 1–6.
1. If slot occupied and not `confirm_pin_replace` → **`409 slot_occupied`** with current holder.
1. If all 6 occupied and no slot specified → **`422 pin_slots_full`**.
1. Deactivate previous pin on that slot (if any) in same transaction.
1. Insert `notice_pins` row with `is_active = true`.

**Replace confirm UI:** when pinning would displace another notice, show panel listing displaced
`display_code` + title; user confirms → `confirm_pin_replace: true`.

### 5.4 Unpin

```http
POST /v1/projects/{project_id}/notices/{notice_id}/unpin
```

Sets active pin `is_active = false`, `unpinned_at = now()`. Idempotent if not pinned.

### 5.5 Pin expiry (background job)

Periodic job (e.g. every 15 minutes):

```sql
UPDATE notice_pins
SET is_active = false, unpinned_at = now()
WHERE is_active = true
  AND expires_at IS NOT NULL
  AND expires_at <= now();
```

______________________________________________________________________

## 6. Attachments

Follow presigned upload pattern from tenant requests / project media:

1. Client requests presigned URL (`POST /v1/presigned-url` with notice attachment key prefix).
1. Client uploads to storage bucket.
1. Client sends `file_path` + metadata in create/PATCH body.

**Storage path convention:**

```text
{organization_id}/projects/{project_id}/notices/{notice_id}/{uuid}.{ext}
```

For create-before-id flows, use a temp prefix then rewrite paths on first save, or create draft
row first then attach.

**Validation:**

| Rule         | Limit                     |
| ------------ | ------------------------- |
| Count        | ≤ 4 per notice            |
| MIME         | `image/jpeg`, `image/png` |
| Size         | ≤ 5 MB each               |
| `sort_order` | 0–3                       |

______________________________________________________________________

## 7. Background jobs

### 7.1 Scheduled publish

Cron every minute (or `POST /notices/publish-due` for ops):

```sql
UPDATE notices
SET status = 'live',
    published_at = now(),
    updated_at = now()
WHERE status = 'scheduled'
  AND publish_at <= now();
```

Does **not** auto-pin — pinning requires explicit admin action on live notices.

### 7.2 Pin expiry

See §5.5.

______________________________________________________________________

## 8. Resident flow (Phase 2)

### 8.1 Visibility rule

A notice is **visible** to a resident when **all** of:

1. `status = live`
1. Caller has active `contact_units` for the project (or org member for Security-only notices — TBD)
1. Caller matches ≥1 `notice_recipients` group:
   - **Owner / Tenant:** active `contact_roles` on a unit in scope (via `contacts`)
   - **Staff:** active non-`security` `project_members`, **and** security project members (staff notices also go to security)
   - **Security:** active `project_members` with `role = security` only
1. Scope matches:
   - `whole_society` → always (within project)
   - `by_tower` → caller's unit `tower_id` ∈ `notice_towers`

**Privacy:** API never exposes which other recipient groups were targeted.

### 8.2 Resident endpoints

```http
GET /v1/notices/banner?project_id=
GET /v1/notices?project_id=&search=&page=1&page_size=20
GET /v1/notices/{notice_id}
POST /v1/notices/{notice_id}/like
DELETE /v1/notices/{notice_id}/like
```

- **Banner:** up to 6 pinned live notices for project (from `notice_pins`), filtered by resident
  visibility rule. Returns `view_count`, `like_count`, `liked_by_me`; does **not** increment views.
- **Feed list:** same count fields; does **not** increment views. Optional `search` filters by notice title (case-insensitive substring match, max 200 chars), applied after visibility filtering.
- **Detail GET:** increments `view_count` by 1 when resident opens notice — see §4.11.
- **Like / unlike:** upsert/delete `notice_likes`; update denormalized `like_count` — see §4.11.

Auth: `extract_notice_viewer_context()` — resolves **residents** via `contacts`, or **staff/security** via active `project_members` on the requested `project_id` (list/banner) or the notice's project (detail/like).

### 8.3 Push on publish (Phase 2b)

After publish-now or scheduled go-live, resolve recipient user IDs and call
`PushNotificationService` per [push-notifications-flow.md](./push-notifications-flow.md) and
[ADR 0009](./adr/0009-push-notifications-grpc.md).

______________________________________________________________________

## 9. Relationship to existing flows

| Existing doc                                                 | Relationship                                                     |
| ------------------------------------------------------------ | ---------------------------------------------------------------- |
| [membership-architecture.md](./membership-architecture.md)   | Project scoping; staff `project_members` gate                    |
| [frontend-membership-flow.md](./frontend-membership-flow.md) | Staff `activeProjectId`; route `/projects/:id/community/notices` |
| [project-setup-flow.md](./project-setup-flow.md)             | Towers required for by-tower scope                               |
| [ADR 0010 / contact-roles](./adr/0010-contact-roles.md)      | Owner/Tenant/Staff recipient resolution                          |
| [push-notifications-flow.md](./push-notifications-flow.md)   | Phase 2 push on publish                                          |
| [tenant-requests-flow.md](./tenant-requests-flow.md)         | Same admin project prefix pattern                                |
| [fee-flow.md](./fee-flow.md)                                 | No direct coupling Phase 1                                       |

### Difference from CRM broadcasts (if added later)

|                        | Notices                   | Hypothetical org-wide broadcast |
| ---------------------- | ------------------------- | ------------------------------- |
| Scope                  | Single `project_id`       | Could span org                  |
| Audience               | Role groups + tower scope | Often all contacts              |
| Pinning                | 6 banner slots            | N/A                             |
| Immutability when live | Yes                       | TBD                             |

______________________________________________________________________

## 10. Error cases (i18n keys to add)

| Key                                       | When                                      |
| ----------------------------------------- | ----------------------------------------- |
| `notices.errors.title_too_long`           | Title > 70 chars                          |
| `notices.errors.description_too_long`     | Description > 600 chars                   |
| `notices.errors.recipients_required`      | Publish/schedule without recipient groups |
| `notices.errors.towers_required`          | `by_tower` without tower_ids              |
| `notices.errors.schedule_too_far`         | `publish_at` > 2 months ahead             |
| `notices.errors.not_editable`             | PATCH on live notice                      |
| `notices.errors.not_pinnable`             | Pin on non-live notice                    |
| `notices.errors.already_pinned`           | Pin when notice already in a slot         |
| `notices.errors.pin_slots_full`           | All 6 slots occupied                      |
| `notices.errors.slot_occupied`            | Pin without confirm when slot taken       |
| `notices.errors.too_many_attachments`     | > 4 attachments                           |
| `notices.errors.invalid_attachment`       | Wrong mime or size                        |
| `notices.errors.duplicate_live_forbidden` | Duplicate API on live notice              |
| `notices.errors.project_access_denied`    | Failed `ensure_staff_project_access`      |
| `notices.errors.not_found`                | Unknown notice_id or wrong project        |

______________________________________________________________________

## 11. Frontend notes (staff admin)

Route: `/projects/:projectId/community/notices` ([frontend-membership-flow.md](./frontend-membership-flow.md)).

| UI element               | Behaviour                                               |
| ------------------------ | ------------------------------------------------------- |
| Banner section label     | "Banner slots — up to 6 pinned notices"                 |
| Slot header              | Category label from pinned notice                       |
| Live card Edit           | Hidden / disabled                                       |
| Live card Duplicate      | Opens Create Notice prefilled                           |
| Scheduled card           | Shows "Publishes {date}"; pin toggle disabled in create |
| Deleted card             | Restore button; no edit/duplicate                       |
| Card engagement row      | Eye = `view_count`, heart = `like_count` (§4.11)        |
| Applied filters          | Chips + "Clear all"                                     |
| Create drawer validation | Footer note lists missing required fields               |

**Cache keys (React Query):**

```typescript
["notices", orgId, projectId, "summary"]
["notices", orgId, projectId, filters]
["notices", orgId, projectId, noticeId]
```

Invalidate on project switch and after create/publish/delete/pin.

______________________________________________________________________

## 12. Implementation phases

| Phase  | Scope                                                                       |
| ------ | --------------------------------------------------------------------------- |
| **1a** | Migrations + `notice-board-schema.md`                                       |
| **1b** | Admin API: summary, list, detail, create, PATCH, delete, restore, duplicate |
| **1c** | Banner-slots, pin/unpin, reach-estimate                                     |
| **1d** | Presigned upload integration + attachment validation                        |
| **1e** | Scheduled publish job + pin expiry job                                      |
| **2a** | Resident feed + banner + view/like                                          |
| **2b** | Push on publish ([ADR 0009](./adr/0009-push-notifications-grpc.md))         |
| **3**  | RLS policies if client-side Supabase reads added                            |

______________________________________________________________________

## 13. Where to change things (quick reference)

| Change                       | Location                                                    |
| ---------------------------- | ----------------------------------------------------------- |
| Add notice category          | Migration enum + `NoticeCategory` + UI icons                |
| Change pin slot count (6)    | Migration CHECK + service constant + UI grid                |
| Reach count query            | `notice_recipient_resolution_service.py`                    |
| Pin / unpin rules            | `notices_service.pin_notice` / `unpin_notice`               |
| Scheduled horizon (2 months) | `notices_service._validate_schedule`                        |
| Live immutability            | `notices_service.update_notice` guard                       |
| Display code format          | `notices_repository.allocate_display_code`                  |
| Admin RBAC                   | `notices.py` — permission dependency                        |
| Project access               | `ensure_staff_project_access` in router or service          |
| Resident visibility          | `notices_resident_service._is_visible_to_contact` (Phase 2) |
| Tab counts                   | `notices_repository.get_summary_counts`                     |
| View / like counters         | §4.11; `notices_resident_service` + `notices_repository`    |
