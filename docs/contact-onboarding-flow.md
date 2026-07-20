# Contact Onboarding Flow — Context & Change Guide

This document explains the **Contact Onboarding wizard** implemented in `user_service`. It is
written so anyone (developer, reviewer, or product owner) can understand the flow end‑to‑end
and know exactly where to change things.

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **API prefix:** `/v1/contact-onboarding`
- **DB schema:** `ats-home-craft-supabase` (migrations `20260629110000_*` enums, `20260629111000_*` tables)
- **Full column reference:** `ats-home-craft-supabase/docs/resident-onboarding-schema.md`

> Naming note: this feature was renamed from "resident onboarding" to "contact onboarding" in
> the code. The **migration/seed/doc filenames still say `resident_onboarding`** (renaming an
> applied migration is unsafe), but the tables, code, and APIs use "contact" terminology.

______________________________________________________________________

## 1. What this flow does

This is a **self‑service wizard** a contact (resident) completes to move into a unit. Unlike
the project setup wizard (an admin building a project), here the **logged‑in contact acts on
their own onboarding**. The current contact is resolved from the JWT via
`extract_onboarding_contact_context()` — there are **no `PROJECTS_MANAGEMENT_*` RBAC codes**;
authorization is "you can only touch your own onboarding".

Progress is tracked in two places:

- **Family members:** contacts with `contact_type = Family` (household invitees) only receive
  the `complete_profile` step. `GET /status` returns that single step with empty `unit_onboarding`.
  Full owner onboarding (properties, unit steps, review) applies to Owner/Tenant contacts only.

- **`contact_onboarding_steps`** — contact-level steps: profile, property selection, default unit, review.

- **`contact_unit_onboarding_steps`** — per confirmed unit: `vehicles` and `household` (migration `20260717150000_*`).

When all required contact-level steps are `completed`/`skipped`, every confirmed unit has
`vehicles`/`household` terminal, and prerequisites pass, onboarding is finalized and unit
links receive `activated_at`.

### Wizard steps (order matters)

Enum: `ContactOnboardingStep` in `apps/user_service/app/schemas/enums.py`.

**Contact-level (once per contact):**

| #   | Step key            | Required?                  | Purpose                                                |
| --- | ------------------- | -------------------------- | ------------------------------------------------------ |
| 1   | `complete_profile`  | required                   | Fill contact profile (name, DOB, gender, phones, etc.) |
| 2   | `select_properties` | required                   | Confirm which pre‑allotted units the contact accepts   |
| 3   | `choose_unit`       | required (only if >1 unit) | Pick default login unit (auto-completed when 1 unit)   |
| 4   | `review`            | required                   | Final review → completes onboarding                    |

**Per confirmed unit** (stored in `contact_unit_onboarding_steps`):

| Step key    | Required?     | Purpose                                   |
| ----------- | ------------- | ----------------------------------------- |
| `vehicles`  | **skippable** | Register vehicles for that unit (or skip) |
| `household` | **skippable** | Add family members to that unit (or skip) |

Only unit-level `vehicles` and `household` may be skipped (`skip_step` requires `contact_unit_id`).
The "current step" is derived on the fly via `_derive_navigation` in `contact_onboarding_service.py`:
profile → properties → **each unit's vehicles then household** → choose unit (if needed) → review.
`GET /status` returns `setup_current_step` and `current_contact_unit_id` when on a unit step.

