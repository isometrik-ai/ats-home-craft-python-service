# Tenant Requests Flow — Context & Change Guide

> **Status: Phase 1 implemented (API + service + migrations).** Storage signed-upload,
> portal invite, and move-event hooks are follow-ups. This document describes the **Tenant
> Requests** feature — owner submit on mobile, admin review on dashboard — in the same style as
> [`contact-onboarding-flow.md`](./contact-onboarding-flow.md), [`move-events-flow.md`](./move-events-flow.md),
> and [`passes-flow.md`](./passes-flow.md).
>
> Schema and architecture rationale: [ADR 0007](./adr/0007-tenant-requests.md).

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **Owner API prefix:** `/v1/contact-onboarding/tenant-requests`
- **Admin API prefix:** `/v1/tenant-requests`
- **DB schema:** `ats-home-craft-supabase` (migrations `20260722150000_*`, `20260722151000_*`)

______________________________________________________________________

## 1. What this flow does

An **owner** (`contacts.contact_type = Owner`) who has an **active unit assignment** can submit
a **tenant request** for that unit: prospective tenant profile, three documents, and an intended
move-in date. A **community admin** reviews each document independently, then approves or rejects
the request.

On **approval**:

1. A real **`contacts`** row is created (`contact_type = Tenant`).
1. A **`contact_units`** link is created (`status = active`, tenant as primary occupant).
1. The request moves to **`approved`** and appears in history forever.

### Business rules (must enforce)

| Rule                                    | Enforcement                                             |
| --------------------------------------- | ------------------------------------------------------- |
| **One in-flight request per unit**      | Partial unique index + service check before create      |
| **One active approved tenant per unit** | Partial unique index; supersede previous on new approve |
| **Past history visible**                | Never hard-delete requests; `superseded` retains row    |
| **Owner can only act on owned units**   | Join `contact_units` where owner contact is active      |
| **Three documents required to submit**  | `id_proof`, `rental_agreement`, `police_verification`   |

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

| Screen element           | Capability                                                          |
| ------------------------ | ------------------------------------------------------------------- |
| Summary cards            | `GET /tenant-requests/summary`                                      |
| Table + filters + search | `GET /tenant-requests?status=&search=`                              |
| Row detail + documents   | `GET /tenant-requests/{id}`                                         |
| Verify document          | `POST /tenant-requests/{id}/documents/{doc_id}/verify`              |
| Reject document          | `POST /tenant-requests/{id}/documents/{doc_id}/reject` `{ reason }` |
| Approve request          | `POST /tenant-requests/{id}/approve`                                |
| Export (later)           | `GET /tenant-requests/export`                                       |

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
| Persistence             | `app/db/repositories/tenant_requests_repository.py`                                 |
| Request/response models | `app/schemas/tenant_requests.py`                                                    |
| Enums (mirror Postgres) | `app/schemas/enums.py`                                                              |
| Owner context           | `extract_onboarding_contact_context` in `app/utils/common_utils.py`                 |
| Admin RBAC              | Reuse `contacts_management.*` (same as move events / contacts)                      |
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
- Owner has **`contact_type = Owner`** (or active owner link — confirm with product).
- Target unit: owner has **`contact_units.status = active`** for that `unit_id`.
- No other in-flight request on that unit.
- If unit already has an approved tenant, owner may still submit — approval will **supersede** the
  previous tenant (admin action).

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

1. Validates ownership + no in-flight request.
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
GET /v1/tenant-requests?status=pending_review&search=A-2104
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

| Card                  | Query                                              |
| --------------------- | -------------------------------------------------- |
| Pending review        | `status IN (submitted, pending_review)`            |
| Awaiting resubmission | `status = awaiting_resubmission`                   |
| Ready to approve      | `status = ready_to_approve`                        |
| Approved this month   | `status = approved AND approved_at >= month_start` |

### 5.2 Per-document review

```http
POST /v1/tenant-requests/{id}/documents/{doc_id}/verify
POST /v1/tenant-requests/{id}/documents/{doc_id}/reject
{ "rejection_reason": "Rental agreement expired" }
```

After each action, service recomputes header status and appends `document_verified` /
`document_rejected` event. When all three verified → `ready_to_approve` + event.

### 5.3 Approve (creates tenant)

```http
POST /v1/tenant-requests/{id}/approve
{
  "move_in_date": "2026-08-01",
  "admin_notes": "optional"
}
```

`move_in_date` is **required** at approval (admin confirms or sets the tenant move-in date).

Transactional steps:

1. Assert `status = ready_to_approve`.
1. If unit has current approved request → supersede old + `moved_out` old tenant link.
1. `ContactsService.create_contact` (`contact_type = Tenant`, `provision_auth = !portal_access`).
1. `contact_units` insert (tenant, `is_primary = true`, `status = active`).
1. Update request: `approved`, `tenant_contact_id`, `contact_unit_id`, `approved_at`, **`move_in_date`** (from request body).
1. Append `approved` + `tenant_added` events.

Returns created tenant summary + request snapshot.

______________________________________________________________________

## 6. Relationship to existing flows

| Existing doc                                               | Relationship                                                           |
| ---------------------------------------------------------- | ---------------------------------------------------------------------- |
| [contact-onboarding-flow.md](./contact-onboarding-flow.md) | Owner auth context; household/invite patterns for post-approval portal |
| [move-events-flow.md](./move-events-flow.md)               | Optional auto `move_in` on approve; `moved_out` on supersede           |
| [project-setup-flow.md](./project-setup-flow.md)           | Units must exist from project setup                                    |
| [passes-flow.md](./passes-flow.md)                         | Same owner JWT pattern; different domain                               |
| [fee-flow.md](./fee-flow.md)                               | No direct coupling in phase 1                                          |

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

| Key                                                | When                                   |
| -------------------------------------------------- | -------------------------------------- |
| `tenant_requests.errors.unit_not_owned`            | Owner has no active link to unit       |
| `tenant_requests.errors.inflight_request_exists`   | Another open request on same unit      |
| `tenant_requests.errors.documents_incomplete`      | Submit without 3 docs                  |
| `tenant_requests.errors.not_ready_to_approve`      | Admin approve before all docs verified |
| `tenant_requests.errors.invalid_status_transition` | Cancel approved request, etc.          |
| `tenant_requests.errors.document_not_rejected`     | Re-upload when doc not rejected        |
| `tenant_requests.errors.active_tenant_exists`      | Rare — unique index race               |

______________________________________________________________________

## 8. Implementation phases

| Phase | Scope                                                                                |
| ----- | ------------------------------------------------------------------------------------ |
| **1** | Migrations + enums + repositories + owner create/list/detail/submit/cancel/re-upload |
| **2** | Admin list/summary + document verify/reject + approve + supersede logic              |
| **3** | Storage signed upload helper + audit logging on all writes                           |
| **4** | Post-approval portal invite (SMS) + move-event integration                           |
| **5** | RLS policies + export                                                                |

______________________________________________________________________

## 9. Where to change things (quick reference)

| Change                       | Location                                               |
| ---------------------------- | ------------------------------------------------------ |
| Add document type            | Migration enum + `TenantRequestDocumentType` + UI copy |
| Change approval side effects | `tenant_requests_service.approve_request`              |
| Owner ownership rules        | `_assert_owner_can_access_unit` in service             |
| Timeline copy                | `_derive_milestones` in service                        |
| Admin RBAC                   | `tenant_requests.py` router permissions                |
| Supersede behavior           | `approve_request` + partial unique indexes             |
