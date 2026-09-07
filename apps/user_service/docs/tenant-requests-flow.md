# Tenant Requests Flow — Context & Change Guide

> **Status: Phase 1–2 implemented (API + service + migrations + occupancy turnover).** Storage signed-upload,
> portal invite are follow-ups. This document describes the **Tenant
> Requests** feature — owner submit on mobile, admin review on dashboard — in the same style as
> [`contact-onboarding-flow.md`](./contact-onboarding-flow.md), [`move-events-flow.md`](./move-events-flow.md),
> and [`passes-flow.md`](./passes-flow.md).
>
> Schema and architecture rationale: [ADR 0007](./adr/0007-tenant-requests.md), [ADR 0010](./adr/0010-contact-roles.md) (roles).

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **Owner API prefix:** `/v1/contact-onboarding/tenant-requests`
- **Admin API prefix:** `/v1/projects/{project_id}/tenant-requests`
- **DB schema:** `ats-home-craft-supabase` (migrations `20260722150000_*`, `20260722151000_*`,
  backfills `20260827140000_*`, `20260827150000_*`)

______________________________________________________________________

## 1. What this flow does

A **primary occupant** (`contact_units.relationship = self`) who has an **active unit assignment** can submit
a **tenant request** for that unit: prospective tenant profile, three documents, and an intended
move-in date. A **community admin** reviews each document independently, then approves or rejects
the request.

On **approval**:

