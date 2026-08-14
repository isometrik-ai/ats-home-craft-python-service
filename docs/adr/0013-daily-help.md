# ADR 0013: Daily Help — project registry, household links, gate integration

|                  |                                                                                                                                                                                                                                                                        |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Accepted — implemented in `user_service` (Phases 1–3 core)                                                                                                                                                                                                             |
| **Date**         | 2026-08-11                                                                                                                                                                                                                                                             |
| **Authors**      | Home Craft platform team                                                                                                                                                                                                                                               |
| **Depends on**   | [ADR 0003](./0003-visitor-passes.md), [ADR 0004](./0004-pass-validation-gate.md), [ADR 0008](./0008-walk-in-entries.md), [ADR 0009](./0009-push-notifications-grpc.md), [ADR 0010](./0010-contact-roles.md), [ADR 0011](./0011-project-membership.md) (project access) |
| **Related docs** | [daily-help-flow.md](../daily-help-flow.md), [passes-validation-flow.md](../passes-validation-flow.md), [passes-flow.md](../passes-flow.md), [push-notifications-flow.md](../push-notifications-flow.md)                                                               |
| **Migrations**   | `20260811120000_daily_help_enums.sql`, `20260811121000_daily_help_tables.sql`, `20260811121500_daily_help_categories.sql`, `20260811122000_passes_daily_help_link.sql`, `20260814160000_daily_help_attendance_absences.sql` (`ats-home-craft-supabase`)                |

______________________________________________________________________

## Context

Communities maintain a **registry of everyday household help** — maids, cooks, drivers, milk/newspaper
delivery, and similar recurring service providers. Product UI spans:

1. **Admin dashboard — Requests → Daily Help** — create, list, filter, export, mark inactive, soft-delete.
   Documents are stored for reference; **no admin verification workflow** (unlike tenant requests).
1. **Resident mobile — Daily Help directory** — browse by category, search, open profile, **Add to Household**.
1. **Resident mobile — Activities** — this is the existing **Visitor Logs / gate check-in** surface
   (passes + walk-ins). Daily help entries appear there when the person checks in at the gate.

### Product decisions (confirmed from screens)

| #   | Decision                                                                                                                                                                                                                                  |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Daily help is not a `contacts` row and not an auth user.** Identity lives on dedicated registry tables only.                                                                                                                            |
| 2   | **Admin / project staff** create and maintain records for a **project** — no resident submission or approval queue.                                                                                                                       |
| 3   | **Documents on file only** — photo, ID proof, police verification, and ad-hoc uploads. No verify/reject step.                                                                                                                             |
| 4   | **Status:** `active`, `inactive`, `deleted` (soft delete). Deleted rows remain for audit; list tabs show counts.                                                                                                                          |
| 5   | **Categories are admin-maintained per project** — not a global Postgres enum. Each project defines its own category list (Maid, Cook, …).                                                                                                 |
| 6   | **Gate / Activities** reuse the existing **pass check-in/out + visitor logs** pipeline — not a third parallel entry system.                                                                                                               |
| 7   | Each profile gets a **project-scoped gate passcode** (searchable in the app) backed by a **recurring pass** (`pass_type = daily_help`).                                                                                                   |
| 8   | **Add to Household** links a daily help profile to a **unit** (and the linking resident) without creating a contact for the helper.                                                                                                       |
| 9   | **Check-in/out notifications:** when a daily help person enters or exits at the gate, send push to **Owner and Tenant currently holding each linked unit** (via active `daily_help_household_links` + `contact_roles`).                   |
| 10  | **Ratings, attendance calendar, availability slots, “open to work”** — implemented in Phase 3. Ratings: one row per `(profile, unit, rater)` with trait tags. Attendance: gate check-ins merged with resident-reported absences per unit. |

### Screens (product)

**Admin dashboard — Daily Help list**

| Element         | Capability                                                                 |
| --------------- | -------------------------------------------------------------------------- |
| Summary cards   | Total, Active, Inactive, Deleted                                           |
| Status tabs     | All / Active / Inactive / Deleted                                          |
| Category filter | Dropdown (All + **project categories**)                                    |
| Search          | Name or contact number                                                     |
| Table columns   | Name (+ gender), Category, Contact, Documents, Created on, Status, Actions |
| Add             | Side drawer form                                                           |
| Row actions     | View, Edit, Mark inactive, Delete record                                   |
| Export          | CSV/XLS of filtered list                                                   |

