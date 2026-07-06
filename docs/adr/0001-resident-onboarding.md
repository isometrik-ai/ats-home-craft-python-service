# ADR 0001: Resident onboarding schema and backend model

|                  |                                                                                                                                                                                                |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Accepted                                                                                                                                                                                       |
| **Date**         | 2026-06-29                                                                                                                                                                                     |
| **Authors**      | Home Craft platform team                                                                                                                                                                       |
| **Related docs** | [resident-onboarding-schema.md](../../../ats-home-craft-supabase/docs/resident-onboarding-schema.md), [project-setup-schema.md](../../../ats-home-craft-supabase/docs/project-setup-schema.md) |
| **Migrations**   | `20260629110000_resident_onboarding_enums.sql`, `20260629111000_resident_onboarding_tables.sql`, `20260629112000_contacts_profile_fields.sql`                                                  |

______________________________________________________________________

## Context

Home Craft has two distinct setup flows:

1. **Project Setup (admin)** — community admins configure inventory (`projects`, `towers`, `units`, …) via the dashboard wizard.
1. **Resident Onboarding (mobile)** — owners/tenants claim units, complete profile, register vehicles, add household members, and activate portal access.

The mobile flow spans six screens: property selection → profile → vehicles → household → choose default unit → review.

Constraints:

- Multi-tenancy via `organization_id` on all property-related data (same pattern as Project Setup and `contacts`).
- **`organization_members`** is staff RBAC only — mobile residents must not be modeled there.
- **`contacts`** already exists with `contact_type` (`Owner`, `Tenant`, `Family`, …), phone/email, DOB, and **`portal_access`** provisioning (auth user + Isometrik) in `ContactsService`.
- Unit inventory already lives in **`units`** from Project Setup — onboarding must reference it, not duplicate it.
- RLS is enabled on new tables but **policies are deferred**; backend uses `service_role` until policies are added (same as Project Setup phase 1).

______________________________________________________________________

## Decision

### 1. Reuse `contacts` as the person root — no `residents` table

All people in the onboarding flow (primary user and family members) are **`contacts`** rows:

| Person        | `contact_type`      | Notes                         |
| ------------- | ------------------- | ----------------------------- |
| Primary user  | `Owner` or `Tenant` | Logged-in resident            |
| Family member | `Family`            | Created during household step |

Auth resolution:

```text
auth.users.id → contacts.user_id (uq_contacts_user_org) → contact_units → units
```

**Rationale:** Avoids duplicating identity, phones, portal provisioning, and CRM sync already implemented in the contacts API.

### 2. Add three new tables for onboarding relationships only

| Table                          | Purpose                                                                              |
| ------------------------------ | ------------------------------------------------------------------------------------ |
| **`contact_units`**            | Many-to-many contact ↔ unit (“Your properties”, household scope, default login unit) |
| **`vehicles`**                 | Vehicle registered by `contact_id`, assigned to `unit_id`                            |
| **`contact_onboarding_steps`** | Wizard progress per `contact_id` (mirrors `project_setup_steps`)                     |

Every new table carries **`organization_id NOT NULL`**. `contact_units` and `vehicles` also carry **`project_id`** (denormalized from `units`).

### 3. Extend `contacts` with profile and portal columns

Migration `20260629112000_contacts_profile_fields.sql` adds:

| Column                      | Type                       | Default / notes                                                 |
| --------------------------- | -------------------------- | --------------------------------------------------------------- |
| `gender`                    | `contact_gender` enum      | Optional                                                        |
| `blood_group`               | `contact_blood_group` enum | Optional                                                        |
| `communication_preferences` | `jsonb`                    | API default `{ email: true, sms: true, push: false }` on create |
| `portal_access`             | `boolean`                  | `NOT NULL DEFAULT true`; persisted; drives auth provisioning    |

Onboarding **progress** is **not** stored on `contacts` — it lives in `contact_onboarding_steps`.

### 4. Fixed six-step wizard

