# ADR 0007: Tenant requests — owner submit, admin review

|                  |                                                                                                                                                                      |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Accepted (Phase 1)                                                                                                                                                   |
| **Date**         | 2026-07-22                                                                                                                                                           |
| **Authors**      | Home Craft platform team                                                                                                                                             |
| **Depends on**   | [ADR 0001](./0001-resident-onboarding.md) (`contacts`, `contact_units`), [ADR 0002](./0002-resident-onboarding-implementation.md), [ADR 0005](./0005-move-events.md) |
| **Related docs** | [tenant-requests-flow.md](../tenant-requests-flow.md), [contact-onboarding-flow.md](../contact-onboarding-flow.md)                                                   |
| **Migrations**   | `20260722150000_tenant_requests_enums.sql`, `20260722151000_tenant_requests_tables.sql` (to be created in `ats-home-craft-supabase`)                                 |

______________________________________________________________________

## Context

Contact Onboarding lets **owners** link to units and manage household members. The next mobile
feature is **Add Tenant**: an owner submits a prospective tenant (profile + documents) for a
unit they own; a **community admin** reviews each document independently and approves the
tenancy.

### Screens (product)

**Owner mobile**

| Screen                | Capability                                                             |
| --------------------- | ---------------------------------------------------------------------- |
| Tenant list           | Past + in-flight requests per owned unit (status badge)                |
| Add tenant form       | Unit picker, name, phone, email, 3 document uploads                    |
| Confirm submit        | Review snapshot → submit for approval                                  |
| Status timeline       | Request submitted → Documents verified → Tenant added                  |
| Re-upload             | After partial doc rejection, replace only rejected files               |
| Renew / resend invite | After docs verified, (re)send portal invite to tenant (optional phase) |

**Admin dashboard ("Tenant Requests")**

| Element             | Capability                                                                             |
| ------------------- | -------------------------------------------------------------------------------------- |
| Summary cards       | Pending review, awaiting resubmission, ready to approve, approved this month           |
| Table               | Tenant, unit, submitted by (owner), move-in date, documents, submitted on, status      |
| Filters / search    | Status tabs + search by unit or tenant name                                            |
| Per-document review | Verify or reject each of ID proof, rental agreement, police verification independently |
| Approve             | Final approval → create tenant contact + unit link                                     |

### Constraints (carried from ADR 0001 / 0003 / 0005)

- Multi-tenancy via **`organization_id`** on every new table and query.
- **Owner actor** = logged-in **`contacts`** row (`contact_type = Owner`) resolved via
  `extract_onboarding_contact_context()` — same as contact onboarding / passes. No
  `*_MANAGEMENT_*` RBAC on owner routes.
- **Admin actor** = `organization_member` via `check_permissions(contacts_management.*)` —
  same as move events / contacts admin.
- Reuse **`contacts`**, **`contact_units`**, **`units`** — do not duplicate person or inventory.
- Document files store **storage paths only** (`text` / `text[]`) — no blobs in Postgres (same as
  `vehicles.photo_paths`, `move_events.document_paths`).
- RLS enabled, **policies deferred** (backend `service_role`), matching earlier phases.
- **Per unit, at most one currently approved tenant** — enforced in DB + service (see §4).
- **Full history retained** — requests are never hard-deleted; list APIs return past rows.

______________________________________________________________________

## Decision

### 1. Three new tables — request header, documents, event log

| Table                          | Purpose                                                             |
| ------------------------------ | ------------------------------------------------------------------- |
| **`tenant_requests`**          | One owner-initiated tenancy request (unit, tenant snapshot, status) |
| **`tenant_request_documents`** | One row per required document slot (independent verify/reject)      |
| **`tenant_request_events`**    | Append-only timeline / audit (submitted, doc verified, approved, …) |

All carry **`organization_id NOT NULL`**. `tenant_requests` also carries **`project_id`**
(denormalized from `units`, like `contact_units` / `vehicles` / `move_events`).