**Admin — Add / Edit Daily Help**

| Field                      | Required              | Notes                                                         |
| -------------------------- | --------------------- | ------------------------------------------------------------- |
| Initials                   | No                    | Mr. / Mrs. / Ms.                                              |
| First / middle / last name | First + last required | Display name built in service                                 |
| Contact no.                | Yes                   | ISD + number                                                  |
| Alternate contact          | No                    | ISD + number                                                  |
| Category                   | Yes                   | **`category_id`** — must reference an active project category |
| Gender                     | No                    | Male / Female / Other                                         |
| Date of birth              | No                    |                                                               |
| Photo                      | No                    | Storage path; used at gate recognition                        |
| Documents                  | No                    | Typed slots + free-form uploads                               |

**Resident mobile — Daily Help home**

| Element               | Meaning                                                          |
| --------------------- | ---------------------------------------------------------------- |
| Category sections     | Maids, Cooks, Car Cleaners, Drivers, …                           |
| Profile card          | Photo, name, house count, rating (Phase 2), “Open to work” badge |
| Category footer stats | Inside / Open to work / Newly added (aggregates)                 |
| Search                | Name, mobile, **gate passcode**                                  |

**Resident mobile — Profile + Activities**

| Screen     | Notes                                                                                                      |
| ---------- | ---------------------------------------------------------------------------------------------------------- |
| Profile    | Attendance calendar, ratings (view/update), availability, “Works in N houses”, tenure — resident APIs live |
| Activities | **Existing visitor logs UI** — daily help rows come from `pass_type = daily_help` passes                   |

### Constraints (carried from existing flows)

- Multi-tenancy via **`organization_id`** on every new table and query.
- **Staff actor** = `organization_member` with project access ([ADR 0011](./0011-project-membership.md))
  and RBAC (proposed `daily_help_management.*` or reuse `contacts_management.*` / `projects_management.*`).
- **Resident actor** = `contacts` via `extract_onboarding_contact_context()` for directory + household links.
- Reuse **`projects`**, **`units`**, **`passes`**, **`pass_events`**, **`visitor_logs`** read model.
- Photos/documents store **storage paths only** — no blobs in Postgres.
- RLS enabled, **policies deferred** (backend `service_role`), matching earlier phases.
- **Do not** call `ContactsService.create_contact` or provision auth for daily help persons.

### Relationship to existing `pass_type = service`

[ADR 0003](./0003-visitor-passes.md) documents `service` as a loose proxy for “daily help / maintenance”.
[ADR 0004 §8](./0004-pass-validation-gate.md) proposed adding `daily_help` to `pass_type`. This ADR
**supersedes that proxy** for first-class daily help: new profiles use `pass_type = daily_help` and
`passes.daily_help_id`. Legacy `service` passes remain valid; migration/mapping is optional follow-up.

______________________________________________________________________

## Decision

### 1. Dedicated registry — do not use `contacts` or auth users

Daily help persons are **managed resources**, like vendor directory entries, not platform users.
Their name, phone, photo, and documents are stored on **`daily_help_profiles`** (+ documents child table).

> **Why not `ContactType.Staff` / Vendor:** [ADR 0010](./0010-contact-roles.md) contact roles assume a
> `contacts` row and optional portal access. Daily help explicitly must **not** create contacts or auth
> identities. A separate domain keeps onboarding, household invites, and RBAC unchanged.

### 2. Five new core tables (Phase 1) + one link table (Phase 2)

| Table                                | Phase | Purpose                                                                       |
| ------------------------------------ | ----- | ----------------------------------------------------------------------------- |
| **`daily_help_categories`**          | 1     | **Project-scoped category catalog** — admin-maintained labels (Maid, Cook, …) |
| **`daily_help_profiles`**            | 1     | Project-scoped person registry: identity snapshot, category FK, status        |
| **`daily_help_documents`**           | 1     | Photo, ID proof, police verification, and other file metadata                 |
| **`daily_help_events`**              | 1     | Append-only audit (created, updated, status_changed, deleted, restored)       |
| **`daily_help_household_links`**     | 2     | Resident “Add to Household” — profile ↔ unit, with linker contact             |
| **`daily_help_availability_slots`**  | 3     | Optional free-time windows (morning/evening)                                  |
| **`daily_help_ratings`**             | 3     | Star rating + trait tags; unique per `(profile, unit, rated_by_contact_id)`   |
| **`daily_help_rating_traits`**       | 3     | Trait enum tags attached to each rating row                                   |
| **`daily_help_attendance_absences`** | 3     | Resident-reported absence per `(profile, unit, attendance_date)`              |