> **Multiple properties:** see [§6 Multi-property onboarding](#6-multi-property-onboarding) for
> how steps 1, 3–5 behave when a contact has more than one pre‑allotted unit.
>
> **Admin assigns a unit later:** see [§7 Post-onboarding property assignment](#7-post-onboarding-property-assignment).

______________________________________________________________________

## 2. Architecture (layers)

Same 3‑layer FastAPI pattern as the rest of the service:

```
HTTP → API router → Service (business rules) → Repository (SQL) → Postgres
```

### File map

| Concern                            | File                                                                |
| ---------------------------------- | ------------------------------------------------------------------- |
| API endpoints                      | `app/api/contact_onboarding.py`                                     |
| Route registration                 | `app/api/routes.py` (`contact_onboarding_router`)                   |
| Wizard orchestration / step‑gating | `app/services/contact_onboarding_service.py`                        |
| Contact CRUD (reused)              | `app/services/contacts_service.py`                                  |
| Contact↔unit links                 | `app/services/contact_units_service.py`                             |
| Vehicles                           | `app/services/vehicles_service.py`                                  |
| Step persistence                   | `app/db/repositories/contact_onboarding_repository.py`              |
| Unit step persistence              | `app/db/repositories/contact_unit_onboarding_repository.py`         |
| Unit links persistence             | `app/db/repositories/contact_units_repository.py`                   |
| Vehicles persistence               | `app/db/repositories/vehicles_repository.py`                        |
| Contacts persistence               | `app/db/repositories/contacts_repository.py`                        |
| Request/response models            | `app/schemas/contact_onboarding.py`                                 |
| Enums (mirror Postgres)            | `app/schemas/enums.py`                                              |
| Contact context resolver           | `extract_onboarding_contact_context` in `app/utils/common_utils.py` |
| i18n messages                      | `app/locales/en.json` (`contact_onboarding.*`)                      |

The onboarding service **composes** other services (`ContactsService`, `ContactUnitsService`,
`VehiclesService`) rather than duplicating their logic.

______________________________________________________________________

## 3. Data model

Contact onboarding reuses the existing `contacts` table and adds onboarding tables
(`20260629111000_resident_onboarding_tables.sql`, `20260629113000_household_invitations.sql`).
All carry `organization_id`.

| Table                           | Purpose                                                                        |
| ------------------------------- | ------------------------------------------------------------------------------ |
| `contacts` (existing)           | The person being onboarded (and family members)                                |
| `contact_units`                 | Links a contact to a unit (status, is_primary, is_default_login, relationship) |
| `vehicles`                      | Vehicles registered by the contact for a unit                                  |
| `household_invitations`         | Phone-based SMS invites for portal-access family members (`20260629113000_*`)  |
| `contact_onboarding_steps`      | Per‑contact wizard step status (profile, properties, choose_unit, review)      |
| `contact_unit_onboarding_steps` | Per‑unit wizard step status (`vehicles`, `household`)                          |

Key enums: `ContactOnboardingStep`, `ContactUnitStatus` (`pending`/`active`/`moved_out`),
`ContactUnitRelationship`, `VehicleType` (`two_wheeler`/`four_wheeler`),
`VehicleFuelType` (`non_ev`/`ev` — UI label: Non EV / EV Vehicle),
`VehicleStatus` (`pending`/`approved`/`rejected`/`removed`), `SetupStepStatus`,
`HouseholdInvitationStatus`, `HouseholdMemberStatus`.

### `vehicles` columns (contact-facing)

| Column                   | Type        | Notes                                                           |
| ------------------------ | ----------- | --------------------------------------------------------------- |
| `unit_id`                | uuid FK     | Must be a unit actively assigned to the contact                 |
| `vehicle_type`           | enum        | `two_wheeler`, `four_wheeler`                                   |
| `registration_number`    | text        | Unique per project among active vehicles (`deleted_at IS NULL`) |
| `make`, `model`, `color` | text        | Optional                                                        |
| `photo_paths`            | text[]      | Storage paths only (max 10 per vehicle); not raw blobs          |
| `fuel_type`              | enum        | Optional on create; `non_ev`, `ev` (UI: Non EV / EV Vehicle)    |
| `status`                 | enum        | `pending`, `approved`, `rejected`, `removed`                    |
| `status_updated_at`      | timestamptz | Set whenever `status` changes                                   |
| `deleted_at`             | timestamptz | Set on soft-remove; row retained for audit                      |
| `rejection_reason`       | text        | Set by admin when `status = rejected`                           |
| `parking_slot_id`        | uuid FK     | Set by admin on approve; links to `facility_parking_slots`      |

Media/files (profile photo, vehicle images) store **paths only** — no raw blobs in Postgres.

______________________________________________________________________

## 4. API catalog

All routes under `/v1/contact-onboarding`. The acting contact is resolved from the JWT, so
most endpoints take **no** contact id in the path.

| Method | Path                                                                   | Step / purpose                                                    |
| ------ | ---------------------------------------------------------------------- | ----------------------------------------------------------------- |
| GET    | `/v1/contact-onboarding/status`                                        | Wizard progress + `current_contact_unit_id`                       |
| GET    | `/v1/contact-onboarding/properties`                                    | List pre‑allotted units to confirm                                |
| POST   | `/v1/contact-onboarding/properties/confirm`                            | Confirm selected units (requires profile complete)                |
| POST   | `/v1/contact-onboarding/properties/claim`                              | Claim pending units after onboarding is complete                  |
| GET    | `/v1/contact-onboarding/profile`                                       | Read contact profile for the wizard                               |
| PATCH  | `/v1/contact-onboarding/profile`                                       | Update profile + complete `complete_profile`                      |
| GET    | `/v1/contact-onboarding/vehicles/options`                              | Brand/model/color picker options (static JSON)                    |
| GET    | `/v1/contact-onboarding/vehicles`                                      | List vehicles (`?unit_id=` optional filter)                       |
| POST   | `/v1/contact-onboarding/vehicles`                                      | Add a vehicle                                                     |
| PATCH  | `/v1/contact-onboarding/vehicles/{vehicle_id}`                         | Update a vehicle                                                  |
| POST   | `/v1/contact-onboarding/vehicles/{vehicle_id}/withdraw`                | Withdraw a pending request (hard-delete before approval)          |
| DELETE | `/v1/contact-onboarding/vehicles/{vehicle_id}`                         | Soft-remove an approved vehicle (`status = removed`)              |
| POST   | `/v1/contact-onboarding/steps/vehicles/complete`                       | Complete `vehicles` for one unit (`{ contact_unit_id }`)          |
| POST   | `/v1/contact-onboarding/steps/skip`                                    | Skip unit step (`vehicles`/`household` + `contact_unit_id`)       |
| GET    | `/v1/contact-onboarding/household`                                     | List household/family members (`?unit_id=` optional)              |
| POST   | `/v1/contact-onboarding/household`                                     | Add a family member to a unit                                     |
| PATCH  | `/v1/contact-onboarding/household/{contact_unit_id}`                   | Update a family member (name, relationship, portal_access)        |
| DELETE | `/v1/contact-onboarding/household/{contact_unit_id}`                   | Remove a family member (deletes orphaned family contact)          |
| POST   | `/v1/contact-onboarding/household/{contact_unit_id}/revoke-invitation` | Primary revokes a pending portal invite (member kept)             |
| POST   | `/v1/contact-onboarding/household/{contact_unit_id}/resend-invitation` | Resend SMS for a pending portal invite                            |
| POST   | `/v1/contact-onboarding/household/invitations/validate`                | Validate SMS deep-link token (public)                             |
| POST   | `/v1/contact-onboarding/household/invitations/accept`                  | Accept invitation via token (public)                              |
| POST   | `/v1/contact-onboarding/household/invitations/decline`                 | Decline invitation via token (public)                             |
| POST   | `/v1/contact-onboarding/steps/household/complete`                      | Complete `household` for one unit (`{ contact_unit_id }`)         |
| POST   | `/v1/contact-onboarding/default-unit`                                  | Choose default login unit (step 5)                                |
| GET    | `/v1/contact-onboarding/review`                                        | Aggregate review (contact + units + vehicles + household + steps) |
| POST   | `/v1/contact-onboarding/complete`                                      | Finalize onboarding → activate unit links                         |

### Admin vehicle review (project APIs)

These live under `/v1/projects` (community admin RBAC), not contact-onboarding:

| Method | Path                                                               | Purpose                                    |
| ------ | ------------------------------------------------------------------ | ------------------------------------------ |
| GET    | `/v1/projects/{project_id}/vehicle-requests`                       | List vehicle requests (`?status=pending`)  |
| PATCH  | `/v1/projects/{project_id}/vehicle-requests/{vehicle_id}`          | Approve (with `parking_slot_id`) or reject |
| GET    | `/v1/projects/{project_id}/facilities/{facility_id}/parking-slots` | List slots (`?status=available`)           |

______________________________________________________________________

## 5. Business rules & gating

Enforced in `contact_onboarding_service.py` and related services:

- **Contact steps auto‑seeded:** `_ensure_onboarding` creates contact-level step rows on first touch.
- **Unit steps auto‑seeded:** `confirm_properties` (and post-onboarding `claim_properties`) call
  `ContactUnitOnboardingRepository.ensure_steps_for_units` for each confirmed unit.
- **Profile before properties:** `confirm_properties` rejects until `complete_profile` is terminal.
- **Skippable unit steps only:** `skip_step` rejects anything except `vehicles` / `household`
  and requires `contact_unit_id` (`unit_step_requires_contact_unit`).
- **Vehicles:**
  - Picker options (brand → models, colors) come from `app/data/vehicle_catalog.json` via
    `GET /vehicles/options?vehicle_type=two_wheeler|four_wheeler` — not stored in Postgres.
    JSON is split by vehicle type; edit the file to add brands/models/colors per type.
    Optional query params: `brand_id` (narrow models), `search` (filter names).
  - Each vehicle is tied to a unit the contact actively owns (`unit_not_assigned` / `unit_not_found`).
  - Create payload: `unit_id`, `vehicle_type`, `registration_number`, optional `make`/`model`/`color`,
    `fuel_type`, and `photo_paths` (list of storage paths, up to 10).
  - New vehicles default to `status = pending`. Contacts cannot set `status`, `rejection_reason`,
    or `parking_slot_id`.
  - **Admin review** (community admin, project APIs):
    1. Resident submits vehicle → `pending`
    1. Admin lists `GET /v1/projects/{id}/vehicle-requests?status=pending`
    1. Admin lists available slots `GET .../facilities/{facility_id}/parking-slots?status=available`
    1. Admin approves `PATCH .../vehicle-requests/{vehicle_id}` with `{ "status": "approved", "parking_slot_id": "..." }`
       or rejects with `{ "status": "rejected", "rejection_reason": "..." }`
    1. On approve: slot → `assigned`, vehicle gets `parking_slot_id`. On remove: slot released.
  - Parking slots are provisioned when a **parking** facility is created in project setup
    (`facilities.parking_slots` → `facility_parking_slots` rows). See `docs/project-setup-flow.md`.
  - **Withdraw (pending only):** `POST /vehicles/{id}/withdraw` permanently deletes the row.
    Allowed only while `status = pending` (before admin approval).
  - **Remove (approved only):** `DELETE /vehicles/{id}` sets `status = removed`, `deleted_at = now()`,
    releases parking slot; row is kept for audit (soft delete).
  - `status_updated_at` is set on create and on every status change (approve, reject, remove).
  - Registration numbers are unique per project among active vehicles (`vehicle_registration_duplicate` on conflict).
- **Household requires an assigned unit:** adding a member checks the primary contact has an
  active link to that unit (`contact_onboarding.errors.unit_not_assigned`) and the unit exists
  (`contact_onboarding.errors.unit_not_found`).
- **Household removal is ownership‑scoped:** removing a member only works on a `Family` link
  that sits on a unit the primary contact actively owns
  (`contact_onboarding.errors.household_member_not_found`). The `contact_units` link is deleted,
  any pending invitation is cancelled, and if the family contact has no remaining links it is
  soft‑deleted.
- **Household invitation (phone-only, standalone):**
  - `portal_access=false` → auth provisioned immediately, link `active`, `member_status=joined`.
  - `portal_access=true` → contact created without auth, link `pending`, `household_invitations` row created,
    SMS sent to the member's phone with a deep link (`household_invitation_service.py`).
  - `member_status` in list/add responses: `joined` (no portal / accepted) or `invited` (portal, pending).
  - `GET /household` includes `invite_url` + `invitation_expires_at` for pending invites (copy/share manually).
  - Accept: invitee opens SMS link → `POST .../invitations/accept { token, password }` → auth provisioned from phone,
    password set, session tokens returned (phone login), unit activated, family member onboarding seeded.
  - Decline: invitee opens SMS link → `POST .../invitations/decline { token }` → invitation marked `declined`,
    pending unit link removed, orphan family contact soft-deleted (member disappears from primary's `GET /household`).
  - Inviter cancel vs invitee decline: primary `POST .../revoke-invitation` or
    `PATCH .../household/{id}` with `portal_access=false` sets invitation `cancelled` and keeps
    the member on the unit; primary `DELETE /household/{contact_unit_id}` removes the member and
    cancels the invite; invitee decline sets invitation `declined` and removes the member link.
  - **Update:** `PATCH /household/{contact_unit_id}` can change `first_name`, `last_name`,
    `relationship`, and `portal_access`. Enabling `portal_access` requires a primary phone on the
    member, sets the unit link to `pending`, and sends an SMS invite. Disabling `portal_access`
    cancels any pending invitation and reactivates the unit link.
  - SMS provider: wire in `app/utils/household_invitation_sms.py` (currently logs in dev).
- **Finalize (`complete_onboarding`) prerequisites:**
  - not already completed (`already_completed`),
  - at least one active unit (`no_active_units`),
  - if more than one unit, a default login unit must be set (`no_default_unit`),
  - every contact-level step except `review` must be `completed`/`skipped` (`step_prerequisite`),
  - every confirmed unit must have `vehicles` and `household` `completed`/`skipped` (`unit_steps_incomplete`),
  - then unit links are activated and the `review` step is completed.

______________________________________________________________________

## 6. Multi-property onboarding

Onboarding is **one wizard per contact**, not one wizard per unit. A contact pre‑allotted
three apartments still has a single profile and one set of contact-level steps — but
`vehicles` and `household` are tracked **per confirmed unit** in `contact_unit_onboarding_steps`.

### How it differs from a single property

| Area                         | 1 unit                               | Multiple units                                            |
| ---------------------------- | ------------------------------------ | --------------------------------------------------------- |
| Profile (`complete_profile`) | Once per contact                     | Once per contact (shared)                                 |
| `select_properties`          | Confirm the one pending allotment    | Multi-select which pending allotments to accept           |
| Vehicles / household         | One unit loop (vehicles → household) | Repeat vehicles → household **for each confirmed unit**   |
| `choose_unit`                | Auto-completed on confirm            | Required unless `default_contact_unit_id` sent on confirm |
| Review                       | One unit in payload                  | All active units + all vehicles + all household           |
| Finalize                     | `is_default_login` not enforced      | `is_default_login` required on exactly one active unit    |

### Step-by-step flow (multiple units)

```
Admin pre-allots N units (contact_units.status = pending)
        ↓
Step 1  GET  /profile                   → pre-fill profile form
        PATCH /profile                  → complete_profile
        ↓
Step 2  GET  /properties                → list all pending + active units
        POST /properties/confirm        → { "contact_unit_ids": ["...", "..."],
                                            "default_contact_unit_id": "..." (optional) }
                                        seeds unit onboarding steps per confirmed unit;
                                        auto-completes choose_unit when 1 unit confirmed
        ↓
Step 3  For each confirmed unit (use GET /status → current_contact_unit_id):
        POST /vehicles { unit_id, … }   → optional
        POST /steps/vehicles/complete { contact_unit_id }
        or POST /steps/skip { step_key: "vehicles", contact_unit_id }
        POST /household { unit_id, … }  → optional; family members are per unit
        POST /steps/household/complete { contact_unit_id }
        or POST /steps/skip { step_key: "household", contact_unit_id }
        ↓
Step 4  POST /default-unit              → when 2+ active units and not set on confirm
        ↓
Step 5  GET  /review                    → aggregate + unit_onboarding progress
        POST /complete                  → sets activated_at on all active units
```

### Step 2 — confirming properties

- **`GET /properties`** returns every `contact_units` row for the contact with
  `status` in `pending` or `active`, including display fields (`code`, `tower_name`,
  `floor_name`, `config_label`, `is_default_login`, etc.).
- **`POST /properties/confirm`** requires `complete_profile` first. Accepts one or more
  `contact_unit_ids` and optional `default_contact_unit_id` when confirming multiple units.
  Selected pending rows → `active`; unit onboarding steps are seeded for each.
- When exactly one unit is confirmed, `choose_unit` is auto-completed and default login is set.
- Unselected pending units **remain pending** and are excluded from vehicle/household
  validation (`unit_not_assigned`) until confirmed.

**Partial property selection:** `contact_unit_ids` is the set the user accepts **in this call**,
not all assigned units. To finish onboarding for one unit only, confirm just that unit's
`contact_unit_id` and leave others pending. Pending units do **not** block `POST /complete`.

**Multiple units confirmed in one call:** `POST /complete` requires vehicles + household
`completed` or `skipped` for **every active (confirmed) unit** (`unit_steps_incomplete` otherwise).
To finish onboarding before finishing Unit 2 setup, either skip Unit 2's steps:

```json
POST /steps/skip { "step_key": "vehicles", "contact_unit_id": "<unit2_contact_unit_id>" }
POST /steps/skip { "step_key": "household", "contact_unit_id": "<unit2_contact_unit_id>" }
```

or confirm units one at a time across separate `/properties/confirm` calls before calling
`POST /complete`. After onboarding, claim remaining pending units via `POST /properties/claim`.

### Unit-scoped vehicles and household

- **Navigation:** `GET /status` returns `unit_onboarding[]` (per-unit step progress) and
  `current_contact_unit_id` while on vehicles/household.
- **Vehicles:** `POST /vehicles` requires `unit_id`. Complete/skip with `contact_unit_id`.
- **Household:** `POST /household` requires `unit_id`. `GET /household?unit_id=` filters to one unit.
  Complete/skip with `contact_unit_id`.

### Step 5 — default login unit

When the contact has **exactly one active unit** after confirm, `choose_unit` is
auto-completed and default login is set — the mobile app can skip the choose-unit screen.

When the contact has **more than one active unit** after confirm:

- Call **`POST /default-unit`** with `{ "contact_unit_id": "<uuid>" }`.
- Sets `is_default_login = true` on the chosen row and clears it on all other active rows
  for that contact.
- Completes the `choose_unit` wizard step.
- **`POST /complete` fails** with `no_default_unit` if this was not done.

When the contact has **exactly one active unit**, default-login validation is skipped at
finalize. For multiple units, `POST /complete` fails with `no_default_unit` if not set.

> **`is_primary`** (set at admin allotment) and **`is_default_login`** (set on confirm or step 4)
> are independent. Primary marks ownership; default login controls which property opens first
> after sign-in.

### Review and finalize

**`GET /review`** returns:

| Key               | Contents                               |
| ----------------- | -------------------------------------- |
| `contact`         | Profile of the onboarding contact      |
| `units`           | All pending + active property links    |
| `vehicles`        | All vehicles registered by the contact |
| `household`       | All family members across owned units  |
| `steps`           | Contact-level wizard step statuses     |
| `unit_onboarding` | Per-unit vehicles/household progress   |

**`POST /complete`** prerequisites (enforced in `complete_onboarding`):

1. Wizard not already completed.
1. At least one active unit.
1. If `active_count > 1`, a default login unit must be set.
1. Every contact-level step except `review` must be `completed` or `skipped`.
1. Every active unit must have `vehicles` and `household` `completed` or `skipped`.
1. On success: `activated_at` is set on all active units; `review` step is completed.

### Mobile UI recommendations

1. **Profile first:** `GET /profile` → `PATCH /profile` before property selection.
1. **Properties:** Multi-select checklist; send `contact_unit_ids` (+ optional `default_contact_unit_id`).
1. **Unit loop:** Drive UI from `GET /status` — iterate `unit_onboarding` or follow
   `current_contact_unit_id` until all unit steps are terminal.
1. **Vehicles / household:** Scope forms to the active unit; pass `contact_unit_id` on complete/skip.
1. **Choose unit:** Show only when `setup_current_step === "choose_unit"`.
1. **Review:** Group vehicles and household members by unit (use `unit_id` / tower + code).

### Quick API reference (multi-unit)

| Action            | Endpoint                        | Notes                                            |
| ----------------- | ------------------------------- | ------------------------------------------------ |
| Check progress    | `GET /status`                   | `setup_current_step` + `current_contact_unit_id` |
| Read profile      | `GET /profile`                  | Pre-fill step 1                                  |
| List allotments   | `GET /properties`               | Pending + active                                 |
| Accept units      | `POST /properties/confirm`      | After profile; seeds unit steps                  |
| Complete vehicles | `POST /steps/vehicles/complete` | `{ contact_unit_id }`                            |
| Skip unit step    | `POST /steps/skip`              | `step_key` + `contact_unit_id`                   |
| Add vehicle       | `POST /vehicles`                | Requires `unit_id`                               |
| Add family        | `POST /household`               | Requires `unit_id`                               |
| Set login default | `POST /default-unit`            | When 2+ active units                             |
| Preview all       | `GET /review`                   | Includes `unit_onboarding`                       |
| Finish            | `POST /complete`                | Activates all confirmed units                    |

______________________________________________________________________

## 7. Post-onboarding property assignment

When a contact **already finished onboarding** (`GET /status` → `is_completed: true`) and an
admin assigns another unit later, the **full wizard does not reopen**. The new allotment is a
**property claim** flow instead.

### What the admin does

```
GET  /v1/contacts/{contact_id}/units              → list all unit assignments (optional ?status=)
POST /v1/contacts/{contact_id}/units
{ "unit_id": "...", "relationship": "self", "is_primary": false }
```

Creates a new `contact_units` row with `status = pending`. Existing wizard step rows stay
`completed` / `skipped`.

### What the contact sees

| API               | Result                                             |
| ----------------- | -------------------------------------------------- |
| `GET /status`     | `is_completed: true`, `setup_current_step: null`   |
| `GET /properties` | Existing active units **plus** new pending unit(s) |

The mobile app should show a **“New property to accept”** banner — not the 6-step wizard.

### Claim flow (recommended)

```
GET  /properties                         → detect pending rows while is_completed
POST /properties/claim                   → { "contact_unit_ids": ["..."] }
POST /default-unit (if requires_default_unit === true)
POST /vehicles, POST /household (optional) → scoped to new unit_id
```

**`POST /properties/claim`** (post-onboarding only):

- Requires onboarding to be **already complete**; otherwise returns
  `onboarding_not_completed_use_confirm` (use `POST /properties/confirm` during the wizard).
- Activates selected pending rows (`status → active`, `claimed_at` set).
- Sets **`activated_at`** on the claimed rows (same as finalize does for first onboarding).
- Returns:

```json
{
  "items": [{ "id": "...", "status": "active" }],
  "requires_default_unit": true
}
```

`requires_default_unit` is `true` when the contact now has **2+ active units** and no
`is_default_login` unit is set — prompt for `POST /default-unit`.

### During vs after onboarding

| Endpoint                   | When to use                                         |
| -------------------------- | --------------------------------------------------- |
| `POST /properties/confirm` | Step 1 of the wizard (`is_completed: false`)        |
| `POST /properties/claim`   | After onboarding is complete (`is_completed: true`) |

Both accept one or more `contact_unit_ids`. `confirm` also marks the `select_properties`
step complete. `claim` does **not** change wizard steps or call `POST /complete` again
(that endpoint returns `already_completed`).

### Example timeline

```text
Day 1 — Contact1 + Unit A
  Full onboarding → POST /complete → Unit A active, activated_at set

Day 30 — Admin assigns Unit B (pending)
  Contact logs in:
    GET /status      → is_completed: true
    GET /properties  → Unit A (active), Unit B (pending)
    POST /properties/claim { Unit B }
    POST /default-unit (if requires_default_unit)
    POST /vehicles / POST /household for Unit B as needed
```

### Mobile UI recommendation

```text
On app open:
  GET /status + GET /properties

If is_completed && any property.status === "pending":
  → Show claim modal
  → POST /properties/claim
  → If requires_default_unit, prompt POST /default-unit

Else if !is_completed:
  → Normal onboarding wizard (POST /properties/confirm at step 1)
```

______________________________________________________________________

## 8. How to make common changes

| I want to…                          | Change here                                                                        |
| ----------------------------------- | ---------------------------------------------------------------------------------- |
| Add/remove a wizard step            | `ContactOnboardingStep` enum + Postgres enum + `ONBOARDING_STEP_KEYS` ordering     |
| Make a step skippable / required    | `allowed_skip` set in `skip_step` (`contact_onboarding_service.py`)                |
| Change finalize prerequisites       | `complete_onboarding` in `contact_onboarding_service.py`                           |
| Add a field to a request/response   | matching model in `schemas/contact_onboarding.py`                                  |
| Add/rename a DB column              | new migration in `ats-home-craft-supabase` + repository SQL + schema model         |
| Change how "current step" is chosen | `_derive_current_step`                                                             |
| Add an endpoint                     | route in `api/contact_onboarding.py` → service method → repository method          |
| Change a user‑facing message        | `app/locales/en.json` under `contact_onboarding.*`                                 |
| Change vehicle approval workflow    | `vehicles_service.review_vehicle` + `PATCH /v1/projects/.../vehicle-requests/{id}` |
| Wire household SMS delivery         | `app/utils/household_invitation_sms.py`                                            |
| Change who can act                  | `extract_onboarding_contact_context` (context resolution)                          |

______________________________________________________________________

## 9. Tests

- `tests/unit/test_contact_onboarding_service.py` — step derivation, skip rules, finalize gating.
- `tests/unit/test_contact_units_service.py` — property confirm/claim after onboarding.

Run: `.venv/bin/python -m pytest apps/user_service/tests/unit`

______________________________________________________________________

## Related

- Project setup wizard (admin side): `docs/project-setup-flow.md`. The two flows meet at
  **units** (project setup) and **vehicles** (onboarding submit → project admin review + parking slot).
  Schema reference: `ats-home-craft-supabase/docs/project-setup-schema.md`.