> **Why not one JSONB column for documents:** the admin UI verifies **each document
> independently** with its own rejection reason and timestamps. Normalized rows match the
> vehicle review pattern (`vehicles.status` + `rejection_reason`) but with three fixed slots.
>
> **Why an events table:** the mobile timeline ("Request submitted", "Documents verified",
> "Tenant added") and admin audit need **past history** even after status changes. Append-only
> events mirror `pass_events` / move-event ledger thinking without overloading the header row.

### 2. Prospective tenant is a snapshot until approval — then a real `contacts` row

While the request is in review, tenant identity lives **on the request** (name, phone, email
jsonb). The backend **does not** create a `contacts` row at submit time.

On **approval**, the service:

1. Creates (or reuses) a **`contacts`** row with `contact_type = Tenant` via
   `ContactsService.create_contact` / `_provision_contact_auth_identity` (same as household).
1. Creates **`contact_units`** for the unit with `status = active`, `is_primary = true`,
   `relationship = self` (tenant is the primary occupant).
1. Sets `tenant_requests.tenant_contact_id` and `tenant_requests.contact_unit_id`.
1. Appends `tenant_added` event.

**Rationale:** avoids orphan contacts when requests are rejected or cancelled; matches
"submit → review → activate" product flow.

### 3. Request status vs document status

**`tenant_request_status`** (on header):

| Status                  | Meaning                                                                 |
| ----------------------- | ----------------------------------------------------------------------- |
| `draft`                 | Owner saved but not submitted (optional — can omit if create-on-submit) |
| `submitted`             | Owner submitted; admin review not started                               |
| `pending_review`        | Alias / same bucket as submitted for admin filters                      |
| `awaiting_resubmission` | ≥1 document `rejected`; owner must re-upload                            |
| `ready_to_approve`      | All documents `verified`; admin may approve                             |
| `approved`              | Tenant contact + unit link created                                      |
| `cancelled`             | Owner cancelled in-flight request                                       |
| `superseded`            | Was approved but replaced by a newer approved tenant on the same unit   |

**`tenant_request_document_status`** (per document row):

| Status     | Meaning                          |
| ---------- | -------------------------------- |
| `pending`  | Uploaded, not reviewed           |
| `verified` | Admin accepted                   |
| `rejected` | Admin rejected (reason required) |

Header status is **derived in the service** after each document action:

```text
any doc rejected          → awaiting_resubmission
all docs verified         → ready_to_approve
owner re-uploads rejected → reset those docs to pending; header → submitted / pending_review
admin approves            → approved (+ side effects)
```

### 4. One active approved tenant per unit (hard rule)

Two partial unique indexes on `tenant_requests`:

```sql
-- Only one in-flight request per unit
CREATE UNIQUE INDEX uq_tenant_requests_one_inflight_per_unit
  ON tenant_requests (unit_id)
  WHERE status IN (
    'draft', 'submitted', 'pending_review',
    'awaiting_resubmission', 'ready_to_approve'
  );

-- Only one current approved tenant per unit
CREATE UNIQUE INDEX uq_tenant_requests_one_active_approved_per_unit
  ON tenant_requests (unit_id)
  WHERE status = 'approved' AND superseded_at IS NULL;
```

**Approval when a prior approved tenant exists:** before inserting the new approved row's side
effects, the service:

1. Marks the previous approved request `status = superseded`, `superseded_at = now()`.
1. Sets the previous tenant's `contact_units.status = moved_out`, `moved_out_at = now()` (reuse
   `ContactUnitStatus.MOVED_OUT` from ADR 0005).
1. Optionally records a `move_out` move event (follow-up integration with move-events flow).

This preserves **history** (old request still `approved` → `superseded`, still listed) while
enforcing **one live tenant per unit**.

Also align with existing occupant constraint:

```sql
-- already exists (ADR 0001)
uq_contact_units_primary_per_unit ON contact_units (unit_id)
  WHERE is_primary = true AND status = 'active'
```

Approval must ensure only **one** `is_primary = true` active link per unit (tenant becomes
primary occupant; owner link remains but typically `is_primary = false`).

### 5. Owner authorization

Owner routes validate:

1. JWT → `contacts` via `extract_onboarding_contact_context()`.
1. `contact_type = Owner` (or active owner link on the unit — product may allow co-owners later).
1. Active `contact_units` row: `contact_id = owner`, `unit_id = request.unit_id`,
   `status = active`, owner contact type.