Gate movement and **Activities** do **not** get new tables — they flow through **`passes` + `pass_events`**
and the existing **visitor logs** union ([ADR 0004](./0004-pass-validation-gate.md)).

### 3. Link to recurring pass for gate + visitor logs

On **create** (and on re-activate from inactive/deleted when product requires):

1. Insert `daily_help_profiles` with generated **`gate_passcode`** (unique per `(organization_id, project_id)`).
1. Insert **`passes`** row:
   - `pass_type = daily_help`
   - `validity_type = recurring`
   - `daily_help_id` → profile PK
   - Guest snapshot columns copied from profile (name, phone, photo path)
   - `code` = same as `gate_passcode` (4-digit, scoped like ADR 0003)
   - `unit_id` = NULL at project level (household links are informational; gate pass is project-wide)
   - `created_by_contact_id` = NULL; `created_by_user_id` = admin staff user

Security **verify / check-in / check-out** uses existing gate endpoints. Visitor logs list/detail
already support pass rows; extend filters and overview cards for `daily_help`.

> **Why a pass instead of a bespoke gate table:** Activities UI, guard columns, IN/OUT, time spent,
> and exports already exist in visitor logs. Duplicating enter/exit would fork gate logic from
> [ADR 0004](./0004-pass-validation-gate.md) and [ADR 0008](./0008-walk-in-entries.md).

### 4. Status lifecycle

#### Profile — `daily_help_status`

| Status     | Meaning                                      |
| ---------- | -------------------------------------------- |
| `active`   | Visible in directory; recurring pass usable  |
| `inactive` | Hidden from resident directory; pass blocked |
| `deleted`  | Soft-deleted; audit retained; pass cancelled |

```text
admin create ──► active
active ──admin mark inactive──► inactive ──admin reactivate──► active
active|inactive ──admin delete──► deleted
deleted ──admin restore (optional)──► inactive (default) or active
```

When status → `inactive` or `deleted`, service sets linked pass `status = cancelled` (or equivalent).
Re-activate may re-issue pass if missing.

### 5. Household links (Phase 2)

`daily_help_household_links` connects a profile to a **unit**:

- **`linked_by_contact_id`** — resident who tapped “Add to Household”
- **`status`** — `active` | `removed`
- **`started_at`** — drives “Working in your society for N months” / per-unit tenure in UI

**Rules:**

- Resident must have an **active `contact_units`** link to the unit.
- Multiple units per profile allowed (multi-household maid).
- Removing a link sets `status = removed`; history retained.
- **Does not** create passes per unit — gate pass remains one recurring pass per profile.

### 6. Project-scoped categories (admin-maintained)

Categories are **not** a global Postgres enum. Each **project** maintains its own list in
**`daily_help_categories`** so communities can define labels relevant to them (e.g. one project
has “Car Cleaner”, another has “Newspaper” only).

**Rules:**

- Admin CRUD under `/v1/projects/{project_id}/daily-help/categories`.
- **`daily_help_profiles.category_id`** FK → `daily_help_categories.id` (required on create).
- Category **`name`** unique per `(organization_id, project_id)` (case-insensitive).
- Category **`status`**: `active` | `inactive`. Inactive categories hidden from new profile forms
  but retained on existing profiles and in history.
- **Delete blocked** when any profile references the category; admin must reassign or deactivate instead.
- Optional **seed set** on project creation (Maid, Cook, Driver, …) — service helper, not hard-coded enum.

> **Why not enum:** product screenshots show a dropdown, but different projects need different
> category sets. Admin-maintained rows match notice-board categories / fee heads pattern better than
> migrations per new label.

### 7. Check-in/out push notifications (Phase 2+)

