# Daily Help Flow — Context & Change Guide

> **Status: Proposed — not implemented.** This document is the build guide for **Daily Help** in
> `user_service`: admin registry on the dashboard, resident directory + household links on mobile,
> and gate **Activities** via the existing visitor logs / pass pipeline. Schema and decisions:
> [ADR 0013](./adr/0013-daily-help.md).

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **Admin API prefix:** `/v1/projects/{project_id}/daily-help`
- **Resident API prefix:** `/v1/daily-help` (Phase 2)
- **Gate / Activities:** existing `/v1/passes/*` + `/v1/visitor-logs/*` ([passes-validation-flow.md](./passes-validation-flow.md))
- **DB schema:** `ats-home-craft-supabase` (migrations `20260811120000_*`, `20260811121000_*`, `20260811121500_*`, `20260811122000_*`)

______________________________________________________________________

## 1. What this flow does

**Daily Help** is a **project-scoped registry** of recurring household service providers (maids, cooks,
drivers, milk/newspaper delivery, etc.). Admins create and maintain records; residents browse the
directory and optionally link helpers to their unit. **Gate movement and the Activities feed reuse
visitor passes and visitor logs** — we do **not** create `contacts` rows or auth users for helpers.

### Product rules (must enforce)

| Rule                                    | Enforcement                                                                            |
| --------------------------------------- | -------------------------------------------------------------------------------------- |
| **No contact / auth user for helper**   | Only `daily_help_profiles` + child tables; never call `ContactsService.create_contact` |
| **Project-scoped registry**             | All queries filter `organization_id` + `project_id`                                    |
| **Admin-maintained only (Phase 1)**     | Create/update/status routes are staff-only                                             |
| **Documents on file — no verification** | Store paths; no verify/reject workflow (unlike tenant requests)                        |
| **Soft delete**                         | `status = deleted`; row retained; pass cancelled                                       |
| **One recurring gate pass per profile** | `linked_pass_id` + unique partial index on `passes.daily_help_id`                      |
| **Gate passcode searchable**            | Unique `(organization_id, project_id, gate_passcode)`                                  |
| **Categories per project**              | Admin-maintained `daily_help_categories` — not a global enum                           |
| **Check-in/out notifications**          | Push to Owner + Tenant on each active `daily_help_household_links` unit                |
| **Activities = visitor logs**           | Daily help check-ins appear as `pass_type = daily_help` pass rows                      |

### Screen → capability map

**Admin dashboard — Requests → Daily Help**

| Screen / element                                    | Capability                                                           |
| --------------------------------------------------- | -------------------------------------------------------------------- |
| Summary cards (Total / Active / Inactive / Deleted) | `GET /projects/{project_id}/daily-help/summary`                      |
| Status tabs + category filter + search              | `GET /projects/{project_id}/daily-help?status=&category_id=&search=` |
| Manage categories                                   | `GET/POST/PATCH /projects/{project_id}/daily-help/categories`        |
| Table list                                          | Same list endpoint (paginated)                                       |
| Add Daily Help drawer                               | `POST /projects/{project_id}/daily-help`                             |
| View detail drawer                                  | `GET /projects/{project_id}/daily-help/{id}`                         |
| Edit details                                        | `PATCH /projects/{project_id}/daily-help/{id}`                       |
| Mark inactive                                       | `POST /projects/{project_id}/daily-help/{id}/deactivate`             |
| Reactivate inactive                                 | `POST /projects/{project_id}/daily-help/{id}/reactivate`             |
| Delete record                                       | `POST /projects/{project_id}/daily-help/{id}/delete`                 |
| Restore (optional)                                  | `POST /projects/{project_id}/daily-help/{id}/restore`                |
| Add / remove document                               | `POST/PATCH/DELETE .../documents`                                    |
| Export                                              | `GET /projects/{project_id}/daily-help/export`                       |

**Resident mobile — Daily Help (Phase 2)**

