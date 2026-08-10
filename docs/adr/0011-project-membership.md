# ADR 0011: Project membership — org layer + project layer

|                  |                                                                                                                                                                                                                                                                                    |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Proposed                                                                                                                                                                                                                                                                           |
| **Date**         | 2026-08-06                                                                                                                                                                                                                                                                         |
| **Authors**      | Home Craft platform team                                                                                                                                                                                                                                                           |
| **Related docs** | [membership-architecture.md](../membership-architecture.md), [membership-schema.md](../../../ats-home-craft-supabase/docs/membership-schema.md), [ADR 0010](./0010-contact-roles.md), [ADR 0001](./0001-resident-onboarding.md), [project-setup-flow.md](../project-setup-flow.md) |
| **Migrations**   | Existing: `20250821124646_initial_execute.sql`, `20260629101000_property_setup_tables.sql`. Follow-ups: `project_members` role enum, optional `teams.project_id`, new permission codes.                                                                                            |

______________________________________________________________________

## Terminology

| Context                | Canonical term   | Notes                                           |
| ---------------------- | ---------------- | ----------------------------------------------- |
| Database, API, code    | **project**      | `projects`, `project_id`, `project_members`     |
| Staff UI (optional)    | **community**    | Display label only; maps to `projects`          |
| Resident UI (optional) | **property**     | e.g. "My properties"; still `project_id` in API |
| SaaS multi-tenancy     | **organization** | Not "tenant" (overloaded with renter role)      |

Do **not** introduce a separate `societies` table or `society_id`. One **project** = one gated community / development.

______________________________________________________________________

## Context

The platform serves **property management companies** (organizations) that operate **multiple gated
communities** (`projects`). Users appear in several shapes:

- **Staff** — org employees (admins, project managers, gate security, CRM sales).
- **Residents** — people living in units (`contacts` + `contact_units`).

Both personas can belong to **multiple projects within the same organization**. The session is
**org-scoped** (`user_sessions.organization_id`); project scope is resolved per request.

Questions that must be answered consistently:

1. Should `organization_members` be project-wise?
1. How do staff get access to specific projects?
1. How do residents appear in multiple projects?
1. Should `teams` be org-wide or project-wise?
1. How do permissions combine org RBAC with project assignment?

Today:

- `organization_members` is org-wide with RBAC via `roles` / `permissions`.
- `project_members` exists but is minimal (default role `community_admin`; used for `/projects/mine`
  and community-admin assignment on project create).
- `teams` / `team_members` are org-wide CRM groupings with no `project_id`.
- `contacts` are org-scoped identity; project membership is via `contact_units` + `contact_roles`
  (ADR 0010).
- RBAC checks are org-only (`check_user_access_async` joins `organization_members` → permissions).
  Project-scoped APIs do not yet uniformly enforce `project_members` assignment.

______________________________________________________________________

## Decision

### 1. Two-layer membership model (do not collapse layers)

| Layer                     | Table                             | Meaning                                                                             |
| ------------------------- | --------------------------------- | ----------------------------------------------------------------------------------- |
| **Org gateway**           | `organization_members`            | Person works for this property management company; enables staff login and org RBAC |
| **Project assignment**    | `project_members`                 | Person is assigned to this project; gates which projects they operate on            |
| **Resident identity**     | `contacts`                        | One person record per org (identity + auth)                                         |
| **Resident project link** | `contact_units` + `contact_roles` | Which units / projects the resident belongs to                                      |

**Rule:** Never move `organization_members` to project scope. Never duplicate org member rows per
project.

### 2. Staff access formula

```
Can staff user U perform action A on project P?

1. U is an active organization_member in session org
2. U's org role grants permission code for A
3. EITHER U has org-wide permission (e.g. projects_management.view)
   OR U has an active project_members row for P
```

Org-wide admins bypass step 3. Project-scoped staff require step 3.

### 3. Resident access formula

```
Can resident contact C access data for project P?

1. C is the active contact for session user in session org
2. C has an active contact_units row with project_id = P
   (and optionally matching contact_roles for billing / dashboards)
```

One contact row; multiple projects via junction tables (ADR 0010).

### 4. Teams — two types, one table with optional project scope

| Team type            | `teams.project_id`   | Use case                                    |
| -------------------- | -------------------- | ------------------------------------------- |
| **Org CRM team**     | `NULL`               | Sales / marketing across all projects       |
| **Project ops team** | Set to `projects.id` | Gate crew, maintenance, project admin group |