When security successfully **check-in** or **check-out** a pass where **`passes.daily_help_id` IS NOT NULL**,
notify residents linked to that helper:

1. Load **active** `daily_help_household_links` for the profile.
1. For **each linked `unit_id`**, resolve contacts with an **active `contact_roles`** row where
   **`role_type IN ('Owner', 'Tenant')`** and **`unit_id`** matches ([ADR 0010](./0010-contact-roles.md)).
1. Send push only to contacts with a linked Supabase **`user_id`** and push enabled
   ([ADR 0009](./0009-push-notifications-grpc.md), [push-notifications-flow.md](../push-notifications-flow.md)).
1. **Do not** notify Family / Guest members on the unit — only current Owner and Tenant holders.
1. **Dedupe** recipients by `user_id` within one gate event (same person on two linked units still gets
   one notification per unit if both units are linked).

**Hook location:** extend `PassVerificationService.check_in` / `check_out` — after a successful
`daily_help` pass event, call `DailyHelpNotificationService.notify_linked_unit_holders(...)` instead
of `_notify_household_pass_event` (which requires `passes.unit_id` and notifies all `contact_units`).

Regular guest passes keep existing `_notify_household_pass_event` behaviour unchanged.

**Message keys (proposed):**

| Event     | Key                                         |
| --------- | ------------------------------------------- |
| Check-in  | `notifications.push.daily_help.checked_in`  |
| Check-out | `notifications.push.daily_help.checked_out` |

**Payload params:** `helper_name`, `category_name`, `unit_label` (when single unit; omit or list when many).

If **no active household links** exist, **no notifications** are sent (profile is registered but not
linked to any flat yet).

### 8. RBAC and API split

| Actor    | Prefix                                 | Auth                                       |
| -------- | -------------------------------------- | ------------------------------------------ |
| Admin    | `/v1/projects/{project_id}/daily-help` | Staff RBAC + `ensure_staff_project_access` |
| Resident | `/v1/daily-help`                       | `extract_onboarding_contact_context()`     |

Resident routes cover directory, profile, household links, **open-to-work**, **ratings** (create /
view mine / update / summary), and **attendance** (monthly calendar + mark absent). Admin routes cover
CRUD, category management, status changes, document upload metadata, export, summary, availability,
and gate check-in attendance calendar.

### 9. Visitor logs / Activities alignment

| App “Activities” need            | Source                                                                          |
| -------------------------------- | ------------------------------------------------------------------------------- |
| Name, photo, category            | Join `passes.daily_help_id` → `daily_help_profiles`                             |
| INSIDE / LEFT                    | Existing `VisitorLogVisitStatus` from pass events                               |
| “Entered N times”                | Count `checked_in` events on linked pass                                        |
| Rate now / Attendance / Gatepass | Resident rating CRUD + monthly attendance calendar; gatepass = linked pass code |
| Deliveries mixed in same feed    | Unchanged — visitor logs union (pass + walk-in)                                 |

Filter visitor logs by `pass_type = daily_help` (and later `visitor_type` mapping in service layer).

______________________________________________________________________

## Schema (proposed)

### Enums

```sql
CREATE TYPE public.daily_help_status AS ENUM (
  'active',
  'inactive',
  'deleted'
);

CREATE TYPE public.daily_help_category_status AS ENUM (
  'active',
  'inactive'
);

CREATE TYPE public.daily_help_document_type AS ENUM (
  'photo',
  'id_proof',
  'police_verification',
  'other'
);

CREATE TYPE public.daily_help_event_type AS ENUM (
  'created',
  'updated',
  'status_changed',
  'document_added',
  'document_removed',
  'pass_issued',
  'pass_cancelled',
  'deleted',
  'restored',
  'household_linked',
  'household_removed',
  'attendance_marked_absent'
);

CREATE TYPE public.daily_help_actor_type AS ENUM (
  'staff',
  'resident',
  'system'
);

CREATE TYPE public.daily_help_household_link_status AS ENUM (
  'active',
  'removed'
);
```

### Extend existing pass enum + table

