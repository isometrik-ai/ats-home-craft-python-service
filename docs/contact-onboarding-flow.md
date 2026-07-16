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

Progress is tracked per contact in `contact_onboarding_steps` (one row per step). When all
required steps are `completed`/`skipped` and prerequisites pass, onboarding is finalized:
the contact's unit links are activated.

### Wizard steps (order matters)

Enum: `ContactOnboardingStep` in `apps/user_service/app/schemas/enums.py`.

| #   | Step key            | Required?                  | Purpose                                                       |
| --- | ------------------- | -------------------------- | ------------------------------------------------------------- |
| 1   | `select_properties` | required                   | Confirm which pre‑allotted units the contact accepts          |
| 2   | `complete_profile`  | required                   | Fill contact profile (name, DOB, gender, emails/phones, etc.) |
| 3   | `vehicles`          | **skippable**              | Register vehicles (or skip if none)                           |
| 4   | `household`         | **skippable**              | Add family members to a unit (or skip)                        |
| 5   | `choose_unit`       | required (only if >1 unit) | Pick default login unit                                       |
| 6   | `review`            | required                   | Final review → completes onboarding                           |

Only `vehicles` and `household` may be skipped (`skip_step` enforces this via `allowed_skip`).
The "current step" is derived on the fly: the first step whose status is not
`completed`/`skipped` (`_derive_current_step` in `contact_onboarding_service.py`).

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

| Table                      | Purpose                                                                        |
| -------------------------- | ------------------------------------------------------------------------------ |
| `contacts` (existing)      | The person being onboarded (and family members)                                |
| `contact_units`            | Links a contact to a unit (status, is_primary, is_default_login, relationship) |
| `vehicles`                 | Vehicles registered by the contact for a unit                                  |
| `household_invitations`    | Phone-based SMS invites for portal-access family members (`20260629113000_*`)  |
| `contact_onboarding_steps` | Per‑contact wizard step status                                                 |

Key enums: `ContactOnboardingStep`, `ContactUnitStatus` (`pending`/`active`/`moved_out`),
`ContactUnitRelationship`, `VehicleType` (`two_wheeler`/`four_wheeler`),
`VehicleFuelType` (`non_ev`/`ev` — UI label: Non EV / EV Vehicle),
`VehicleStatus` (`pending`/`approved`/`rejected`), `SetupStepStatus`,
`HouseholdInvitationStatus`, `HouseholdMemberStatus`.

### `vehicles` columns (contact-facing)

| Column                   | Type    | Notes                                                                     |
| ------------------------ | ------- | ------------------------------------------------------------------------- |
| `unit_id`                | uuid FK | Must be a unit actively assigned to the contact                           |
| `vehicle_type`           | enum    | `two_wheeler`, `four_wheeler`                                             |
| `registration_number`    | text    | Unique per `(organization_id, project_id)`                                |
| `make`, `model`, `color` | text    | Optional                                                                  |
| `photo_paths`            | text[]  | Storage paths only (max 10 per vehicle); not raw blobs                    |
| `fuel_type`              | enum    | Optional on create; `non_ev`, `ev` (UI: Non EV / EV Vehicle)              |
| `status`                 | enum    | Defaults to `pending` on create; admin sets `approved` / `rejected` later |
| `rejection_reason`       | text    | Set by admin when `status = rejected` (not contact-editable yet)          |

Media/files (profile photo, vehicle images) store **paths only** — no raw blobs in Postgres.

______________________________________________________________________

## 4. API catalog

All routes under `/v1/contact-onboarding`. The acting contact is resolved from the JWT, so
most endpoints take **no** contact id in the path.