Admin routes use **`contacts_management.view`** (list/detail) and **`contacts_management.edit`**
(document verify, approve, reject).

### 6. Required documents (fixed enum)

**`tenant_request_document_type`:**

| Value                 | UI label            |
| --------------------- | ------------------- |
| `id_proof`            | ID Proof            |
| `rental_agreement`    | Rental Agreement    |
| `police_verification` | Police Verification |

Exactly **three rows** per request (created at submit). Re-upload updates the same row's
`file_path` and resets `status → pending`.

### 7. API surface (two routers)

| Actor | Prefix                                   | Auth                                       |
| ----- | ---------------------------------------- | ------------------------------------------ |
| Owner | `/v1/contact-onboarding/tenant-requests` | `extract_onboarding_contact_context`       |
| Admin | `/v1/tenant-requests`                    | `check_permissions(contacts_management.*)` |

See [tenant-requests-flow.md](../tenant-requests-flow.md) for endpoint catalogue and file map.

### 8. Integration with existing flows

| Existing flow                  | Integration point                                                        |
| ------------------------------ | ------------------------------------------------------------------------ |
| Contact onboarding             | Owner must have completed onboarding + active owned unit                 |
| `contacts` / `ContactsService` | Create tenant contact on approve                                         |
| `contact_units`                | Create tenant link; sync `moved_out` on supersede                        |
| Move events (optional)         | Auto `move_in` on approve, `move_out` on supersede                       |
| Household invitations          | Post-approval `portal_access` + SMS invite (same as household — phase 2) |
| Vehicles                       | Separate flow; no schema coupling                                        |
| Fee configuration              | No coupling in phase 1                                                   |

______________________________________________________________________

## Schema (proposed)

### Enums

```sql
CREATE TYPE public.tenant_request_status AS ENUM (
  'draft',
  'submitted',
  'pending_review',
  'awaiting_resubmission',
  'ready_to_approve',
  'approved',
  'cancelled',
  'superseded'
);

CREATE TYPE public.tenant_request_document_type AS ENUM (
  'id_proof',
  'rental_agreement',
  'police_verification'
);

CREATE TYPE public.tenant_request_document_status AS ENUM (
  'pending',
  'verified',
  'rejected'
);

CREATE TYPE public.tenant_request_event_type AS ENUM (
  'created',
  'submitted',
  'document_uploaded',
  'document_verified',
  'document_rejected',
  'resubmitted',
  'ready_to_approve',
  'approved',
  'cancelled',
  'superseded',
  'tenant_invite_sent'
);
```

### `tenant_requests`

| Column                      | Type                           | Notes                                    |
| --------------------------- | ------------------------------ | ---------------------------------------- |
| `id`                        | uuid PK                        |                                          |
| `organization_id`           | uuid NOT NULL                  | → `organizations`                        |
| `project_id`                | uuid NOT NULL                  | denormalized from `units`                |
| `unit_id`                   | uuid NOT NULL                  | → `units`                                |
| `submitted_by_contact_id`   | uuid NOT NULL                  | owner → `contacts`                       |
| `tenant_first_name`         | text NOT NULL                  | snapshot until approval                  |
| `tenant_last_name`          | text                           |                                          |
| `tenant_phones`             | jsonb NOT NULL DEFAULT `[]`    | same shape as `contacts.phones`          |
| `tenant_emails`             | jsonb NOT NULL DEFAULT `[]`    |                                          |
| `move_in_date`              | date                           | optional intended move-in                |
| `status`                    | tenant_request_status NOT NULL | default `draft` or `submitted`           |
| `portal_access`             | boolean NOT NULL DEFAULT false | invite tenant after approval (phase 2)   |
| `tenant_contact_id`         | uuid NULL                      | set on approve → `contacts`              |
| `contact_unit_id`           | uuid NULL                      | set on approve → `contact_units`         |
| `approved_at`               | timestamptz                    |                                          |
| `approved_by_user_id`       | uuid                           | admin org member                         |
| `superseded_at`             | timestamptz                    | when replaced by newer tenant            |
| `superseded_by_request_id`  | uuid                           | → `tenant_requests`                      |
| `cancelled_at`              | timestamptz                    |                                          |
| `submitted_at`              | timestamptz                    |                                          |
| `admin_notes`               | text                           | optional internal note on approve/reject |
| `created_at` / `updated_at` | timestamptz                    |                                          |