```sql
ALTER TYPE public.pass_type ADD VALUE IF NOT EXISTS 'daily_help';

ALTER TABLE public.passes
  ADD COLUMN IF NOT EXISTS daily_help_id uuid REFERENCES public.daily_help_profiles(id);

-- Relax NOT NULL on contact/unit columns for project-level daily help passes.
ALTER TABLE public.passes
  ADD COLUMN IF NOT EXISTS created_by_user_id uuid,
  ALTER COLUMN unit_id DROP NOT NULL,
  ALTER COLUMN host_contact_id DROP NOT NULL,
  ALTER COLUMN created_by_contact_id DROP NOT NULL;

ALTER TABLE public.passes
  ADD CONSTRAINT passes_daily_help_or_resident_pass_chk CHECK (
    (daily_help_id IS NOT NULL AND pass_type = 'daily_help'::public.pass_type
     AND unit_id IS NULL AND host_contact_id IS NULL AND created_by_contact_id IS NULL
     AND created_by_user_id IS NOT NULL)
    OR (daily_help_id IS NULL AND pass_type <> 'daily_help'::public.pass_type
        AND unit_id IS NOT NULL AND host_contact_id IS NOT NULL AND created_by_contact_id IS NOT NULL)
  );

CREATE INDEX IF NOT EXISTS idx_passes_daily_help_id
  ON public.passes (daily_help_id)
  WHERE daily_help_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_passes_daily_help_id_active
  ON public.passes (daily_help_id)
  WHERE daily_help_id IS NOT NULL AND status NOT IN ('cancelled', 'completed', 'expired');
```

> Migration order: create `daily_help_categories` + `daily_help_profiles` first, then `passes` FK
> migration in a follow-up file in the same release (or single transaction).

### `daily_help_categories`

| Column                      | Type                                | Notes                                       |
| --------------------------- | ----------------------------------- | ------------------------------------------- |
| `id`                        | uuid PK                             |                                             |
| `organization_id`           | uuid FK → organizations             |                                             |
| `project_id`                | uuid FK → projects                  |                                             |
| `name`                      | text NOT NULL                       | Display label, e.g. "Maid", "Milk Delivery" |
| `sort_order`                | integer NOT NULL                    | default 0; admin list / resident home order |
| `status`                    | daily_help_category_status NOT NULL | default `active`                            |
| `created_by_user_id`        | uuid FK → auth.users                |                                             |
| `updated_by_user_id`        | uuid FK → auth.users                |                                             |
| `created_at` / `updated_at` | timestamptz                         |                                             |

**Indexes / constraints:**

- **Unique** `(organization_id, project_id, lower(name))`
- `(organization_id, project_id, status, sort_order)`

### `daily_help_profiles`

| Column                      | Type                                     | Notes                                          |
| --------------------------- | ---------------------------------------- | ---------------------------------------------- |
| `id`                        | uuid PK                                  |                                                |
| `organization_id`           | uuid FK → organizations                  |                                                |
| `project_id`                | uuid FK → projects                       |                                                |
| `initials`                  | text                                     | Mr. / Mrs. / Ms.                               |
| `first_name`                | text NOT NULL                            |                                                |
| `middle_name`               | text                                     |                                                |
| `last_name`                 | text NOT NULL                            |                                                |
| `display_name`              | text NOT NULL                            | denormalized for search/list                   |
| `phone_isd_code`            | text NOT NULL                            |                                                |
| `phone_number`              | text NOT NULL                            |                                                |
| `alternate_phone_isd_code`  | text                                     |                                                |
| `alternate_phone_number`    | text                                     |                                                |
| `category_id`               | uuid FK → daily_help_categories NOT NULL |                                                |
| `gender`                    | text                                     | Male / Female / Other (or separate enum later) |
| `date_of_birth`             | date                                     |                                                |
| `photo_path`                | text                                     | primary face photo for gate/directory          |
| `gate_passcode`             | text NOT NULL                            | 4-digit string; unique per project             |
| `status`                    | daily_help_status NOT NULL               | default `active`                               |
| `open_to_work`              | boolean NOT NULL                         | default false; Phase 2 resident/admin toggle   |
| `linked_pass_id`            | uuid FK → passes                         | recurring gate pass                            |
| `created_by_user_id`        | uuid FK → auth.users                     | admin staff                                    |
| `updated_by_user_id`        | uuid FK → auth.users                     |                                                |
| `deleted_at`                | timestamptz                              | set when status → deleted                      |
| `created_at` / `updated_at` | timestamptz                              |                                                |