`team_members.user_id` must reference a user who is an active `organization_members` row in the
same org. Project ops teams are a grouping convenience; **`project_members` remains the access
control source of truth** for staff.

### 5. Data scoping summary

| Data                                    | Scope                            |
| --------------------------------------- | -------------------------------- |
| Staff identity, org RBAC, org settings  | `organization_id`                |
| CRM: companies, leads (unless filtered) | `organization_id`                |
| Project layout: towers, units, gates    | `organization_id` + `project_id` |
| Staff project assignment                | `project_members`                |
| Resident identity                       | `contacts.organization_id`       |
| Resident project membership             | `contact_units.project_id`       |
| Visitor passes, fees, move events       | `organization_id` + `project_id` |
| Notices (community board)               | `organization_id` + `project_id` |

### 6. Terminology (avoid overload)

| Term in product copy      | Database entity                                       |
| ------------------------- | ----------------------------------------------------- |
| Tenant / multi-tenant     | `organizations`                                       |
| Project / gated community | `projects`                                            |
| Resident                  | `contacts` + `contact_units`                          |
| Renter (role)             | `contact_roles.role_type = 'Tenant'`                  |
| Tenant request (workflow) | `tenant_requests` (rental approval — not SaaS tenant) |

### 7. Implementation phases

**Phase 1 — Document & enforce existing model**

- Document two-layer staff model (this ADR + architecture guide).
- Add `ensure_project_access()` helper used by project-scoped APIs.
- Split permission codes: `projects_management.view` (all) vs `projects_management.view_assigned`.

**Phase 2 — Extend `project_members`**

- Replace free-text `role` with enum: `community_admin`, `security`, `accountant`, `facility_manager`, `viewer`.
- Expose CRUD APIs: assign / remove / list staff per project.
- Validate assignee is active `organization_members` row (same rule as `community_admin_user_id`).

**Phase 3 — Teams (optional)**

- Add nullable `teams.project_id`.
- Filter team list by org vs project context.

**Phase 4 — Contact registry filters**

- Add optional `project_id` filter to `POST /contacts/list` via `contact_units` join.
- Add `project_ids[]` facet to Typesense contact index.

______________________________________________________________________

## Consequences

### Positive

- **Clear mental model** — same pattern for staff and residents: identity at org layer, assignment
  at project layer.
- **Reuses existing tables** — no third tenancy tier; `project_members` already exists.
- **Supports multi-project users** — one org member + N project members; one contact + N contact_units.
- **Flexible admin model** — org-wide admins see all projects; project staff see assigned only.
- **Consistent naming** — "project" in code, API, and docs aligns with `projects` table.

### Negative / trade-offs

- **Two concepts for staff** — org membership vs project assignment; UI must explain both.
- **RBAC remains org-scoped** — project-specific capabilities require combining permission codes
  with `project_members` checks (not project-scoped roles table).
- **Teams duplication risk** — project teams and `project_members` can drift if not kept in sync;
  document that `project_members` is authoritative for access.

### Follow-ups

1. Implement `ensure_project_access()` in `common_utils.py` or a dedicated access service.
1. Add project staff management endpoints under `/v1/projects/{project_id}/members`.
1. Audit project-scoped APIs (passes, visitor logs, fees, move events) for project access enforcement.
1. Optional: `user_sessions.active_project_id` for server-side default project context.
1. RLS policies when client-side Supabase reads are added.

______________________________________________________________________

## Alternatives considered

| Alternative                                  | Why rejected                                                                         |
| -------------------------------------------- | ------------------------------------------------------------------------------------ |
| Make `organization_members` project-scoped   | Breaks org login, RBAC, unique `(user_id, organization_id)`, cross-project HQ staff  |
| Duplicate `organization_members` per project | Violates unique constraint; fragments staff identity and auth                        |
| Replace `project_members` with teams only    | Teams lack status lifecycle and RBAC integration; not suitable as sole access gate   |
| Add `societies` table above `projects`       | Unnecessary for current domain; `projects` already represent one gated community     |
| Project-scoped RBAC (`project_roles` table)  | Heavier model; defer until fine-grained per-project permission matrices are required |
| Separate contact row per project             | Blocked by `uq_contacts_user_org`; splits identity and portal login incorrectly      |