| Screen / element                             | Capability                                                    |
| -------------------------------------------- | ------------------------------------------------------------- |
| Category home (Maids, Cooks, …)              | `GET /daily-help/categories` + `GET /daily-help?category_id=` |
| Search (name, mobile, passcode)              | `GET /daily-help/search?q=`                                   |
| Profile card list                            | `GET /daily-help?category_id={uuid}`                          |
| Profile detail                               | `GET /daily-help/{id}`                                        |
| Add to Household                             | `POST /daily-help/{id}/household-links?unit_id=`              |
| Remove from household                        | `DELETE /daily-help/{id}/household-links/{link_id}`           |
| Toggle open to work                          | `PATCH /daily-help/{id}/open-to-work?unit_id=`                |
| Category stats (Inside / Open to work / New) | Aggregates on list endpoints + visitor logs                   |

**Resident mobile — Activities (existing — visitor logs)**

| Screen / element                     | Capability                                                           |
| ------------------------------------ | -------------------------------------------------------------------- |
| Activity feed (maids, deliveries, …) | Client composes from visitor logs / pass list filtered by date       |
| INSIDE / LEFT badge                  | `visit_status` from pass events                                      |
| Rate now / Gatepass / Attendance     | Phase 2+; attendance = pass check-in history for `daily_help_id`     |
| Filter by date                       | `GET /visitor-logs?pass_type=daily_help&date=` (extend query params) |

______________________________________________________________________

## 2. Architecture (layers)

Same 3-layer FastAPI pattern as the rest of the service:

```
HTTP → API router → Service (business rules) → Repository (SQL) → Postgres
                          │
                          ├── PassesService (issue/cancel recurring pass)
                          ├── PassVerificationService (check-in/out → daily help notifications)
                          ├── DailyHelpNotificationService (resolve linked units + Owner/Tenant)
                          └── VisitorLogsService (read-only for Activities aggregates)
```

### File map (to create)

| Concern                    | File                                                                       |
| -------------------------- | -------------------------------------------------------------------------- |
| Admin routes               | `app/api/daily_help.py` (under projects router)                            |
| Resident routes (Phase 2)  | `app/api/daily_help_resident.py`                                           |
| Orchestration              | `app/services/daily_help_service.py`                                       |
| Check-in/out notifications | `app/services/daily_help_notification_service.py`                          |
| Pass hook                  | extend `app/services/pass_verification_service.py`                         |
| SQL                        | `app/db/repositories/daily_help_repository.py`                             |
| Category SQL               | `app/db/repositories/daily_help_categories_repository.py`                  |
| Schemas                    | `app/schemas/daily_help.py`                                                |
| Enums                      | `app/schemas/enums.py` — mirror Postgres enums                             |
| Pass linkage               | extend `passes_service.py` / `passes_repository.py`                        |
| Visitor logs filter        | extend `visitor_logs_repository.py` — `pass_type=daily_help`, join profile |
| RBAC                       | `libs/shared_utils/common_query.py` — new permission codes or reuse        |
| Tests                      | `tests/unit/test_daily_help_service.py`, `tests/integration/daily_help/`   |

`DailyHelpService` **composes** `PassesService` to create/update/cancel the linked recurring pass in
the same transaction as profile writes.

______________________________________________________________________

## 3. Data model