**Indexes:**

- `(organization_id, project_id, status)`
- `(organization_id, project_id, category_id)`
- `(organization_id, project_id, lower(display_name))` — search
- `(organization_id, project_id, phone_number)`
- **Unique** `(organization_id, project_id, gate_passcode)`

### `daily_help_documents`

| Column                      | Type                              | Notes                          |
| --------------------------- | --------------------------------- | ------------------------------ |
| `id`                        | uuid PK                           |                                |
| `organization_id`           | uuid FK                           |                                |
| `daily_help_profile_id`     | uuid FK → daily_help_profiles     | ON DELETE CASCADE              |
| `document_type`             | daily_help_document_type NOT NULL |                                |
| `label`                     | text                              | e.g. “Aadhaar”, custom title   |
| `file_path`                 | text NOT NULL                     | storage path                   |
| `file_name`                 | text                              | original filename for download |
| `mime_type`                 | text                              |                                |
| `file_size_bytes`           | bigint                            |                                |
| `sort_order`                | integer NOT NULL                  | default 0                      |
| `uploaded_by_user_id`       | uuid FK → auth.users              |                                |
| `created_at` / `updated_at` | timestamptz                       |                                |

Partial unique: at most one `photo` typed row may duplicate `profiles.photo_path` — service keeps in sync.

### `daily_help_events`

| Column                  | Type                  | Notes                    |
| ----------------------- | --------------------- | ------------------------ |
| `id`                    | uuid PK               |                          |
| `organization_id`       | uuid FK               |                          |
| `daily_help_profile_id` | uuid FK               |                          |
| `event_type`            | daily_help_event_type |                          |
| `actor_type`            | daily_help_actor_type |                          |
| `actor_user_id`         | uuid                  | staff                    |
| `actor_contact_id`      | uuid                  | resident (Phase 2 links) |
| `payload`               | jsonb                 | optional diff/metadata   |
| `occurred_at`           | timestamptz NOT NULL  | default `now()`          |

### `daily_help_household_links` (Phase 2)

| Column                      | Type                             | Notes            |
| --------------------------- | -------------------------------- | ---------------- |
| `id`                        | uuid PK                          |                  |
| `organization_id`           | uuid FK                          |                  |
| `project_id`                | uuid FK                          |                  |
| `daily_help_profile_id`     | uuid FK                          |                  |
| `unit_id`                   | uuid FK → units                  |                  |
| `linked_by_contact_id`      | uuid FK → contacts               |                  |
| `status`                    | daily_help_household_link_status | default `active` |
| `started_at`                | timestamptz NOT NULL             | default `now()`  |
| `removed_at`                | timestamptz                      |                  |
| `created_at` / `updated_at` | timestamptz                      |                  |

**Partial unique:** one `active` link per `(daily_help_profile_id, unit_id)`.

### `daily_help_ratings` (Phase 3)

| Column                      | Type               | Notes                             |
| --------------------------- | ------------------ | --------------------------------- |
| `id`                        | uuid PK            |                                   |
| `organization_id`           | uuid FK            |                                   |
| `project_id`                | uuid FK            |                                   |
| `daily_help_profile_id`     | uuid FK            |                                   |
| `unit_id`                   | uuid FK → units    | Rater's unit context              |
| `rated_by_contact_id`       | uuid FK → contacts | Resident who submitted the rating |
| `stars`                     | numeric(2,1)       | 0.5 – 5.0                         |
| `comment`                   | text               | Optional free text                |
| `created_at` / `updated_at` | timestamptz        |                                   |

**Unique:** `(daily_help_profile_id, unit_id, rated_by_contact_id)` — one rating per resident per unit.

Child table **`daily_help_rating_traits`** stores trait enum values (`very_punctual`, `quite_regular`,
`exceptional_service`, `great_attitude`).

**Resident API:**

- `POST /v1/daily-help/{id}/ratings?unit_id=` — create (409 if duplicate)
- `GET /v1/daily-help/{id}/ratings/mine?unit_id=` — fetch caller's rating
- `PUT /v1/daily-help/{id}/ratings?unit_id=` — update existing
- `GET /v1/daily-help/{id}/ratings/summary?unit_id=` — aggregate average + trait counts

