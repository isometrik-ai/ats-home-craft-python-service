# Membership Architecture — Multi-Tenant Projects

> How organizations, projects, staff, residents, and teams fit together.
> **Status:** Proposed (see [ADR 0011](./adr/0011-project-membership.md)).
> Schema reference: [membership-schema.md](../../ats-home-craft-supabase/docs/membership-schema.md).

______________________________________________________________________

## Terminology

| Context                 | Use          | Maps to                                     |
| ----------------------- | ------------ | ------------------------------------------- |
| **Code, API, DB, docs** | **project**  | `projects`, `project_id`, `project_members` |
| Staff UI (optional)     | community    | Same as project — display label only        |
| Resident UI (optional)  | property     | Same as project — e.g. "My properties"      |
| SaaS tenant             | organization | `organizations`, `organization_id`          |

There is **no** `societies` table. Do not use `society_id` in code or schema.

______________________________________________________________________

## Table of contents

1. [Overview](#overview)
1. [Tenancy layers](#tenancy-layers)
1. [Staff membership](#staff-membership)
1. [Resident membership (contacts)](#resident-membership-contacts)
1. [Teams](#teams)
1. [Permissions](#permissions)
1. [Session and project context](#session-and-project-context)
1. [Operational flows](#operational-flows)
1. [API conventions](#api-conventions)
1. [Data scoping reference](#data-scoping-reference)
1. [Implementation checklist](#implementation-checklist)
1. [FAQ](#faq)

______________________________________________________________________

## Overview

ATS Home Craft is a **two-tier multi-tenant** system:

```
Platform
  └── organizations          ← SaaS tenant (property management company)
        └── projects         ← Gated community / development (one project each)
              └── units      ← Flats, plots, commercial spaces
```

**There is no third tenant layer.**

Two user personas share the same org + project hierarchy but use different membership tables:

| Persona      | Identity table         | Project assignment                |
| ------------ | ---------------------- | --------------------------------- |
| **Staff**    | `organization_members` | `project_members`                 |
| **Resident** | `contacts`             | `contact_units` + `contact_roles` |

Both can belong to **multiple projects within one organization**.

______________________________________________________________________

## Tenancy layers

### Layer 1 — Organization (SaaS tenant)

- Root entity: `organizations`
- Session-bound: `user_sessions.organization_id` set after login + org selection
- All API queries filter by session `organization_id` (never trust client-supplied org)
- Isolation enforced in Python backend (RLS enabled in Postgres but policies deferred)

### Layer 2 — Project

- Entity: `projects` (one gated community each)
- Denormalized `organization_id` + `project_id` on operational tables
- Project access validated per request via `ensure_project()` + membership checks
- Session does **not** currently store active project (client passes `project_id` or uses route param)

### What is NOT a tenant

| Concept        | Table                                | Notes                                                           |
| -------------- | ------------------------------------ | --------------------------------------------------------------- |
| Physical gate  | `tower_gates`                        | Configuration (location, hours), not a user tenancy boundary    |
| CRM team       | `teams`                              | Org-wide grouping; optional future `project_id` for project ops |
| Renter role    | `contact_roles.role_type = 'Tenant'` | Unit role, not SaaS tenant                                      |
| Tenant request | `tenant_requests`                    | Rental approval workflow                                        |

______________________________________________________________________

## Staff membership

### Principle: two layers, one identity

```
auth.users
  └── organization_members     ← "Works for ABC Property Management" (1 row per org)
        └── project_members    ← "Assigned to Green Valley" (N rows, 1 per project)
```

**Do not** make `organization_members` project-wise. It is the staff gateway for login and RBAC.

### `organization_members` (org gateway)

| Responsibility | Details                                        |
| -------------- | ---------------------------------------------- |
| Staff login    | `SelectOrganizationType.ORGANIZATION_MEMBER`   |
| RBAC           | `role_id` → `role_permissions` → `permissions` |
| Profile        | Name, email, phone, Isometrik chat id          |
| Lifecycle      | `active`, `invited`, `suspended`, `deleted`    |
| Uniqueness     | One row per `(user_id, organization_id)`       |

Used for: org settings, cross-project reporting, CRM when org-wide, invite/onboarding flow.

### `project_members` (project assignment)

| Responsibility  | Details                                                                        |
| --------------- | ------------------------------------------------------------------------------ |
| Project access  | Which projects the staff member can operate on                                 |
| Project role    | e.g. `community_admin`, `security`, `accountant` (extend from current default) |
| My projects     | Powers `GET /v1/projects/mine`                                                 |
| Community admin | Set via `projects.community_admin_user_id` on create/update                    |

| Column            | Purpose                                        |
| ----------------- | ---------------------------------------------- |
| `organization_id` | Denormalized org scope                         |
| `project_id`      | Project (gated community)                      |
| `user_id`         | `auth.users.id` (must be active org member)    |
| `role`            | Project-level role (text today; enum proposed) |
| `status`          | `active`, `invited`, `suspended`               |

**Uniqueness:** one row per `(project_id, user_id)`.

**Prerequisite:** assignee must exist as active `organization_members` in the same org (same rule as
`community_admin_user_id` validation in project setup).

### Staff types

| Type                 | `organization_members` | `project_members`                      | Typical org permissions    |
| -------------------- | ---------------------- | -------------------------------------- | -------------------------- |
| Org owner / HQ admin | Yes                    | Optional (all projects via permission) | Full org RBAC              |
| Project manager      | Yes                    | Yes — assigned projects                | Project ops + limited CRM  |
| Gate security        | Yes                    | Yes — one project                      | Visitor logs, passes       |
| CRM sales            | Yes                    | No                                     | Leads, contacts (org-wide) |
| Accountant           | Yes                    | Yes — assigned projects                | Fees, invoices per project |

### Access check (pseudocode)

```python
async def ensure_staff_project_access(
    user_context: UserContext,
    project_id: str,
    permission_codes: list[str],
) -> None:
    """Staff must have org permission AND project assignment (unless org-wide)."""
    await require_permission(user_context, permission_codes)

    if has_org_wide_project_access(user_context):
        return  # e.g. projects_management.view

    member = await project_members_repo.get_active(
        organization_id=user_context.organization_id,
        project_id=project_id,
        user_id=user_context.user_id,
    )
    if not member:
        raise ForbiddenException(...)
```

______________________________________________________________________

## Resident membership (contacts)

### Principle: one identity, many projects

```
auth.users
  └── contacts                   ← One row per org (identity + portal auth)
        ├── contact_units        ← Residency per unit (has project_id)
        └── contact_roles        ← Owner/Tenant/Family per unit (has project_id)
```

See [ADR 0010](./adr/0010-contact-roles.md) for role history and scope rules.

### Hard constraints

| Constraint                                   | Effect                                           |
| -------------------------------------------- | ------------------------------------------------ |
| `uq_contacts_user_org`                       | One contact per auth user per org                |
| Org-scoped email uniqueness                  | Same email cannot create two contacts in one org |
| `contact_units (unit_id, contact_id)` unique | One link per unit per contact                    |

### Multi-project resident example

Raj owns Unit 101 in **Green Valley** and rents Unit 205 in **Sunrise Towers** (same org):

```
contacts: { id: raj-contact, organization_id: abc, user_id: raj-auth }

contact_units:
  { contact_id: raj-contact, project_id: green-valley, unit_id: 101, status: active }
  { contact_id: raj-contact, project_id: sunrise,      unit_id: 205, status: active }

contact_roles:
  { contact_id: raj-contact, project_id: green-valley, unit_id: 101, role_type: Owner }
  { contact_id: raj-contact, project_id: sunrise,      unit_id: 205, role_type: Tenant }
```

Portal lists properties grouped by project via `ContactUnitsService.list_my_properties_grouped()`.

### What stays org-wide vs project-specific for contacts

| Org-wide (on `contacts` row) | Project-specific (junction tables)         |
| ---------------------------- | ------------------------------------------ |
| Name, phone, email, auth     | Unit assignment (`contact_units`)          |
| CRM tags, companies, leads   | Role label (`contact_roles`)               |
| Org-level custom fields      | Vehicles, documents, unit onboarding steps |
| Portal access flag           | Gate passes, fee invoices per unit         |

### Optional extension: project-specific profile

If the same person needs **different preferences per project** (emergency contact, comms prefs),
add `contact_project_profiles (organization_id, contact_id, project_id)` — do **not** duplicate
contact rows.

### Admin contact registry

- Default: list all contacts in org (`POST /v1/contacts/list`)
- Proposed: optional `project_id` filter joining `contact_units` / `contact_roles`
- Do **not** create separate contact records per project

______________________________________________________________________

## Teams

### Two team types (proposed)

| Type                 | Scope        | Example                  | `teams.project_id` |
| -------------------- | ------------ | ------------------------ | ------------------ |
| **Org CRM team**     | Organization | Sales team for all leads | `NULL`             |
| **Project ops team** | Project      | Green Valley gate crew   | Set                |

### Rules

1. Every `team_members.user_id` must be an active `organization_members` user in the org.
1. **Access control uses `project_members`, not teams.** Teams are for UI grouping, assignment
   labels, and notifications — not the authoritative gate for API access.
1. When inviting staff to a project team, also create/update `project_members`.

### Current state

- `teams` / `team_members` exist without `project_id` (org-wide CRM only)
- Invite flow can attach `team_id` in invitation metadata
- Project ops teams require migration adding nullable `teams.project_id`

______________________________________________________________________

## Permissions

### Org-scoped RBAC (today)

Permissions are resolved via:

```
organization_members → role_permissions → permissions
```

Function: `check_user_access_async()` in `libs/shared_middleware/jwt_auth.py`.

All permission codes are org-scoped. There is no project-scoped roles table.

### Proposed permission split

| Code                                | Scope                  | Who gets it             |
| ----------------------------------- | ---------------------- | ----------------------- |
| `organization.settings.manage`      | Org                    | Org admins              |
| `projects_management.view`          | All projects in org    | HQ / org admins         |
| `projects_management.view_assigned` | Assigned projects only | Project managers        |
| `projects_management.edit`          | All projects           | Org admins              |
| `visitor_logs.view`                 | Per project            | Requires project access |
| `contacts_management.view`          | Org CRM                | CRM team                |
| `resident_management.view`          | Per project            | Project staff           |

### Combining org permission + project assignment

| User            | Org permission             | `project_members` | Can access Green Valley passes? |
| --------------- | -------------------------- | ----------------- | ------------------------------- |
| HQ admin        | `projects_management.view` | None              | Yes                             |
| Project manager | `visitor_logs.view`        | Green Valley      | Yes                             |
| Project manager | `visitor_logs.view`        | Sunrise only      | No                              |
| CRM sales       | `contacts_management.view` | None              | No (wrong module)               |

______________________________________________________________________

## Session and project context

### Current behavior

| Context           | Source                                                                     |
| ----------------- | -------------------------------------------------------------------------- |
| Organization      | `user_sessions.organization_id` (set on select/switch org)                 |
| Staff vs resident | Auth flow user type + membership table                                     |
| Active project    | **Client-side** (route param, header, or app state) — not in session today |

### Recommended client flow (staff app)

1. Login → select organization
1. `GET /v1/projects/mine` → project switcher
1. Store `activeProjectId` in app state
1. Pass `project_id` on project-scoped API calls (path or query)

### Recommended client flow (resident app)

1. Login → select organization
1. `list_my_properties_grouped(contact_id)` → pick project + unit
1. Scope passes, visitors, fees to selected `project_id` + `unit_id`

### Optional server-side enhancement

Add `user_sessions.active_project_id` (nullable) for default project after switch. Not required for
Phase 1.

______________________________________________________________________

## Operational flows

### Flow 1 — Invite project manager

```
1. Org admin creates invitation
      → organization_members (on accept) with role "Community Manager"
      → optional: team_members if org CRM team

2. Org admin assigns to project
      → INSERT project_members (project_id, user_id, role='community_admin')

3. Manager logs in
      → session.organization_id = org
      → GET /projects/mine → sees assigned projects
```

### Flow 2 — Resident in two projects

```
1. Admin allots unit in Green Valley
      → contact_units + contact_roles (Owner)

2. Admin approves tenant request for Sunrise unit
      → contact_units + contact_roles (Tenant) on same contact row

3. Resident logs in (portal)
      → extract_onboarding_contact_context → one contact
      → list_my_properties_grouped → two project groups
```

### Flow 3 — Org admin vs project manager

| Step               | Org admin       | Project manager        |
| ------------------ | --------------- | ---------------------- |
| List all projects  | `GET /projects` | `GET /projects/mine`   |
| Create project     | Yes             | No (unless granted)    |
| View visitor logs  | Any project     | Assigned projects only |
| Manage org members | Yes             | No                     |

### Flow 4 — Project create (existing)

On `POST /projects` (see `projects_service.py`):

1. Validate `community_admin_user_id` is active org member
1. Insert project
1. Upsert creator into `project_members`
1. Upsert community admin into `project_members` with role `community_admin`

______________________________________________________________________

## API conventions

### Every project-scoped endpoint should

1. Resolve `organization_id` from session
1. Accept `project_id` (path param preferred: `/v1/projects/{project_id}/…`)
1. Call `ensure_project(project_id)` — project exists in org
1. Call `ensure_staff_project_access()` or equivalent for staff routes
1. Filter all queries with `organization_id` AND `project_id`

### Resident routes should

1. Resolve contact from `extract_onboarding_contact_context()`
1. Validate `contact_units` exists for requested `project_id` / `unit_id`
1. Never accept `contact_id` from client on portal routes (use session contact)

### Proposed new endpoints (Phase 2)

| Method | Path                                          | Purpose                        |
| ------ | --------------------------------------------- | ------------------------------ |
| GET    | `/v1/projects/{project_id}/members`           | List staff assigned to project |
| POST   | `/v1/projects/{project_id}/members`           | Assign org member to project   |
| PATCH  | `/v1/projects/{project_id}/members/{user_id}` | Update project role / status   |
| DELETE | `/v1/projects/{project_id}/members/{user_id}` | Remove project assignment      |

______________________________________________________________________

## Data scoping reference

| Entity / feature        | `organization_id` | `project_id`    | Membership check           |
| ----------------------- | ----------------- | --------------- | -------------------------- |
| Org settings            | Required          | —               | Org admin permission       |
| Roles / permissions     | Required          | —               | Org member                 |
| CRM contacts (registry) | Required          | Filter optional | Org permission             |
| CRM companies, leads    | Required          | —               | Org permission             |
| Projects list (all)     | Required          | —               | `projects_management.view` |
| Projects list (mine)    | Required          | —               | `project_members`          |
| Towers, units, gates    | Required          | Required        | Project access             |
| Visitor passes          | Required          | Required        | Project access             |
| Maintenance fees        | Required          | Required        | Project access             |
| Resident portal         | Required          | Per unit        | `contact_units`            |
| Push notifications      | Required          | Optional target | Persona-specific           |

______________________________________________________________________

## Implementation checklist

### Phase 1 — Document & enforce (no schema change)

- [x] ADR 0011 + this architecture guide + schema doc
- [x] `ensure_staff_project_access()` helper
- [x] Audit project APIs for project access enforcement
- [x] Split `projects_management.view` vs `view_assigned` permission codes

### Phase 2 — Project staff management

- [x] `project_member_role` Postgres enum + migration
- [x] CRUD API for `/projects/{project_id}/members`
- [x] Validate assignee is active org member
- [ ] Admin UI: assign staff to project (see [frontend-membership-flow.md](./frontend-membership-flow.md))

### Phase 3 — Teams (optional)

- [x] `teams.project_id` nullable FK migration
- [x] Filter teams by org vs project context
- [x] Invite flow: optional project assignment alongside team

### Phase 4 — Contact registry

- [x] `project_id` filter on `POST /contacts/list`
- [x] Typesense `project_ids[]` facet from active `contact_units`

#### Typesense reindex (ops)

After deploying the `project_ids` schema field, recreate or update the contacts collection and
reindex existing documents:

1. Apply the schema change (new collection or alias swap with `project_ids` facet).
1. Trigger a full org reindex via the existing background path, e.g. enqueue
   `index_contacts_background` for all `(contact_id, organization_id)` pairs, or run the
   contacts bulk reindex admin task if available in your environment.
1. Verify search/list with `project_id` returns the same contacts as the DB `contact_units` filter.

______________________________________________________________________

## FAQ

### Should we add a `societies` table?

**No.** Use `projects`. Only add a layer above projects if you need federation (one legal entity
spanning multiple developments) — uncommon.

### Can the same auth user be staff and resident?

**Yes**, but via separate tables: `organization_members` and `contacts`. UI should offer role
switching (Admin mode vs Resident mode).

### Should Vendor/Staff contacts be project-scoped?

Today `Vendor` / `Staff` roles are org-scoped (`project_id IS NULL`). For project-specific guards,
either extend `contact_roles` to allow `Staff` with `project_id`, or use `project_members` for
employed staff and `contacts.Staff` for external vendors.

### Why not duplicate contacts per project?

Blocked by `uq_contacts_user_org`, breaks single portal login, splits CRM identity incorrectly.
Use junction tables instead.

### Is session project-scoped?

**No** — session is org-scoped only. Project is request context. Optional future:
`user_sessions.active_project_id`.

### What UI label should we show users?

| App             | Suggested label     | Backend term |
| --------------- | ------------------- | ------------ |
| Staff dashboard | Community / Project | `project_id` |
| Resident portal | My properties       | `project_id` |
| Code & API      | project             | `projects`   |

______________________________________________________________________

## Related documentation

| Doc                               | Location                                                                                          |
| --------------------------------- | ------------------------------------------------------------------------------------------------- |
| Frontend flow (org/project/staff) | [frontend-membership-flow.md](./frontend-membership-flow.md)                                      |
| ADR 0011 (decision)               | [adr/0011-project-membership.md](./adr/0011-project-membership.md)                                |
| Schema reference                  | [membership-schema.md](../../ats-home-craft-supabase/docs/membership-schema.md)                   |
| Contact roles ADR                 | [adr/0010-contact-roles.md](./adr/0010-contact-roles.md)                                          |
| Resident onboarding               | [adr/0001-resident-onboarding.md](./adr/0001-resident-onboarding.md)                              |
| Project setup flow                | [project-setup-flow.md](./project-setup-flow.md)                                                  |
| Contact roles schema              | [contact-roles-schema.md](../../ats-home-craft-supabase/docs/contact-roles-schema.md)             |
| Resident onboarding schema        | [resident-onboarding-schema.md](../../ats-home-craft-supabase/docs/resident-onboarding-schema.md) |