| Step key            | UI screen                          |
| ------------------- | ---------------------------------- |
| `select_properties` | Your properties                    |
| `complete_profile`  | Complete profile                   |
| `vehicles`          | Vehicle details + select apartment |
| `household`         | Household & app access             |
| `choose_unit`       | Choose unit to login               |
| `review`            | Review & confirm                   |

Step status reuses **`setup_step_status`** from Project Setup (`not_started`, `in_progress`, `completed`, `skipped`).

### 5. Python service integration

**Implemented (contacts module):**

- `ContactGender`, `ContactBloodGroup` enums in `app/schemas/enums.py`
- `CommunicationPreferences` model with defaults on `CreateContactRequest`
- `gender`, `blood_group`, `communication_preferences`, `portal_access` on create/update/response schemas
- `ContactsRepository` and `ContactsService` persist and normalize profile fields

**Planned (separate modules):**

| Area                 | Operations                                                      |
| -------------------- | --------------------------------------------------------------- |
| `contact-units`      | List claimable units, confirm selection, set `is_default_login` |
| `vehicles`           | CRUD per contact                                                |
| `contact-onboarding` | Get/update step status, complete wizard                         |

Household members continue to use **`POST /contacts`** with `contact_type: Family` and `portal_access` toggle.

### 6. Domain boundaries (explicit non-goals)

| Do not use                               | For residents                            |
| ---------------------------------------- | ---------------------------------------- |
| `organization_members`                   | Staff RBAC only                          |
| `project_members`                        | Admin project access only                |
| `residents` / `household_members` tables | Use `contacts` + `contact_units` instead |

______________________________________________________________________

## Consequences

### Positive

- **Single person model** — CRM, mobile, and portal share `contacts`; no sync between `residents` and `contacts`.
- **Reuses Project Setup inventory** — `units`, towers, configs power “Your properties” display without new inventory tables.
- **Consistent patterns** — org scope, wizard steps, and deferred RLS match Project Setup conventions.
- **Existing auth path** — `portal_access` + `ContactsService._provision_contact_auth_identity` works for primary users and family with app access.
- **Minimal schema surface** — 3 new tables + 4 columns on `contacts`.

### Negative / trade-offs

- **`contact_units` denormalization** — `organization_id` and `project_id` must stay consistent with parent rows; no DB sync trigger yet (backend-enforced).
- **Family as separate contacts** — each family member is a full contact row; relationship to unit is on `contact_units.relationship`, not a lightweight embed.
- **No RLS policies yet** — access control is backend-only until a follow-up migration.
- **Vehicle photos** — `photo_path` only; storage bucket not created in this phase.

### Follow-ups

1. **Implementation plan:** [ADR 0002 — implementation plan](./0002-resident-onboarding-implementation.md)
1. Add RLS policies keyed on `organization_id` and `contacts.user_id`.
1. Seed/demo data linking demo contacts to `demo-residential` units.
1. Optional: sync activated `contacts` → CRM tags or audit events on onboarding completion.
1. Optional: `units.status` → `occupied` on activation (business rule TBD).

______________________________________________________________________

## Alternatives considered

| Alternative                                     | Why rejected                                                                           |
| ----------------------------------------------- | -------------------------------------------------------------------------------------- |
| New **`residents`** table                       | Duplicates `contacts` identity, phones, and portal provisioning                        |
| **`household_members`** table                   | Extra person store; family fits `contacts` + `contact_type = Family`                   |
| Profile fields in **`additional_data`**         | No type safety; harder to query and validate; replaced by dedicated columns            |
| **`portal_access` only in API** (not DB)        | Cannot query/filter contacts without portal access; household toggle needs persistence |
| Merge residents into **`organization_members`** | Conflates staff RBAC with end-user residents                                           |

______________________________________________________________________

## References

- Schema detail: [`ats-home-craft-supabase/docs/resident-onboarding-schema.md`](../../../ats-home-craft-supabase/docs/resident-onboarding-schema.md)
- Project Setup (prerequisite inventory): [`ats-home-craft-supabase/docs/project-setup-schema.md`](../../../ats-home-craft-supabase/docs/project-setup-schema.md)
- Contacts API: `apps/user_service/app/api/contacts.py`
- Contacts schemas: `apps/user_service/app/schemas/contacts.py`