Indexes: `(organization_id, status)`, `(organization_id, unit_id)`,
`(submitted_by_contact_id, created_at DESC)`, partial uniques in §4.

### `tenant_request_documents`

| Column                | Type                               | Notes                                 |
| --------------------- | ---------------------------------- | ------------------------------------- |
| `id`                  | uuid PK                            |                                       |
| `organization_id`     | uuid NOT NULL                      |                                       |
| `tenant_request_id`   | uuid NOT NULL                      | → `tenant_requests` ON DELETE CASCADE |
| `document_type`       | tenant_request_document_type       |                                       |
| `file_path`           | text NOT NULL                      | storage bucket path                   |
| `file_name`           | text                               | original filename for UI              |
| `status`              | tenant_request_document_status     | default `pending`                     |
| `rejection_reason`    | text                               | required when rejected                |
| `verified_at`         | timestamptz                        |                                       |
| `verified_by_user_id` | uuid                               | admin                                 |
| `uploaded_at`         | timestamptz NOT NULL DEFAULT now() |                                       |
| `updated_at`          | timestamptz                        |                                       |

Unique: `(tenant_request_id, document_type)`.

### `tenant_request_events`

| Column              | Type                               | Notes                                 |
| ------------------- | ---------------------------------- | ------------------------------------- |
| `id`                | uuid PK                            |                                       |
| `organization_id`   | uuid NOT NULL                      |                                       |
| `tenant_request_id` | uuid NOT NULL                      | → `tenant_requests` ON DELETE CASCADE |
| `event_type`        | tenant_request_event_type          |                                       |
| `actor_contact_id`  | uuid                               | owner actions                         |
| `actor_user_id`     | uuid                               | admin actions                         |
| `payload`           | jsonb NOT NULL DEFAULT `{}`        | doc type, rejection reason, etc.      |
| `occurred_at`       | timestamptz NOT NULL DEFAULT now() |                                       |

Index: `(tenant_request_id, occurred_at)`.

______________________________________________________________________

## Consequences

### Positive

- **Clear separation** — review workflow without polluting `contacts` until approved.
- **Independent document review** — matches admin UI; rejection + re-upload is straightforward.
- **Full history** — events + non-destructive supersede; owner and admin can list past requests.
- **Reuses person model** — approved tenants become normal `contacts` + `contact_units` rows.
- **Enforced 1-tenant-per-unit** — partial unique indexes + service checks.

### Negative / trade-offs

- **Snapshot vs live contact** — tenant phone/email on request may drift before approval; approve
  uses snapshot (acceptable for v1).
- **Supersede complexity** — approval must transactionally update old tenant link + old request.
- **Storage upload** — file upload endpoint / bucket wiring is a separate task (same gap as vehicles).
- **No RLS yet** — backend-only authorization until policy migration.

### Follow-ups

1. Implement per [tenant-requests-flow.md](../tenant-requests-flow.md).
1. Post-approval tenant portal invite (reuse `household_invitation_service` patterns or dedicated SMS).
1. Link approve → `move_events` auto `move_in`.
1. Admin export (CSV) for dashboard.
1. RLS policies keyed on `organization_id` and owner `contact_id`.
1. Optional: `draft` status if mobile needs save-and-continue before submit.

______________________________________________________________________

## Alternatives considered

| Alternative                                | Why rejected                                                |
| ------------------------------------------ | ----------------------------------------------------------- |
| Create `contacts` row at submit time       | Orphan contacts on reject/cancel; harder GDPR cleanup       |
| Single JSONB `documents` column on header  | Poor per-doc audit; awkward independent verify UI           |
| Reuse `vehicles` table for documents       | Wrong domain; vehicle has registration/parking semantics    |
| Only `contact_units` without request table | No review workflow, no document state, no history           |
| Hard-delete old requests on new approval   | Violates "show past history" requirement                    |
| Allow multiple in-flight requests per unit | Confusing for admin; product shows one pending row per unit |