| Method | Path                                                                   | Step / purpose                                                    |
| ------ | ---------------------------------------------------------------------- | ----------------------------------------------------------------- |
| GET    | `/v1/contact-onboarding/status`                                        | Wizard progress + derived current step                            |
| GET    | `/v1/contact-onboarding/properties`                                    | List pre‑allotted units to confirm (step 1)                       |
| POST   | `/v1/contact-onboarding/properties/confirm`                            | Confirm selected units (step 1)                                   |
| PATCH  | `/v1/contact-onboarding/profile`                                       | Update profile + complete `complete_profile` (step 2)             |
| GET    | `/v1/contact-onboarding/vehicles/options`                              | Brand/model/color picker options (static JSON)                    |
| GET    | `/v1/contact-onboarding/vehicles`                                      | List vehicles                                                     |
| POST   | `/v1/contact-onboarding/vehicles`                                      | Add a vehicle                                                     |
| PATCH  | `/v1/contact-onboarding/vehicles/{vehicle_id}`                         | Update a vehicle                                                  |
| DELETE | `/v1/contact-onboarding/vehicles/{vehicle_id}`                         | Hard-delete a vehicle (row removed from `vehicles`)               |
| POST   | `/v1/contact-onboarding/steps/vehicles/complete`                       | Complete the `vehicles` step                                      |
| POST   | `/v1/contact-onboarding/steps/skip`                                    | Skip an optional step (`vehicles` or `household`)                 |
| GET    | `/v1/contact-onboarding/household`                                     | List household/family members                                     |
| POST   | `/v1/contact-onboarding/household`                                     | Add a family member to a unit                                     |
| PATCH  | `/v1/contact-onboarding/household/{contact_unit_id}`                   | Update a family member (name, relationship, portal_access)        |
| DELETE | `/v1/contact-onboarding/household/{contact_unit_id}`                   | Remove a family member (deletes orphaned family contact)          |
| POST   | `/v1/contact-onboarding/household/{contact_unit_id}/resend-invitation` | Resend SMS for a pending portal invite                            |
| POST   | `/v1/contact-onboarding/household/invitations/validate`                | Validate SMS deep-link token (public)                             |
| POST   | `/v1/contact-onboarding/household/invitations/accept`                  | Accept invitation via token (public)                              |
| POST   | `/v1/contact-onboarding/household/invitations/decline`                 | Decline invitation via token (public)                             |
| POST   | `/v1/contact-onboarding/steps/household/complete`                      | Complete the `household` step                                     |
| POST   | `/v1/contact-onboarding/default-unit`                                  | Choose default login unit (step 5)                                |
| GET    | `/v1/contact-onboarding/review`                                        | Aggregate review (contact + units + vehicles + household + steps) |
| POST   | `/v1/contact-onboarding/complete`                                      | Finalize onboarding → activate unit links                         |

______________________________________________________________________

## 5. Business rules & gating

Enforced in `contact_onboarding_service.py`:

- **Steps auto‑seeded:** `_ensure_onboarding` creates all step rows for the contact on first touch.
- **Skippable steps only:** `skip_step` rejects anything except `vehicles` / `household`
  (`contact_onboarding.errors.step_not_skippable`) and unknown keys
  (`contact_onboarding.errors.invalid_step`).
- **Vehicles:**
  - Picker options (brand → models, colors) come from `app/data/vehicle_catalog.json` via
    `GET /vehicles/options?vehicle_type=two_wheeler|four_wheeler` — not stored in Postgres.
    JSON is split by vehicle type; edit the file to add brands/models/colors per type.
    Optional query params: `brand_id` (narrow models), `search` (filter names).
  - Each vehicle is tied to a unit the contact actively owns (`unit_not_assigned` / `unit_not_found`).
  - Create payload: `unit_id`, `vehicle_type`, `registration_number`, optional `make`/`model`/`color`,
    `fuel_type`, and `photo_paths` (list of storage paths, up to 10).
  - New vehicles default to `status = pending`. Contacts cannot set `status` or `rejection_reason`;
    admin approval/rejection APIs are planned separately.
  - `DELETE /vehicles/{id}` hard-deletes the row (no soft-delete / `removed` status).
  - Registration numbers are unique per project (`vehicle_registration_duplicate` on conflict).