1. **Household turnover** — `UnitOccupancyTurnoverService.release_outgoing_tenant_household()` clears
   outgoing tenant + family, vehicles, passes, daily help, invitations, and portal sessions (owner
   preserved). See [move-events-flow.md §2.1](./move-events-flow.md#21-unit-occupancy-turnover-admin-move-in--move-out).
1. If a prior approved tenant exists → supersede that request + record a **move-out** ledger row in
   `move_events`.
1. A real **`contacts`** row is created (identity only).
1. A **`contact_units`** link is created (`status = active`, `relationship = self`, tenant as primary occupant).
1. An active **`contact_roles`** row is created (`role_type = Tenant`, scoped to the unit).
1. A **move-in** ledger row is written to `move_events`.
1. The request moves to **`approved`** and appears in history forever.

### Business rules (must enforce)

| Rule                                           | Enforcement                                                                 |
| ---------------------------------------------- | --------------------------------------------------------------------------- |
| **One in-flight request per unit**             | Partial unique index + service check before create                          |
| **One active approved tenant per unit**        | Partial unique index; supersede previous on new approve                     |
| **No new request while tenant is active**      | `create_request` → `tenant_requests.errors.active_tenant_exists` (422)      |
| **Past history visible**                       | Never hard-delete requests; `superseded` retains row                        |
| **Submitter must be primary occupant on unit** | `contact_units` active link with `relationship = self`                      |
| **Three documents required to submit**         | `id_proof`, `rental_agreement`, `police_verification`                       |
| **Turnover on approve**                        | Full household cleanup via `UnitOccupancyTurnoverService` before new tenant |

### Screen → capability map

**Owner mobile**

| Screen / action              | Capability                                                                       |
| ---------------------------- | -------------------------------------------------------------------------------- |
| Tenant list (all statuses)   | `GET /contact-onboarding/tenant-requests?unit_id=`                               |
| Add tenant (form)            | `POST /contact-onboarding/tenant-requests` (or draft + PATCH)                    |
| Confirm submit               | `POST /contact-onboarding/tenant-requests/{id}/submit`                           |
| Status timeline              | `GET /contact-onboarding/tenant-requests/{id}` → `events[]` + derived milestones |
| Re-upload rejected docs      | `PATCH /contact-onboarding/tenant-requests/{id}/documents/{type}`                |
| Cancel pending request       | `POST /contact-onboarding/tenant-requests/{id}/cancel`                           |
| Resend tenant invite (later) | Reuse household invite pattern post-approval                                     |

**Admin dashboard**

| Screen element           | Capability                                                                                |
| ------------------------ | ----------------------------------------------------------------------------------------- |
| Summary cards            | `GET /projects/{project_id}/tenant-requests/summary`                                      |
| Table + filters + search | `GET /projects/{project_id}/tenant-requests?status=&search=`                              |
| Row detail + documents   | `GET /projects/{project_id}/tenant-requests/{id}`                                         |
| Verify document          | `POST /projects/{project_id}/tenant-requests/{id}/documents/{doc_id}/verify`              |
| Reject document          | `POST /projects/{project_id}/tenant-requests/{id}/documents/{doc_id}/reject` `{ reason }` |
| Approve request          | `POST /projects/{project_id}/tenant-requests/{id}/approve`                                |
| Export (later)           | `GET /projects/{project_id}/tenant-requests/export`                                       |

______________________________________________________________________

## 2. Architecture (layers)

Same 3-layer FastAPI pattern as the rest of the service:

```
HTTP → API router → Service (business rules) → Repository (SQL) → Postgres
```

### File map (to create)

| Concern                 | File                                                                                |
| ----------------------- | ----------------------------------------------------------------------------------- |
| Owner API endpoints     | `app/api/contact_onboarding_tenant_requests.py` (or extend `contact_onboarding.py`) |
| Admin API endpoints     | `app/api/tenant_requests.py`                                                        |
| Route registration      | `app/api/routes.py`                                                                 |
| Orchestration           | `app/services/tenant_requests_service.py`                                           |
| Household turnover      | `app/services/unit_occupancy_turnover_service.py` (composed on approve)             |
| Move-event ledger       | `app/db/repositories/move_events_repository.py` (approve supersede / move-in rows)  |
| Persistence             | `app/db/repositories/tenant_requests_repository.py`                                 |
| Request/response models | `app/schemas/tenant_requests.py`                                                    |
| Enums (mirror Postgres) | `app/schemas/enums.py`                                                              |
| Owner context           | `extract_onboarding_contact_context` in `app/utils/common_utils.py`                 |
| Admin RBAC              | Reuse `projects_management.*` (same as vehicle requests / project setup)            |
| Tenant contact creation | Compose `ContactsService` (same as household member add)                            |
| Unit link / supersede   | Compose `ContactUnitsRepository`                                                    |
| Audit logging           | `@audit_api_call` + `set_audit_context` (see contact-onboarding-flow.md)            |
| i18n                    | `app/locales/en.json` (`tenant_requests.*`)                                         |

`TenantRequestsService` **composes** existing services rather than duplicating contact/unit logic.

______________________________________________________________________

## 3. Data model

### New tables

| Table                          | Purpose                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------- |
| **`tenant_requests`**          | Header: unit, owner, tenant snapshot, status, approve metadata, supersede links |
| **`tenant_request_documents`** | One row per document slot; independent verify/reject                            |
| **`tenant_request_events`**    | Append-only timeline for mobile milestones + admin audit                        |

Full column reference: [ADR 0007 § Schema](./adr/0007-tenant-requests.md#schema-proposed).

### Reused tables

| Table                   | Role                                              |
| ----------------------- | ------------------------------------------------- |
| `contacts`              | Owner (submitter) + tenant (created on approve)   |
| `contact_units`         | Owner's existing link; new tenant link on approve |
| `units` / `projects`    | Unit picker, denormalized `project_id`            |
| `household_invitations` | Optional phase 2 — portal invite after approve    |

### Status lifecycle

```text
                    ┌─────────────┐
                    │ draft (opt) │
                    └──────┬──────┘
                           │ submit (3 docs)
                           ▼
                    ┌─────────────┐
         ┌─────────│  submitted  │─────────┐
         │         │ pending_review         │
         │         └──────┬──────┘         │
         │ admin rejects  │ admin verifies all
         │ any doc        │ docs
         ▼                ▼
┌────────────────┐  ┌───────────────┐
│ awaiting_      │  │ ready_to_     │
│ resubmission   │  │ approve       │
└────────┬───────┘  └───────┬───────┘
         │ re-upload        │ approve
         └────────► submitted      │
                                    ▼
                             ┌───────────┐
                             │ approved  │──► superseded (new tenant approved)
                             └───────────┘
         cancel (owner) ──► cancelled
```

### Mobile timeline milestones (derived)

| Milestone              | Source                                                      |
| ---------------------- | ----------------------------------------------------------- |
| **Request submitted**  | `tenant_request_events` type `submitted` (+ `submitted_at`) |
| **Documents verified** | All docs `verified` OR event `ready_to_approve`             |
| **Tenant added**       | Event `approved` + `tenant_contact_id` populated            |

Past requests remain listable with their final status (`approved`, `superseded`, `cancelled`).

______________________________________________________________________

## 4. Owner flow (step by step)

### 4.1 Preconditions

- Owner logged in (JWT → `contacts`).
- Owner has an **active `contact_roles` row** with `role_type = Owner` on the unit (or active owner
  `contact_units` link — product may allow co-owners later).
- Target unit: owner has **`contact_units.status = active`** for that `unit_id`.
- No other in-flight request on that unit.
- **No active tenant on the unit** — if a tenant is already moved in (via admin move-in or a prior
  approval), owner must wait for move-out before submitting (`active_tenant_exists`). Admin approval
  with supersede still handles replacing an approved tenant when an in-flight request was opened
  before that guard existed.
- If unit already has an approved tenant request in flight toward supersede, admin approve will
  **supersede** the previous tenant (turnover + new tenant).

### 4.2 Create + upload documents

```http
POST /v1/contact-onboarding/tenant-requests
{
  "unit_id": "...",
  "first_name": "Ankit",
  "last_name": "Kumar",
  "phones": [{ "phone_isd_code": "+91", "phone_number": "9876543210", "is_primary": true }],
  "emails": [{ "email": "ankit@example.com", "is_primary": true }],
  "move_in_date": "2026-08-01",
  "portal_access": false,
  "documents": [
    { "document_type": "id_proof", "file_path": "org/.../aadhar.pdf", "file_name": "aadhar.pdf" },
    { "document_type": "rental_agreement", "file_path": "...", "file_name": "rental_agreement.pdf" },
    { "document_type": "police_verification", "file_path": "...", "file_name": "police.jpg" }
  ]
}
```

Service:

1. Validates ownership + no in-flight request + **no active tenant** on unit.
1. Inserts `tenant_requests` (`status = submitted`).
1. Inserts 3 `tenant_request_documents` rows (`status = pending`).
1. Appends events: `created`, `submitted`.

> File upload to storage is **client → storage bucket → path in API** (same pattern as vehicle
> photos / move-event documents). A signed-upload helper may be added separately.

### 4.3 List + detail (history)

```http
GET /v1/contact-onboarding/tenant-requests
GET /v1/contact-onboarding/tenant-requests/{id}
```

List returns **all** requests for units the owner owns — pending, approved, superseded, cancelled —
sorted by `submitted_at DESC`. Detail includes `documents[]`, `events[]`, and derived `milestones[]`.

### 4.4 Re-upload after rejection

When admin rejects one or more documents:

```http
PATCH /v1/contact-onboarding/tenant-requests/{id}/documents/id_proof
{ "file_path": "org/.../aadhar_v2.pdf", "file_name": "aadhar_v2.pdf" }
```

Service resets that document to `pending`, clears `rejection_reason`, sets header back to
`submitted`, appends `resubmitted` event.

### 4.5 Cancel

Only while status is in-flight (`submitted`, `awaiting_resubmission`, `ready_to_approve`):

```http
POST /v1/contact-onboarding/tenant-requests/{id}/cancel
```

______________________________________________________________________

## 5. Admin flow (step by step)

### 5.1 Dashboard list

```http
GET /v1/projects/{project_id}/tenant-requests?status=pending_review&search=A-2104
```

Response rows match dashboard columns:

| Column               | Source                                   |
| -------------------- | ---------------------------------------- |
| Tenant               | `tenant_first_name` + `tenant_last_name` |
| Unit                 | join `units.code` + tower name           |
| Submitted by (owner) | join owner `contacts`                    |
| Move-in date         | `move_in_date`                           |
| Documents            | count verified / 3                       |
| Submitted on         | `submitted_at`                           |
| Status               | `tenant_requests.status`                 |

Summary cards:

| Card                  | Query                                         |
| --------------------- | --------------------------------------------- |
| Pending review        | `status IN (submitted, pending_review)`       |
| Awaiting resubmission | `status = awaiting_resubmission`              |
| Ready to approve      | `status = ready_to_approve`                   |
| Approved              | `status = approved AND superseded_at IS NULL` |
| Cancelled             | `status = cancelled`                          |

### 5.2 Per-document review

```http
POST /v1/projects/{project_id}/tenant-requests/{id}/documents/{doc_id}/verify
POST /v1/projects/{project_id}/tenant-requests/{id}/documents/{doc_id}/reject
{ "rejection_reason": "Rental agreement expired" }
```

After each action, service recomputes header status and appends `document_verified` /
`document_rejected` event. When all three verified → `ready_to_approve` + event.

### 5.3 Approve (creates tenant)

```http
POST /v1/projects/{project_id}/tenant-requests/{id}/approve
{
  "move_in_date": "2026-08-01",
  "move_in_fee": 5000,
  "admin_notes": "optional"
}
```

`move_in_date` is **required** at approval (admin confirms or sets the tenant move-in date).
`move_in_fee` is optional and defaults to **`0`** when omitted. It is stored on the request and
returned on **GET** detail/list responses as a string (e.g. `"5000.00"`).

Transactional steps:

1. Assert `status = ready_to_approve`.
1. Load any current approved request on the unit (for supersede ledger + events).
1. **`UnitOccupancyTurnoverService.release_outgoing_tenant_household()`** — clear outgoing household
   (family, vehicles, passes, daily help, invitations, portal sessions; owner preserved).
1. If a prior approved tenant existed → mark that request `superseded` + append event; record
   **move-out** in `move_events` for the old tenant.
1. `ContactsService.create_contact` (identity; auth provisioned from `portal_access` on the request).
1. `contact_units` insert (tenant, `is_primary = true`, `status = active`, `relationship = self`).
1. End prior `Tenant` roles on unit; insert new `Tenant` role.
1. Record **move-in** in `move_events` (with document snapshot from the request).
1. Update request: `approved`, `tenant_contact_id`, `contact_unit_id`, `approved_at`, **`move_in_date`** and **`move_in_fee`** (from request body).
1. Append `approved` event; push notification to tenant contact.

Returns created tenant summary + request snapshot.

### 5.4 Admin move-in mirror (`sync_after_admin_move_in`)

When staff records **move-in** via [`POST /v1/move-events`](./move-events-flow.md) instead of tenant
request approval, `MoveEventsService` calls `TenantRequestsService.sync_after_admin_move_in()` so
the **owner mobile list** still shows an approved row:

1. Skip if an approved request already exists for the same `tenant_contact_id`.
1. Else **approve** the newest in-flight request on the unit, if any.
1. Else **insert** a synthetic approved request for the unit owner (tenant snapshot from `contacts`).

Turnover on move-in is handled in `MoveEventsService` **before** this sync (same household cleanup
as approve). This method only reconciles the `tenant_requests` ledger — not occupancy cleanup.

Historical DB rows from move-ins before this sync existed: backfill migration
`20260827140000_backfill_tenant_requests_from_move_ins.sql`. Stale household artifacts:
`20260827150000_backfill_stale_unit_household_artifacts.sql` (no portal session revoke in SQL).

### 5.5 Admin move-out close (`sync_after_admin_move_out`)

When staff records **move-out** for the **active tenant** via `POST /v1/move-events`,
`MoveEventsService` calls `TenantRequestsService.sync_after_admin_move_out()` after household
turnover:

1. Find the active approved request (`status = approved`, `superseded_at IS NULL`) for that
   `tenant_contact_id` + `unit_id`.
1. Set `status = superseded`, `superseded_at = now()`.
1. Append a `superseded` event with payload `{ reason: "admin_move_out", move_event_id }`.

The owner list still shows the row in **history**, but it is no longer the current approved tenant
(mobile can treat `superseded` after move-out as “tenant moved out”). Family-only move-outs do not
touch the tenant request.

______________________________________________________________________

## 6. Relationship to existing flows

| Existing doc                                               | Relationship                                                                      |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------- |
| [contact-onboarding-flow.md](./contact-onboarding-flow.md) | Owner auth context; household/invite patterns for post-approval portal            |
| [move-events-flow.md](./move-events-flow.md)               | Admin move-in/out ledger; `sync_after_admin_move_in/out`; shared turnover service |
| [project-setup-flow.md](./project-setup-flow.md)           | Units must exist from project setup                                               |
| [passes-flow.md](./passes-flow.md)                         | Same owner JWT pattern; different domain                                          |
| [fee-flow.md](./fee-flow.md)                               | No direct coupling in phase 1                                                     |

### Difference from household member add

|                 | Household member                | Tenant request               |
| --------------- | ------------------------------- | ---------------------------- |
| Actor           | Owner (onboarding)              | Owner                        |
| Person created  | Immediately (`POST /household`) | Only after admin approve     |
| Documents       | None                            | Three required               |
| Admin review    | No                              | Yes                          |
| Unit constraint | Family link                     | One approved tenant per unit |

______________________________________________________________________

## 7. Error cases (i18n keys to add)

| Key                                                | When                                                                |
| -------------------------------------------------- | ------------------------------------------------------------------- |
| `tenant_requests.errors.unit_not_owned`            | Owner has no active link to unit                                    |
| `tenant_requests.errors.inflight_request_exists`   | Another open request on same unit                                   |
| `tenant_requests.errors.active_tenant_exists`      | Owner submit while unit has active tenant (move-out required first) |
| `tenant_requests.errors.documents_incomplete`      | Submit without 3 docs                                               |
| `tenant_requests.errors.not_ready_to_approve`      | Admin approve before all docs verified                              |
| `tenant_requests.errors.invalid_status_transition` | Cancel approved request, etc.                                       |
| `tenant_requests.errors.document_not_rejected`     | Re-upload when doc not rejected                                     |

______________________________________________________________________

## 8. Implementation phases

| Phase        | Scope                                                                                   |
| ------------ | --------------------------------------------------------------------------------------- |
| **1**        | Migrations + enums + repositories + owner create/list/detail/submit/cancel/re-upload    |
| **2**        | Admin list/summary + document verify/reject + approve + supersede + turnover cleanup    |
| **2b**       | Move-event ledger on approve; `sync_after_admin_move_in`; active-tenant submit guard    |
| **3**        | Storage signed upload helper + audit logging on all writes                              |
| **4**        | Post-approval portal invite (SMS)                                                       |
| **5**        | RLS policies + export                                                                   |
| **Backfill** | `20260827140000_*` tenant_requests mirror; `20260827150000_*` stale household artifacts |

______________________________________________________________________

## 9. Where to change things (quick reference)

| Change                                  | Location                                                                         |
| --------------------------------------- | -------------------------------------------------------------------------------- |
| Add document type                       | Migration enum + `TenantRequestDocumentType` + UI copy                           |
| Change approval / turnover side effects | `tenant_requests_service.approve_request` + `unit_occupancy_turnover_service.py` |
| Change move-in mirror for admin moves   | `tenant_requests_service.sync_after_admin_move_in`                               |
| Change move-out close for admin moves   | `tenant_requests_service.sync_after_admin_move_out`                              |
| Owner submit guards                     | `create_request` → `_assert_no_active_tenant_on_unit`                            |
| Owner ownership rules                   | `_assert_owner_can_access_unit` in service                                       |
| Timeline copy                           | `_derive_milestones` in service                                                  |
| Admin RBAC                              | `tenant_requests.py` — `PROJECTS_MANAGEMENT_*` (same as vehicle requests)        |
| Supersede behavior                      | `approve_request` + partial unique indexes + move_events ledger                  |

______________________________________________________________________

## 10. Tests

- `tests/unit/test_tenant_requests_service.py` — create/submit guards, approve + supersede,
  `sync_after_admin_move_in`, active-tenant submit block.
- `tests/unit/test_unit_occupancy_turnover_service.py` — shared household cleanup (with move-events).
- `tests/integration/tenant_requests/` — API smoke tests.

Run: `.venv/bin/python -m pytest apps/user_service/tests/unit/test_tenant_requests_service.py`