See [ADR 0013 § Schema](./adr/0013-daily-help.md#schema-proposed) for full DDL.

### New tables summary

| Table                        | Purpose                                                                    |
| ---------------------------- | -------------------------------------------------------------------------- |
| `daily_help_categories`      | **Project-scoped category catalog** (admin CRUD)                           |
| `daily_help_profiles`        | Person registry, `category_id` FK, status, gate passcode, `linked_pass_id` |
| `daily_help_documents`       | Photo, ID proof, police verification, other files                          |
| `daily_help_events`          | Audit timeline                                                             |
| `daily_help_household_links` | Phase 2 — resident ↔ unit links                                            |

### Modified existing tables

| Table    | Change                                                    |
| -------- | --------------------------------------------------------- |
| `passes` | `daily_help_id uuid` FK; `pass_type` enum + `daily_help`  |
| *(none)* | Visitor logs remain a read-model union — no new log table |

### Status lifecycle

```text
admin POST create ──► active (+ pass issued)
active ──deactivate──► inactive (+ pass cancelled)
inactive ──reactivate──► active (+ pass re-issued if needed)
active|inactive ──delete──► deleted (+ pass cancelled, deleted_at set)
```

### Categories (`daily_help_categories`)

Each **project** maintains its own category list. Admins add labels like “Maid”, “Milk Delivery”, or
“Car Cleaner” without code changes.

| Field        | Notes                                                           |
| ------------ | --------------------------------------------------------------- |
| `name`       | Display label; unique per project (case-insensitive)            |
| `sort_order` | Controls admin filter dropdown + resident home section order    |
| `status`     | `active` \| `inactive` — inactive hidden from new profile forms |

**API:**

```http
GET    /v1/projects/{project_id}/daily-help/categories
POST   /v1/projects/{project_id}/daily-help/categories        { "name": "Maid", "sort_order": 0 }
PATCH  /v1/projects/{project_id}/daily-help/categories/{id}   { "name": "...", "status": "inactive" }
```

Delete is rejected when profiles reference the category; deactivate instead.

Optional seed helper on first project setup: insert common defaults (Maid, Cook, Driver, …) — configurable, not enforced.

______________________________________________________________________

## 4. Admin flow (Phase 1)

### 4.0 Manage categories (before or alongside profiles)

Admins configure categories first (or rely on seeded defaults), then assign `category_id` when
creating daily help profiles.

### 4.1 Create daily help

```http
POST /v1/projects/{project_id}/daily-help
{
  "initials": "Mrs.",
  "first_name": "Lakshmi",
  "middle_name": null,
  "last_name": "Devi",
  "phone_isd_code": "+91",
  "phone_number": "9655011223",
  "alternate_phone_isd_code": null,
  "alternate_phone_number": null,
  "category_id": "category-uuid",
  "gender": "Female",
  "date_of_birth": "1988-04-12",
  "photo_path": "org/daily-help/photo_lakshmi.jpg",
  "open_to_work": true,
  "documents": [
    {
      "document_type": "id_proof",
      "label": "Aadhaar",
      "file_path": "org/daily-help/aadhaar_lakshmi.pdf",
      "file_name": "Aadhaar_Lakshmi.pdf"
    },
    {
      "document_type": "police_verification",
      "file_path": "org/daily-help/pvc_lakshmi.pdf",
      "file_name": "PVC_Lakshmi.pdf"
    }
  ]
}
```

**Service steps:**

1. Validate project access + RBAC.
1. Validate `category_id` belongs to project and is `active`.
1. Generate unique 4-digit `gate_passcode` for project.
1. Insert `daily_help_profiles` (`status = active`, `open_to_work` defaults to `true`, `display_name` built from name parts).
1. Insert `daily_help_documents` rows.
1. Call `PassesService.create_daily_help_pass(...)`:
   - `pass_type = daily_help`, `validity_type = recurring`
   - Copy guest name / phone / photo from profile
   - `code = gate_passcode`
1. Set `linked_pass_id` on profile.
1. Append `daily_help_events`: `created`, `pass_issued`.

**Response (summary):**

```json
{
  "id": "profile-uuid",
  "display_name": "Mrs. Lakshmi Devi",
  "category_id": "category-uuid",
  "category_name": "Maid",
  "status": "active",
  "gate_passcode": "4821",
  "document_count": 3,
  "created_at": "2026-06-20T10:00:00Z",
  "created_by_name": "Nitin Jangir"
}
```

### 4.2 List + summary

```http
GET /v1/projects/{project_id}/daily-help/summary
GET /v1/projects/{project_id}/daily-help?status=active&category_id={uuid}&search=lakshmi&page=1&limit=20
```

List row shape (matches admin table):

| Field                    | Source                       |
| ------------------------ | ---------------------------- |
| `display_name`, `gender` | profile                      |
| `category_name`          | join `daily_help_categories` |
| `phone`                  | profile (formatted)          |
| `document_count`         | count documents              |
| `created_on`             | `created_at`                 |
| `status`                 | profile                      |

### 4.3 Detail

```http
GET /v1/projects/{project_id}/daily-help/{id}
```

Returns full name breakdown, contacts, category, gender, DOB, status, documents with download paths,
`gate_passcode`, `linked_pass_id`, audit (`created_by`, `events[]`).

### 4.4 Update / status changes

```http
PATCH /v1/projects/{project_id}/daily-help/{id}
POST /v1/projects/{project_id}/daily-help/{id}/deactivate
POST /v1/projects/{project_id}/daily-help/{id}/reactivate
POST /v1/projects/{project_id}/daily-help/{id}/delete
```

On PATCH identity/photo/phone: **sync linked pass guest snapshot** in the same transaction.

### 4.5 Export

```http
GET /v1/projects/{project_id}/daily-help/export?status=active&format=csv
```

Same filters as list; columns aligned with admin table.

______________________________________________________________________

## 5. Gate + Activities flow (existing — extended)

Daily help **does not add new gate endpoints**. Security continues to use:

```http
POST /v1/passes/verify          { "code": "4821" }
POST /v1/passes/{pass_id}/check-in
POST /v1/passes/{pass_id}/check-out
```

Verify response should include `pass_type: "daily_help"` and optional `daily_help_profile` summary
(photo, category) for guard UI.

**Visitor logs / Activities:**

```http
GET /v1/visitor-logs?pass_type=daily_help&date=2026-08-07
GET /v1/visitor-logs/overview   → extend with daily_help / inside counts (optional Phase 1b)
```

Detail `GET /v1/visitor-logs/{pass_id}` joins profile + category when `passes.daily_help_id` is set.

### Check-in/out push notifications (Phase 2)

Daily help passes have **`unit_id = NULL`**, so the existing `_notify_household_pass_event` in
`PassVerificationService` does **not** apply. Instead, after a successful check-in or check-out on a
pass with `daily_help_id`:

```text
PassVerificationService.check_in / check_out
  └── DailyHelpNotificationService.notify_linked_unit_holders(...)
        1. Load active daily_help_household_links for profile
        2. For each unit_id → contact_roles WHERE role_type IN ('Owner','Tenant') AND status = 'active'
        3. Push via PushNotificationDispatcher.send_to_user (respect push preferences)
```

| Event     | Message key                                 |
| --------- | ------------------------------------------- |
| Check-in  | `notifications.push.daily_help.checked_in`  |
| Check-out | `notifications.push.daily_help.checked_out` |

**Recipient rules:**

- Only **Owner** and **Tenant** currently holding the linked unit ([ADR 0010](./adr/0010-contact-roles.md)).
- **Not** Family / Guest / other `contact_units` members.
- Contact must have linked Supabase `user_id` (portal user with registered push device).
- If no active household links → **no notifications**.
- Idempotency key: `daily_help:{profile_id}:{pass_event_id}:checked_in|checked_out`.

See [push-notifications-flow.md](./push-notifications-flow.md) and [ADR 0009](./adr/0009-push-notifications-grpc.md).

### Derived aggregates for resident category cards (Phase 2)

| Stat             | Derivation                                                                                |
| ---------------- | ----------------------------------------------------------------------------------------- |
| **Inside**       | Profiles with linked pass `visit_status = inside` (from latest check-in without checkout) |
| **Open to work** | `daily_help_profiles.open_to_work = true` AND `status = active`                           |
| **Newly added**  | `created_at >= now() - interval '30 days'` (configurable)                                 |
| **House count**  | Count active `daily_help_household_links` per profile                                     |

______________________________________________________________________

## 6. Resident flow (Phase 2)

### 6.0 Category home

```http
GET /v1/daily-help/categories?unit_id={unit_id}
```

Each category includes footer stats and up to **four** `preview_profiles` with `id`, `display_name`,
`photo_path`, `initials`, and formatted `phone`.

### 6.1 Browse directory

```http
GET /v1/daily-help?unit_id={unit_id}&category_id={uuid}
```

Only `status = active` profiles. Phone is returned in full on list and category preview responses.
Profile detail may still mask phone unless the viewer's **selected unit** has an active household
link to that profile.

### 6.1b Profile detail (resident)

```http
GET /v1/daily-help/{profile_id}?unit_id={unit_id}
```

Resident detail omits admin-only fields (`organization_id`, `created_by_user_id`, `created_by_name`,
`updated_at`, `deleted_at`) and does **not** include the admin audit `events[]` timeline. Only
**active** `household_links` are returned.

### 6.2 Add to Household

```http
POST /v1/daily-help/{profile_id}/household-links?unit_id={unit_id}
```

**Service steps:**

1. Resolve contact from JWT.
1. Assert active `contact_units` for `unit_id` (query param).
1. Insert `daily_help_household_links` (`status = active`, `started_at = now()`).
1. Append event (optional): `household_linked` (extend enum in Phase 2).

Profile detail then shows **Works in N houses** from active links + tenure from `started_at`.

### 6.3 Remove link

```http
DELETE /v1/daily-help/{profile_id}/household-links/{link_id}
```

Sets `status = removed`, `removed_at = now()`.

### 6.4 Toggle open to work

```http
PATCH /v1/daily-help/{profile_id}/open-to-work?unit_id={unit_id}
{ "open_to_work": true }
```

Requires an **active household link** between the caller's unit and the profile. Updates the
project-wide `open_to_work` flag shown on category cards and directory badges.

______________________________________________________________________

## 7. Business rules & gating

| Rule                                 | Where                                                              |
| ------------------------------------ | ------------------------------------------------------------------ |
| Staff project access                 | `ensure_staff_project_access(project_id)` on admin routes          |
| Resident unit access                 | `_assert_contact_on_unit(contact_id, unit_id)` for household links |
| Unique passcode per project          | DB unique index + retry on conflict in service                     |
| Pass cancelled when inactive/deleted | `DailyHelpService._sync_pass_status`                               |
| Edit blocked when deleted            | PATCH → `409` unless restoring first                               |
| Document limits                      | Recommend max 10 `other` docs; photo 1 primary on profile          |
| Notification targets                 | Owner + Tenant on linked units only; via `contact_roles`           |
| No links → no push                   | Skip notification when profile has zero active household links     |

### RBAC (proposed)

| Code                           | Use                                                  |
| ------------------------------ | ---------------------------------------------------- |
| `daily_help_management.view`   | List, detail, export, summary                        |
| `daily_help_management.create` | Create, upload documents                             |
| `daily_help_management.update` | Edit, deactivate, delete, restore, manage categories |

Alternatively map to existing `contacts_management.*` if product prefers fewer permission codes.

______________________________________________________________________

## 8. Cross-cutting conventions

- Response envelope: `success_response` / `list_response` (same as visitor logs, tenant requests).
- i18n keys: `daily_help.errors.*`, `daily_help.messages.*`.
- Audit: `@audit_api_call` on mutating admin routes.
- Storage: signed upload URLs follow vehicles / tenant-requests pattern (Phase 1b).
- **Never** set `portal_access` or create auth identities for daily help persons.

______________________________________________________________________

## 9. How to make common changes

| I want to…                       | Change here                                                                     |
| -------------------------------- | ------------------------------------------------------------------------------- |
| Add a category                   | `POST .../daily-help/categories` — no migration needed                          |
| Deactivate a category            | `PATCH .../categories/{id}` `status=inactive`                                   |
| Change passcode length           | `DailyHelpService._generate_passcode` + passes validation                       |
| Change notification recipients   | `daily_help_notification_service.py` + `ContactsRepository` role query          |
| Show flat on visitor log row     | Join latest household link or check-in metadata in `visitor_logs_repository`    |
| Add overview card                | `visitor_logs_repository.get_overview` + schema                                 |
| Add rating / traits              | New tables in Phase 3 + resident POST endpoint                                  |
| Mask phone in profile detail     | `DailyHelpService.get_resident_detail` (`mask_phone` when not household-linked) |
| Backfill legacy `service` passes | One-off migration script linking by phone match                                 |

______________________________________________________________________

## 10. Error keys

| Key                                          | When                                       |
| -------------------------------------------- | ------------------------------------------ |
| `daily_help.errors.not_found`                | Unknown profile id                         |
| `daily_help.errors.already_deleted`          | Mutate deleted row                         |
| `daily_help.errors.passcode_conflict`        | Rare passcode collision                    |
| `daily_help.errors.invalid_category`         | Unknown or inactive `category_id`          |
| `daily_help.errors.category_in_use`          | Delete category referenced by profiles     |
| `daily_help.errors.duplicate_category_name`  | Name already exists in project             |
| `daily_help.errors.unit_not_accessible`      | Resident link to unit they don't belong to |
| `daily_help.errors.duplicate_household_link` | Active link already exists for unit        |

______________________________________________________________________

## 11. Implementation phases

### Phase 1 — Admin registry + categories + pass linkage

- [ ] Migrations: enums, **categories**, profiles, documents, events, `passes.daily_help_id`, `pass_type` value
- [ ] Category CRUD + list for admin dropdown / resident home
- [ ] `DailyHelpRepository` + `DailyHelpService`
- [ ] Admin CRUD, summary, list filters, deactivate/delete
- [ ] Auto-issue recurring pass on create; cancel on deactivate/delete
- [ ] Unit tests for service + repository

### Phase 1b — Hardening

- [ ] Signed upload URLs for photo/documents
- [ ] Export CSV
- [ ] Visitor logs: filter by `pass_type=daily_help`, join profile + category on detail
- [ ] Gate verify payload includes daily help summary

### Phase 2 — Resident directory + household + notifications

- [ ] `daily_help_household_links` migration
- [ ] Resident list/search/profile endpoints
- [ ] Add/remove household link
- [ ] **Check-in/out push to Owner + Tenant on linked units** (`DailyHelpNotificationService`)
- [ ] Category aggregates (inside, open to work, newly added)
- [ ] Integration tests

### Phase 3 — Engagement

- [ ] Ratings + trait tags
- [ ] Attendance summary from pass events
- [ ] `daily_help_availability_slots`
- [ ] `open_to_work` toggle

______________________________________________________________________

## 12. Tests

**Unit**

- `tests/unit/test_daily_help_service.py` — create issues pass, category validation, deactivate cancels pass,
  soft delete, passcode uniqueness, household link rules (Phase 2).
- `tests/unit/test_daily_help_notification_service.py` — Owner/Tenant recipient resolution, dedupe, no-op when no links.
- `tests/unit/test_daily_help_repository.py` — summary counts, list query.

**Integration**

- `tests/integration/daily_help/test_daily_help_admin_api.py`
- `tests/integration/daily_help/test_daily_help_resident_api.py` (Phase 2)

Run:

```bash
.venv/bin/python -m pytest apps/user_service/tests/unit/test_daily_help_service.py -q
```

______________________________________________________________________

## Related

- [ADR 0013 — Daily Help](./adr/0013-daily-help.md)
- [ADR 0003 — Visitor passes](./adr/0003-visitor-passes.md)
- [ADR 0004 — Pass validation & visitor logs](./adr/0004-pass-validation-gate.md)
- [passes-validation-flow.md](./passes-validation-flow.md) — Activities / visitor logs
- [passes-flow.md](./passes-flow.md) — resident pass creation (distinct from admin registry)
- [tenant-requests-flow.md](./tenant-requests-flow.md) — reference for admin list + documents pattern
- [ADR 0009 — Push notifications](./adr/0009-push-notifications-grpc.md)
- [push-notifications-flow.md](./push-notifications-flow.md)
- [ADR 0010 — Contact roles](./adr/0010-contact-roles.md) — Owner/Tenant resolution for notifications
- [walk-in-flow.md](./walk-in-flow.md) — separate gate flow for unannounced visitors