- **Household requires an assigned unit:** adding a member checks the primary contact has an
  active link to that unit (`contact_onboarding.errors.unit_not_assigned`) and the unit exists
  (`contact_onboarding.errors.unit_not_found`).
- **Household removal is ownership‑scoped:** removing a member only works on a `Family` link
  that sits on a unit the primary contact actively owns
  (`contact_onboarding.errors.household_member_not_found`). The `contact_units` link is deleted,
  any pending invitation is cancelled, and if the family contact has no remaining links it is
  soft‑deleted.
- **Household invitation (phone-only, standalone):**
  - `portal_access=false` → current flow: auth provisioned immediately, link `active`, `member_status=joined`.
  - `portal_access=true` → contact created without auth, link `pending`, `household_invitations` row created,
    SMS sent to the member's phone with a deep link (`household_invitation_service.py`).
  - `member_status` in list/add responses: `joined` (no portal / accepted) or `invited` (portal, pending).
  - `GET /household` includes `invite_url` + `invitation_expires_at` for pending invites (copy/share manually).
  - Accept: invitee opens SMS link → `POST .../invitations/accept { token, password }` → auth provisioned from phone,
    password set, session tokens returned (phone login), unit activated, family member onboarding seeded.
  - Decline: invitee opens SMS link → `POST .../invitations/decline { token }` → invitation marked `declined`,
    pending unit link removed, orphan family contact soft-deleted (member disappears from primary's `GET /household`).
  - Inviter cancel vs invitee decline: primary `DELETE /household/{contact_unit_id}` sets invitation `cancelled`;
    invitee decline sets invitation `declined`.
  - **Update:** `PATCH /household/{contact_unit_id}` can change `first_name`, `last_name`,
    `relationship`, and `portal_access`. Enabling `portal_access` requires a primary phone on the
    member, sets the unit link to `pending`, and sends an SMS invite. Disabling `portal_access`
    cancels any pending invitation and reactivates the unit link.
  - SMS provider: wire in `app/utils/household_invitation_sms.py` (currently logs in dev).
- **Finalize (`complete_onboarding`) prerequisites:**
  - not already completed (`already_completed`),
  - at least one active unit (`no_active_units`),
  - if more than one unit, a default login unit must be set (`no_default_unit`),
  - every non‑review step must be `completed`/`skipped` (`step_prerequisite`),
  - then unit links are activated and the `review` step is completed.

______________________________________________________________________

## 6. How to make common changes

| I want to…                          | Change here                                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------ |
| Add/remove a wizard step            | `ContactOnboardingStep` enum + Postgres enum + `ONBOARDING_STEP_KEYS` ordering |
| Make a step skippable / required    | `allowed_skip` set in `skip_step` (`contact_onboarding_service.py`)            |
| Change finalize prerequisites       | `complete_onboarding` in `contact_onboarding_service.py`                       |
| Add a field to a request/response   | matching model in `schemas/contact_onboarding.py`                              |
| Add/rename a DB column              | new migration in `ats-home-craft-supabase` + repository SQL + schema model     |
| Change how "current step" is chosen | `_derive_current_step`                                                         |
| Add an endpoint                     | route in `api/contact_onboarding.py` → service method → repository method      |
| Change a user‑facing message        | `app/locales/en.json` under `contact_onboarding.*`                             |
| Change vehicle approval workflow    | `VehicleStatus` enum + `vehicles.status` column + future admin API             |
| Wire household SMS delivery         | `app/utils/household_invitation_sms.py`                                        |
| Change who can act                  | `extract_onboarding_contact_context` (context resolution)                      |

______________________________________________________________________

## 7. Tests

- `tests/unit/test_contact_onboarding_service.py` — step derivation, skip rules, finalize gating.

Run: `.venv/bin/python -m pytest apps/user_service/tests/unit`

______________________________________________________________________

## Related

- Project setup wizard (admin side): `docs/project-setup-flow.md`. The two flows meet at
  **units** — a project's `units` become the `contact_units` a contact confirms here.