### `daily_help_attendance_absences` (Phase 3)

Migration `20260814160000_daily_help_attendance_absences.sql`.

| Column                  | Type               | Notes                                      |
| ----------------------- | ------------------ | ------------------------------------------ |
| `id`                    | uuid PK            |                                            |
| `organization_id`       | uuid FK            |                                            |
| `project_id`            | uuid FK            |                                            |
| `daily_help_profile_id` | uuid FK            |                                            |
| `unit_id`               | uuid FK → units    | Resident unit reporting the absence        |
| `marked_by_contact_id`  | uuid FK → contacts | Resident who marked absent                 |
| `attendance_date`       | date NOT NULL      | Calendar day (Asia/Kolkata for gate merge) |
| `created_at`            | timestamptz        |                                            |

**Unique:** `(daily_help_profile_id, unit_id, attendance_date)`.

**Attendance model:**

- **Present** — derived from `pass_events` check-ins on the profile's linked pass (society gate; not unit-specific).
- **Absent** — row in this table when a household-linked resident reports the helper did not visit their unit.
- Monthly calendar API merges both sources per day: `present` | `absent` | `null`.

**Resident API:**

- `GET /v1/daily-help/{id}/attendance?unit_id=&year=&month=`
- `POST /v1/daily-help/{id}/attendance/absence?unit_id=` with `{ "attendance_date": "YYYY-MM-DD" }`

Requires active household link. Cannot mark absent on a future date or on a day with a gate check-in.

### `daily_help_availability_slots` (Phase 3)

Admin-managed via `PUT /v1/projects/{project_id}/daily-help/{id}/availability`.

______________________________________________________________________

## Consequences

### Positive

- **Clear separation** from contacts/auth — satisfies product constraint explicitly.
- **Reuses gate + visitor logs** — Activities screen needs no parallel enter/exit backend.
- **Admin registry** matches tenant-requests / notice-board patterns (project-scoped staff CRUD).
- **Household links** enable “Works in N houses” without N passes.
- **Project-specific categories** without enum migrations when a community adds a new service type.
- **Targeted notifications** — only Owner/Tenant holders on linked flats, not all household members.
- **Extensible** — ratings, availability, open-to-work, and attendance absences delivered in Phase 3.

### Negative / trade-offs

- **Two sources of truth for gate identity** — profile snapshot and pass guest snapshot must stay in sync
  on edit (service updates both in one transaction).
- **`passes.unit_id` NULL** for project-level recurring daily help — visitor log “flat” column may be
  blank unless enriched from latest check-in context or primary household link (service-layer display rule).
- **Legacy `service` passes** — existing data may not link to `daily_help_profiles` until backfill.
- **Category referential integrity** — deleting or renaming categories affects list filters and resident home sections; enforce via FK + deactivate pattern.
- **Notification fan-out** — a helper linked to many units may trigger multiple pushes per gate event; dedupe by user per unit only.
- **No RLS policies yet** — backend-only access until follow-up migration.

### Follow-ups

1. Signed upload URLs for photo/documents (Phase 1b remainder).
1. Extend visitor logs overview cards (`daily_help` count, category aggregates).
1. Optional backfill: map historical `pass_type = service` passes to profiles.
1. RLS policies keyed on `organization_id` + staff/resident access.
1. Optional: absence `reason` text column; undo/clear absence API.

______________________________________________________________________

## Alternatives considered

| Alternative                                   | Why rejected                                                                    |
| --------------------------------------------- | ------------------------------------------------------------------------------- |
| Store daily help as `contacts` + `Staff` role | Creates auth/contact lifecycle the product explicitly forbids                   |
| Extend only `pass_type = service`             | No admin registry, documents, or directory; cannot list inactive/deleted help   |
| New `daily_help_entries` gate table           | Duplicates walk-in/pass enter-exit; Activities would need a third union arm     |
| Global Postgres enum for categories           | Cannot vary per project; every new label needs a migration                      |
| `contact_units` only for notification targets | Would include Family/Guest; product requires Owner + Tenant holders only        |
| Single JSONB documents column on profile      | Poor fit for typed downloads (Photo, ID Proof, PVC) and file counts in admin UI |
