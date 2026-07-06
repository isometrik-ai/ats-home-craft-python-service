# ADR 0002: Resident onboarding — implementation plan

|                   |                                                                                                                                                                            |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**        | Accepted                                                                                                                                                                   |
| **Date**          | 2026-06-29                                                                                                                                                                 |
| **Depends on**    | [ADR 0001](./0001-resident-onboarding.md) (architecture), [resident-onboarding-schema.md](../../../ats-home-craft-supabase/docs/resident-onboarding-schema.md) (DB detail) |
| **Prerequisites** | Migrations `20260629110000` → `20260629112000` applied; Project Setup seeds optional                                                                                       |

This document is the **implementation guide** for building resident onboarding in `user_service`. Use it as the build checklist for backend engineers.

______________________________________________________________________

## Table of contents

1. [Goal](#goal)
1. [Prerequisites](#prerequisites)
1. [Auth model](#auth-model)
1. [Module layout](#module-layout)
1. [Enums & schemas](#enums--schemas)
1. [Repositories](#repositories)
1. [Services](#services)
1. [API endpoints](#api-endpoints)
1. [Wizard implementation (step by step)](#wizard-implementation-step-by-step)
1. [Admin pre-allotment flow](#admin-pre-allotment-flow)
1. [Permissions & access control](#permissions--access-control)
1. [Error keys](#error-keys)
1. [Implementation phases](#implementation-phases)
1. [Testing checklist](#testing-checklist)
1. [Out of scope (this phase)](#out-of-scope-this-phase)

______________________________________________________________________

## Goal

Implement the **mobile resident onboarding wizard** (6 steps) backed by:

| Layer            | Tables / APIs                                                    |
| ---------------- | ---------------------------------------------------------------- |
| Person           | Existing `contacts` (+ profile columns already in Python)        |
| Unit link        | New `contact_units`                                              |
| Vehicles         | New `vehicles`                                                   |
| Progress         | New `contact_onboarding_steps`                                   |
| Inventory (read) | Existing `units`, `towers`, `floors`, `unit_configs`, `projects` |

**Done when:** A contact authenticated as `CLIENT` can complete all 6 steps via REST APIs and land with active `contact_units`, optional `vehicles`, family contacts, and completed onboarding steps.

______________________________________________________________________

## Prerequisites

### Database (Supabase)

Run in order:

```text
20250821124646_initial_execute.sql
20260629100000_property_setup_enums_and_helpers.sql
20260629101000_property_setup_tables.sql
20260629110000_resident_onboarding_enums.sql
20260629111000_resident_onboarding_tables.sql
20260629112000_contacts_profile_fields.sql
```

Optional: `property_setup_demo_projects.sql` seed for demo units.

### Python (already done)

- `contacts` profile fields: `gender`, `blood_group`, `communication_preferences`, `portal_access`
- Auth: `SelectOrganizationType.CLIENT` + `ContactsRepository.is_active_contact_user_for_organization`

______________________________________________________________________

## Auth model

Residents are **contacts**, not org members.

```text
Mobile login (phone OTP)
  → JWT (auth.users.id)
  → POST /auth/select-organization  { user_type: "client" }
  → contacts.user_id + organization_id resolved
  → All onboarding APIs scope by organization_id + contact_id
```

**Resolver helper** (add to service base or `resident_onboarding_service.py`):

```python
async def resolve_current_contact(user_context: UserContext) -> dict:
    """Return active contact row for auth user + org."""
    # SELECT * FROM contacts
    # WHERE user_id = :user_id AND organization_id = :org_id AND status = 'active'
```

Reject onboarding APIs if contact not found or `portal_access = false` (when applicable).

______________________________________________________________________

## Module layout

Create the following under `apps/user_service/app/`:

```text
schemas/
  resident_onboarding.py      # request/response models
  enums.py                    # + ContactOnboardingStep, ContactUnitStatus, etc.

db/repositories/
  contact_units_repository.py
  vehicles_repository.py
  contact_onboarding_repository.py

services/
  resident_onboarding_service.py   # wizard orchestration
  contact_units_service.py
  vehicles_service.py

api/
  resident_onboarding.py      # router prefix /resident-onboarding
  contact_units.py            # optional split; or merge into resident_onboarding.py
  vehicles.py                 # optional split
```

Register in `api/routes.py`:

```python
from apps.user_service.app.api.resident_onboarding import router as resident_onboarding_router
router.include_router(resident_onboarding_router)
```

______________________________________________________________________

## Enums & schemas

### Add to `app/schemas/enums.py`

Mirror Postgres enums (use `str, Enum`):

```python
class ContactOnboardingStep(str, Enum):
    SELECT_PROPERTIES = "select_properties"
    COMPLETE_PROFILE = "complete_profile"
    VEHICLES = "vehicles"
    HOUSEHOLD = "household"
    CHOOSE_UNIT = "choose_unit"
    REVIEW = "review"

class ContactUnitStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    MOVED_OUT = "moved_out"

class ContactUnitRelationship(str, Enum):
    SELF = "self"
    SPOUSE = "spouse"
    CHILD = "child"
    PARENT = "parent"
    SIBLING = "sibling"
    IN_LAW = "in_law"
    OTHER = "other"

class VehicleType(str, Enum):
    TWO_WHEELER = "two_wheeler"
    FOUR_WHEELER = "four_wheeler"

class VehicleStatus(str, Enum):
    ACTIVE = "active"
    REMOVED = "removed"
```

Reuse existing `SetupStepStatus` if present, else mirror `setup_step_status` values: `not_started`, `in_progress`, `completed`, `skipped`.

### `schemas/resident_onboarding.py` (key models)

```python
# --- Step 1: Your properties ---
class ContactUnitSummaryResponse(BaseModel):
    id: str
    unit_id: str
    project_id: str
    code: str                    # units.code
    unit_label: str | None
    tower_name: str | None
    floor_name: str | None
    config_label: str | None
    status: ContactUnitStatus
    is_primary: bool
    is_default_login: bool
    relationship: ContactUnitRelationship

class ConfirmPropertiesRequest(BaseModel):
    contact_unit_ids: list[str] = Field(..., min_length=1)

# --- Step 2: Complete profile ---
# Reuse UpdateContactRequest fields or wrap:
class CompleteProfileRequest(BaseModel):
    prefix: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: FlexibleOptionalDate = None
    profile_photo_url: str | None = None
    gender: ContactGender | None = None
    blood_group: ContactBloodGroup | None = None
    communication_preferences: CommunicationPreferences | None = None
    emails: list[Email] | None = None
    phones: list[Phone] | None = None  # if allowing update here

# --- Step 3–4: Vehicles ---
class CreateVehicleRequest(BaseModel):
    unit_id: str
    vehicle_type: VehicleType
    registration_number: str = Field(..., min_length=1, max_length=20)
    make: str | None = None
    model: str | None = None
    color: str | None = None
    photo_path: str | None = None

class VehicleResponse(BaseModel): ...

# --- Step 5: Household ---
class CreateHouseholdMemberRequest(BaseModel):
    unit_id: str
    first_name: str
    last_name: str | None = None
    phones: list[Phone]
    relationship: ContactUnitRelationship
    portal_access: bool = False

# --- Step 6: Choose unit ---
class SetDefaultUnitRequest(BaseModel):
    contact_unit_id: str

# --- Step 7: Review / wizard meta ---
class OnboardingStepResponse(BaseModel):
    step_key: ContactOnboardingStep
    status: str
    completed_at: str | None

class OnboardingStatusResponse(BaseModel):
    setup_current_step: ContactOnboardingStep | None
    steps: list[OnboardingStepResponse]
    is_completed: bool

class OnboardingReviewResponse(BaseModel):
    contact: ContactDetailsResponse
    units: list[ContactUnitSummaryResponse]
    vehicles: list[VehicleResponse]
    household: list[ContactUnitSummaryResponse]  # family contacts per unit
```

______________________________________________________________________

## Repositories

### `ContactUnitsRepository`

| Method                                                    | SQL intent                                              |
| --------------------------------------------------------- | ------------------------------------------------------- |
| `list_by_contact(org_id, contact_id, statuses?)`          | Join `units`, `towers`, `floors`, `unit_configs`        |
| `get_by_id(org_id, contact_unit_id)`                      | Single row + joins                                      |
| `confirm_selection(org_id, contact_id, contact_unit_ids)` | `status = active`, `claimed_at = now()` where `pending` |
| `set_default_login(org_id, contact_id, contact_unit_id)`  | Clear others; set one `is_default_login = true`         |
| `insert(org_id, project_id, unit_id, contact_id, ...)`    | Admin allotment                                         |
| `insert_for_household(...)`                               | Family member link                                      |
| `activate_all_for_contact(org_id, contact_id)`            | On review complete → `activated_at = now()`             |

### `VehiclesRepository`

| Method                                               | SQL intent                                                     |
| ---------------------------------------------------- | -------------------------------------------------------------- |
| `list_by_contact(org_id, contact_id, status=active)` |                                                                |
| `create(...)`                                        | Insert with unique `(org_id, project_id, registration_number)` |
| `update(...)` / `soft_remove(id)`                    | `status = removed`                                             |
| `assign_unit(vehicle_id, unit_id)`                   | Update `unit_id`                                               |

### `ContactOnboardingRepository`

| Method                                                     | SQL intent                                   |
| ---------------------------------------------------------- | -------------------------------------------- |
| `ensure_steps(org_id, contact_id)`                         | Upsert 6 rows `not_started` if missing       |
| `list_steps(org_id, contact_id)`                           | All steps ordered by step_key                |
| `update_step(org_id, contact_id, step_key, status, data?)` | Patch one step                               |
| `complete_step(org_id, contact_id, step_key)`              | `status = completed`, `completed_at = now()` |
| `is_all_completed(org_id, contact_id)`                     | Boolean                                      |

**Step order constant** (service layer):

```python
ONBOARDING_STEP_ORDER = [
    ContactOnboardingStep.SELECT_PROPERTIES,
    ContactOnboardingStep.COMPLETE_PROFILE,
    ContactOnboardingStep.VEHICLES,
    ContactOnboardingStep.HOUSEHOLD,
    ContactOnboardingStep.CHOOSE_UNIT,
    ContactOnboardingStep.REVIEW,
]
```

______________________________________________________________________

## Services

### `ResidentOnboardingService`

Orchestrates wizard; depends on repos + `ContactsService`.

| Method                    | Responsibility                                                                       |
| ------------------------- | ------------------------------------------------------------------------------------ |
| `get_status()`            | Return steps + derived `setup_current_step` (first non-completed)                    |
| `get_review()`            | Aggregate contact, units, vehicles, household for review screen                      |
| `complete_onboarding()`   | Mark all steps completed; activate contact_units; optional `units.status = occupied` |
| `_advance_step(step_key)` | Mark step completed; validate prior steps                                            |
| `_init_onboarding()`      | Call `ensure_steps` on first API hit                                                 |

### `ContactUnitsService`

| Method                     | Maps to UI           |
| -------------------------- | -------------------- |
| `list_my_properties()`     | Your properties      |
| `confirm_properties(body)` | Confirm selection    |
| `set_default_unit(body)`   | Choose unit to login |

### `VehiclesService`

| Method                 | Maps to UI                    |
| ---------------------- | ----------------------------- |
| `list_my_vehicles()`   | Vehicle list                  |
| `create_vehicle(body)` | Add vehicle + unit assignment |
| `remove_vehicle(id)`   | Soft delete                   |

### Household (in `ResidentOnboardingService` or `ContactsService` wrapper)

```python
async def add_household_member(body: CreateHouseholdMemberRequest):
    # 1. Validate unit belongs to primary contact's contact_units
    # 2. ContactsService.create_contact(Family, portal_access=body.portal_access)
    # 3. ContactUnitsRepository.insert_for_household(family_contact_id, unit_id, relationship)
    # 4. complete_step(HOUSEHOLD) optional — or on explicit continue
```

______________________________________________________________________

## API endpoints

Base prefix: **`/v1/resident-onboarding`**

All routes: `Depends(get_user_from_auth)`, `user_type=CLIENT` check, `resolve_current_contact`.

| Method   | Path                  | Step | Description                                                            |
| -------- | --------------------- | ---- | ---------------------------------------------------------------------- |
| `GET`    | `/status`             | —    | Wizard progress + current step                                         |
| `GET`    | `/properties`         | 1    | List claimable/active `contact_units` for current contact              |
| `POST`   | `/properties/confirm` | 1    | Confirm selected units (`pending` → `active`)                          |
| `PATCH`  | `/profile`            | 2    | Update contact profile (delegates to `ContactsService.update_contact`) |
| `GET`    | `/vehicles`           | 3    | List vehicles for current contact                                      |
| `POST`   | `/vehicles`           | 3    | Create vehicle                                                         |
| `PATCH`  | `/vehicles/{id}`      | 3    | Update vehicle / reassign unit                                         |
| `DELETE` | `/vehicles/{id}`      | 3    | Soft-remove vehicle                                                    |
| `GET`    | `/household`          | 5    | List family contacts linked to my units                                |
| `POST`   | `/household`          | 5    | Add family member (contact + contact_unit)                             |
| `POST`   | `/default-unit`       | 6    | Set `is_default_login`                                                 |
| `GET`    | `/review`             | 7    | Full summary                                                           |
| `POST`   | `/complete`           | 7    | Finalize onboarding                                                    |

### Example: `GET /v1/resident-onboarding/status`

**Response:**

```json
{
  "data": {
    "setup_current_step": "complete_profile",
    "is_completed": false,
    "steps": [
      { "step_key": "select_properties", "status": "completed", "completed_at": "2026-06-29T10:00:00Z" },
      { "step_key": "complete_profile", "status": "in_progress", "completed_at": null },
      { "step_key": "vehicles", "status": "not_started", "completed_at": null }
    ]
  }
}
```

### Example: `POST /v1/resident-onboarding/properties/confirm`

**Request:**

```json
{ "contact_unit_ids": ["uuid-1", "uuid-2"] }
```

**Behavior:**

1. Validate each `contact_unit_id` belongs to current `contact_id` and `organization_id`.
1. Set `status = active`, `claimed_at = now()`.
1. Mark step `select_properties` completed.

### Example: `POST /v1/resident-onboarding/complete`

**Behavior:**

1. Validate required steps completed (vehicles/household may allow `skipped` if empty — product rule: mark `vehicles` completed when user clicks Continue with 0 vehicles).
1. Set all `contact_units` for contact → `activated_at = now()`.
1. Mark step `review` completed.
1. Return `{ "is_completed": true }`.

______________________________________________________________________

## Wizard implementation (step by step)

### Step 1 — Your properties

```text
GET  /properties          → list contact_units (pending + active) with unit display
POST /properties/confirm  → user confirms selection
```

**Display fields** (from join):

- `units.code`, `units.unit_label`
- `towers.name`, `floors.display_name`
- `unit_configs.display_label` (BHK type)

### Step 2 — Complete profile

```text
PATCH /profile  → UpdateContactRequest subset on current contact
                → complete_step(complete_profile)
```

Required fields (validate in service before completing step):

- `first_name`, primary phone (already on contact from login)
- Optional: `gender`, `blood_group`, `communication_preferences` (defaults applied on create)

### Step 3–4 — Vehicles

```text
GET    /vehicles
POST   /vehicles        { unit_id must be in contact's active contact_units }
DELETE /vehicles/{id}
```

On Continue (optional explicit endpoint or client calls step complete):

```text
POST /steps/vehicles/complete   OR mark in POST /vehicles flow when user taps Continue
```

### Step 5 — Household

```text
GET  /household   → family contacts via contact_units where contact_type = Family
POST /household   → create Family contact + contact_unit row
```

Reuse `ContactsService.create_contact` with:

```python
CreateContactRequest(
    contact_type=ContactType.FAMILY,
    portal_access=body.portal_access,
    first_name=...,
    phones=...,
)
```

### Step 6 — Choose unit

```text
POST /default-unit  { "contact_unit_id": "..." }
```

Only allowed for `contact_units` where `status = active` and belongs to current contact.

### Step 7 — Review & complete

```text
GET  /review
POST /complete
```

______________________________________________________________________

## Admin pre-allotment flow

Admins assign units **before** resident logs in (existing contacts API + new admin endpoint).

**Suggested admin endpoint** (permission: `contacts_management.edit`):

```text
POST /v1/contacts/{contact_id}/units
```

**Request:**

```json
{
  "unit_id": "uuid",
  "is_primary": true,
  "relationship": "self"
}
```

**Behavior:**

1. Load `unit` → derive `project_id`, `organization_id`.
1. Insert `contact_units` with `status = pending`.
1. Resident sees unit on Step 1 after login.

Implement in `ContactUnitsService` + admin route on `contacts.py` or separate `admin/contact_units.py`.

______________________________________________________________________

## Permissions & access control

| Actor                  | Access                                                |
| ---------------------- | ----------------------------------------------------- |
| **Resident (CLIENT)**  | Own `contact_id` only via JWT → `contacts.user_id`    |
| **Admin (org member)** | All contacts/units in org via `contacts_management.*` |

**Resident onboarding routes:**

- Do **not** use `CONTACTS_MANAGEMENT_*` permissions.
- Use `resolve_current_contact()` — 403 if not a contact in org.
- Optionally add permission codes later: `resident_portal.onboarding`.

**Rate limits:** Match contacts (`100/minute` read, `20/minute` write).

**Audit:** `@audit_api_call` on create/update/delete; `table_name` = `contact_units` | `vehicles` | `contact_onboarding_steps`.

______________________________________________________________________

## Error keys

Add to i18n (`contacts.errors.*` or new `resident_onboarding.errors.*`):

| Key                                                         | When                                               |
| ----------------------------------------------------------- | -------------------------------------------------- |
| `resident_onboarding.errors.contact_not_found`              | No contact for auth user                           |
| `resident_onboarding.errors.unit_not_assigned`              | `unit_id` not in contact's units                   |
| `resident_onboarding.errors.contact_unit_not_found`         | Invalid `contact_unit_id`                          |
| `resident_onboarding.errors.already_completed`              | Wizard already finished                            |
| `resident_onboarding.errors.step_prerequisite`              | Step completed out of order                        |
| `resident_onboarding.errors.vehicle_registration_duplicate` | Unique violation                                   |
| `resident_onboarding.errors.no_default_unit`                | Complete called without default unit (if required) |

______________________________________________________________________

## Implementation phases

### Phase 1 — Foundation (Day 1–2)

- [ ] Add enums to `schemas/enums.py`
- [ ] Create `schemas/resident_onboarding.py`
- [ ] Implement `ContactOnboardingRepository` + `ensure_steps`
- [ ] Implement `ContactUnitsRepository.list_by_contact` (with joins)
- [ ] `ResidentOnboardingService.get_status()` + `resolve_current_contact`
- [ ] `GET /resident-onboarding/status`
- [ ] Register router in `routes.py`

### Phase 2 — Properties & profile (Day 2–3)

- [ ] `GET /properties`, `POST /properties/confirm`
- [ ] `PATCH /profile` (wrap `ContactsService`)
- [ ] Step completion for steps 1–2
- [ ] Unit tests for confirm + profile

### Phase 3 — Vehicles (Day 3–4)

- [ ] `VehiclesRepository` + `VehiclesService`
- [ ] CRUD endpoints under `/vehicles`
- [ ] Validate `unit_id` against contact's units
- [ ] Step 3 completion

### Phase 4 — Household (Day 4–5)

- [ ] `POST /household` (ContactsService + contact_units insert)
- [ ] `GET /household`
- [ ] Step 5 completion

### Phase 5 — Finish wizard (Day 5)

- [ ] `POST /default-unit`
- [ ] `GET /review`
- [ ] `POST /complete` (activate units, mark steps)
- [ ] Integration test: full wizard happy path

### Phase 6 — Admin allotment (Day 6)

- [ ] `POST /contacts/{id}/units` for admin pre-assign
- [ ] Seed script or doc for demo data

### Phase 7 — Hardening (Day 7+)

- [ ] RLS policies (Supabase follow-up migration)
- [ ] Vehicle photo presigned URL (reuse `presigned_url` API)
- [ ] Kafka events: `resident_onboarding.completed` (optional)

______________________________________________________________________

## Testing checklist

### Unit tests

- [ ] `resolve_current_contact` — found / not found
- [ ] `confirm_properties` — only own pending units
- [ ] `set_default_login` — partial unique constraint (one default per contact)
- [ ] `create_vehicle` — duplicate registration rejected
- [ ] `add_household_member` — Family contact + contact_unit created
- [ ] `complete_onboarding` — all steps marked, `activated_at` set

### Integration tests (with test DB)

- [ ] Full flow: allotment → login as CLIENT → 6 steps → complete
- [ ] Multi-unit contact: 2 units, 2 vehicles, default unit selection
- [ ] Family with `portal_access=true` → `user_id` provisioned

### Manual QA (Postman / mobile)

- [ ] Login → select org as `client`
- [ ] Each screen matches API response shape
- [ ] Review screen aggregates correctly

______________________________________________________________________

## Out of scope (this phase)

- RLS policy SQL (enabled on tables only)
- Supabase Storage bucket for vehicle photos
- `contact_invites` dedicated table
- Push notification delivery (only preferences stored)
- Changing `organizations` / `organization_members` schema

______________________________________________________________________

## References

- Architecture: [ADR 0001](./0001-resident-onboarding.md)
- DB schema: [resident-onboarding-schema.md](../../../ats-home-craft-supabase/docs/resident-onboarding-schema.md)
- Contacts API (reuse): `apps/user_service/app/api/contacts.py`
- Client auth path: `AuthService.select_organization(user_type=CLIENT)`
