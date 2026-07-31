# ADR 0010: Contact roles — unit-scoped role history

|                  |                                                                                                                                                                                                                                                                               |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Accepted                                                                                                                                                                                                                                                                      |
| **Date**         | 2026-07-31                                                                                                                                                                                                                                                                    |
| **Authors**      | Home Craft platform team                                                                                                                                                                                                                                                      |
| **Related docs** | [contacts API](../api/contacts.md), [contact-roles-schema.md](../../../ats-home-craft-supabase/docs/contact-roles-schema.md), [resident-onboarding-schema.md](../../../ats-home-craft-supabase/docs/resident-onboarding-schema.md), [ADR 0001](./0001-resident-onboarding.md) |
| **Migrations**   | `20260731120000_contact_roles_enums.sql`, `20260731121000_contact_roles_tables.sql`, `20260731122000_contact_roles_backfill_drop_contact_type.sql`                                                                                                                            |

______________________________________________________________________

## Context

Resident and CRM flows need to answer **“what role does this person play, and for which unit?”**
with history (owner → tenant supersede, move-out, household changes). The legacy model stored a
single **`contacts.contact_type`** column (`Owner`, `Tenant`, `Family`, `Vendor`, …), which:

- Could not represent **multiple roles** (e.g. owner on unit A and tenant on unit B).
- Mixed **identity** (name, phone, auth) with **operational labels** used for billing and dashboards.
- Did not preserve **ended** roles when someone moved out or was superseded.

Unit residency and portal onboarding already live in **`contact_units`** (`relationship`, `status`,
`activated_at`). Roles are a separate concern: who is billed as owner/tenant, who counts in overview
cards, and what label move events show.

______________________________________________________________________

## Decision

### 1. New table `contact_roles` — source of truth for role labels

| Column            | Purpose                                                            |
| ----------------- | ------------------------------------------------------------------ |
| `role_type`       | `Owner`, `Tenant`, `Family`, `Guest`, `Vendor`, `Staff`            |
| `status`          | `active`, `ended`, `cancelled`                                     |
| `project_id`      | Required for unit-scoped roles; `NULL` for org-scoped Vendor/Staff |
| `unit_id`         | Required for Owner/Tenant/Family/Guest; `NULL` for Vendor/Staff    |
| `relationship`    | Optional household detail when `role_type = Family`                |
| `contact_unit_id` | Optional link to the matching `contact_units` residency row        |
| `started_at`      | When the role began                                                |
| `ended_at`        | Set when role ends (rows are not deleted)                          |

**Scope rules (DB check constraint):**

- **Unit-scoped:** `Owner`, `Tenant`, `Family`, `Guest` require `project_id` + `unit_id`.
- **Org-scoped:** `Vendor`, `Staff` have `project_id` and `unit_id` NULL.

**Uniqueness (partial indexes):**

- At most one **active Owner** per unit.
- At most one **active Tenant** per unit.
- At most one **active Vendor** or **active Staff** per contact per org.

### 2. Drop `contacts.contact_type`

After backfill, **`contacts`** holds identity and CRM fields only. The column is removed in
`20260731122000_contact_roles_backfill_drop_contact_type.sql`.

The app enum **`ContactType`** remains for API filters and create-time Vendor/Staff assignment.

### 3. When roles are written (service layer)

| Flow                                 | Role written                          |
| ------------------------------------ | ------------------------------------- |
| Admin unit allotment / owner link    | `Owner` (active, scoped to unit)      |
| Tenant request approved              | `Tenant` (active); prior tenant ended |
| Household member linked              | `Family` (active, + `relationship`)   |
| `POST /contacts` with `contact_type` | `Vendor` or `Staff` only (org-scoped) |
| Unit unassign / tenant supersede     | End active Owner/Tenant on that unit  |

Owner / Tenant / Family / Guest are **not** set on bare contact create — they require a unit link.

### 4. API and query behavior

- **List / overview:** filter and aggregate via **`contact_roles`** (`status = active`, `ended_at IS NULL`).
- **List items:** `role_types[]` — distinct active role types for the contact.
- **Detail:** `roles[]` — full role history (active and ended).
- **Create:** optional `contact_type` on `CreateContactRequest` → org-scoped Vendor/Staff only.
- **List filter:** query param / body `contact_type` filters contacts with a matching **active** role.

### 5. Relationship to `contact_units`

```
contacts          → identity (name, phone, auth, CRM)
contact_roles     → role label + optional unit + history
contact_units     → residency / portal / onboarding (linked via contact_unit_id)
```

- **`contact_units.relationship`** = household link semantics (`self`, `spouse`, `child`, …).
- **`contact_roles.role_type`** = operational label for billing, dashboards, and move-event display.
- Both are updated together in allotment, tenant approve, and household flows, but serve different purposes.

______________________________________________________________________

## Consequences

### Positive

- **History preserved** — superseded tenants and move-outs retain ended rows.
- **Multi-unit contacts** — one person can hold different roles on different units.
- **Clear separation** — identity vs role vs residency.
- **Billing anchor** — future fee modules can join on `contact_roles` + `unit_id` / `contact_unit_id`.

### Negative / trade-offs

- **Two concepts for newcomers** — `contact_units.relationship` vs `contact_roles.role_type`; docs and UI must explain both.
- **Backfill required** — deploy migrations before app code that omits `contacts.contact_type`.
- **No dedicated role CRUD API yet** — roles are managed through existing allotment, onboarding, tenant, and contact-create flows.

### Follow-ups

1. Optional admin API to assign/end roles without changing `contact_units`.
1. End **Family** roles when household links are removed (Owner/Tenant ending is wired today).
1. Index active roles in Typesense for search filters.
1. RLS policies on `contact_roles` when resident-facing reads are added.

______________________________________________________________________

## Alternatives considered

| Alternative                            | Why rejected                                                           |
| -------------------------------------- | ---------------------------------------------------------------------- |
| Keep `contacts.contact_type`           | Single value; no per-unit or historical roles                          |
| `contact_type_tags text[]` on contacts | Still identity-coupled; weak unit scope and history                    |
| Derive role only from `contact_units`  | Cannot represent org-scoped Vendor/Staff or ended role history cleanly |
| Rename table `contact_unit_roles`      | Vendor/Staff are org-scoped without a unit                             |
