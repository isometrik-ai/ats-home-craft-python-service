# ADR 0015: Project-level RBAC — per-project roles and permissions

|                  |                                                                                                                                                                                                                                                    |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Accepted                                                                                                                                                                                                                                           |
| **Date**         | 2026-09-07                                                                                                                                                                                                                                         |
| **Authors**      | Home Craft platform team                                                                                                                                                                                                                           |
| **Related docs** | [ADR 0011](./0011-project-membership.md), [membership-architecture.md](../membership-architecture.md), [project-setup-flow.md](../project-setup-flow.md), [membership-schema.md](../../../../../ats-home-craft-supabase/docs/membership-schema.md) |
| **Migrations**   | `20260907120000_project_scoped_permissions.sql`, `20260907121000_project_roles_schema.sql`, `20260907122000_prevent_system_project_role_delete.sql`, `20260908123000_allow_custom_project_role_slugs.sql`                                          |

______________________________________________________________________

## Context

Staff access uses a two-layer model (ADR 0011):

1. **Org layer** — `organization_members` + org `roles` / `permissions` (ceiling).
1. **Project layer** — `project_members` gates which projects a user can access.

Today `project_members.role` is a Postgres enum (`community_admin`, `security`, …) used as a
label. It does **not** define a customizable permission set per project, and project-scoped APIs
only combine org permissions with assignment checks — not role-specific capability within a
project.

Requirements:

- Each **project** owns its own role templates (not org-global).
- Default roles are **seeded when a project is created** (same five system slugs).
- Org admins can **customize permissions per role per project** (mirrors org role management).
- **`project_members.role` is removed**; assignments use `project_role_id` FK.
- **Project isolation is mandatory** — project-a members cannot access project-b without a separate
  `project_members` row on project-b.
- **HQ bypass** — users with org `projects_management.view` skip project assignment and project-role
  checks.
- **Member management** — both org `project_members.manage` and project `community_admin` (with
  `project_members.manage_assigned`) may assign staff.

______________________________________________________________________

## Decision

### 1. Schema

```
projects
  └── project_roles (per project; slug + name + is_system)
        └── project_role_permissions → permissions (org catalog)
  └── project_members
        └── project_role_id (required; replaces role enum column)
```

| Table                      | Scope              | Purpose                                                       |
| -------------------------- | ------------------ | ------------------------------------------------------------- |
| `permissions`              | Org                | Shared permission catalog (unchanged)                         |
| `project_roles`            | **Per project**    | Role templates for that project only                          |
| `project_role_permissions` | Per project role   | Which permission codes the role grants **within the project** |
| `project_members`          | Per project + user | Assignment; `project_role_id` only (no `role` column)         |

**Unique constraints:**

- `project_roles (project_id, slug)`
- `project_role_permissions (project_role_id, permission_id)`
- `project_members (project_id, user_id)`

### 2. Default roles seeded on project create

When `POST /v1/projects` succeeds, seed five system roles for that `project_id`:

| slug               | name             |
| ------------------ | ---------------- |
| `community_admin`  | Community Admin  |
| `security`         | Security         |
| `accountant`       | Accountant       |
| `facility_manager` | Facility Manager |
| `viewer`           | Viewer           |

Each role receives default `project_role_permissions` from
`libs/shared_utils/project_role_defaults.py`. Orgs may customize later via project role APIs
(follow-up phase). Seeded roles are stored with `is_system = true` and **cannot be deleted**
(application guard + database trigger); custom roles (`is_system = false`) may be created via
`POST /v1/projects/{project_id}/roles` with a unique non-reserved slug and removed when unused.

### 3. Access formula

```
Can staff U perform action A on project P?

1. U is active organization_member in session org
2. U's org role includes permission code A                    ← ceiling
3. EITHER U has projects_management.view                      ← HQ bypass
   OR (
     U has active project_members row on P
     AND A is granted by U's project_role on P via project_role_permissions
   )
```

Effective permission for assigned staff:

```
effective(A) = org_has(A) AND project_role_has(A)
```

HQ users: `org_has(A)` only (step 3 bypass).

### 4. Project isolation

Assignment check always filters by the **request's `project_id`**. Role rows belong to exactly one
project (`project_roles.project_id`). Joins must enforce `pr.project_id = pm.project_id`.

Example: member-1 on project-a cannot call project-b APIs — no `project_members` row for project-b
→ 403 before role permissions are evaluated.

### 5. Member assignment authorization

| Caller                       | Requirement                                                                                                                                      |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| HQ / org admin               | Org `project_members.manage` + project access                                                                                                    |
| Community admin on project P | Active member on P with slug `community_admin` + org `project_members.manage_assigned` + project role includes `project_members.manage_assigned` |

Assignee must be an active `organization_members` row (unchanged).

### 6. Remove `project_members.role`

- Drop column `role` and stop using Postgres `project_member_role` on `project_members`.
- Keep slug constants in Python (`ProjectMemberRole` enum) for default seed slugs and filters only.
- API requests use `project_role_id`; responses expose `project_role_id`, `role_slug`, `role_name`.

### 7. Migration for existing data

For every existing project:

1. Insert five `project_roles` rows.
1. Insert default `project_role_permissions`.
1. Set `project_members.project_role_id` from former `role` enum via slug match.
1. Drop `project_members.role`.

______________________________________________________________________

## Default permission matrix (seed)

See `libs/shared_utils/project_role_defaults.py` for the authoritative list. Summary:

| Module                           | community_admin | security | accountant | facility_manager | viewer |
| -------------------------------- | :-------------: | :------: | :--------: | :--------------: | :----: |
| Project access (`view_assigned`) |        ✓        |    ✓     |     ✓      |        ✓         |   ✓    |
| Project setup edit/delete        |        ✓        |    —     |     —      |        ✓         |   —    |
| Project members (assigned)       |        ✓        |    —     |     —      |        —         |   —    |
| Visitor logs / verify            |        ✓        |    ✓     |     —      |        —         |  view  |
| Notices                          |        ✓        |   view   |    view    |       view       |  view  |
| Community events                 |        ✓        |   view   |    view    |       edit       |  view  |
| Daily help                       |        ✓        |   view   |     —      |        ✓         |  view  |
| Tenant requests                  |        ✓        |    —     |    view    |        —         |  view  |
| Move events                      |        ✓        |    —     |    view    |        —         |  view  |
| Parking                          |        ✓        |   view   |     —      |        ✓         |  view  |
| Residents (project)              |        ✓        |   view   |     ✓      |       view       |  view  |
| Finance                          |        ✓        |    —     |     ✓      |        —         |  view  |
| Work orders                      |        ✓        |    —     | view/edit  |        ✓         |  view  |

Org-only permissions (users, org roles, CRM leads, `projects_management.view` bypass, etc.) are
**never** stored on project roles.

______________________________________________________________________

## Implementation phases

| Phase | Scope                                                                   | Status |
| ----- | ----------------------------------------------------------------------- | ------ |
| **1** | ADR, migration, seed on create, `project_members` → `project_role_id`   | Done   |
| **2** | Enforce project-role permissions in `ensure_staff_project_access`       | Done   |
| **3** | CRUD API `/v1/projects/{id}/roles` + `my-permissions`                   | Done   |
| **4** | Split endpoint permission codes; remove `projects_management.*` aliases | Done   |

______________________________________________________________________

## Consequences

### Positive

- Each project can customize role permissions independently.
- Clear separation: org ceiling + per-project role floor + assignment gate.
- Removes redundant enum column; single FK for role assignment.
- Aligns with product example: project-a staff cannot access project-b.

### Negative / trade-offs

- More rows per project (5 roles + permissions each).
- Role customization UI is per project (more admin surface).
- Existing code referencing `project_members.role` must migrate to slug join.

### Follow-ups

1. Wire project-role permission check into `ensure_staff_project_access_for_context`.
1. Expose project role management APIs and admin UI.
1. Extend `GET /users/profile?project_id=` with effective project permissions.
1. Update notice recipient resolution and security helpers to use `project_roles.slug` (done in phase 1 queries where required).

______________________________________________________________________

## Alternatives considered

| Alternative                                          | Why rejected                                           |
| ---------------------------------------------------- | ------------------------------------------------------ |
| Org-global `project_roles`                           | User requirement: per-project templates                |
| Keep `role` enum on `project_members`                | Duplicates `project_role_id`; user requested removal   |
| Full `project_roles` table per member (no templates) | No reusable customizable templates per project         |
| Postgres RLS for project RBAC                        | Application-layer enforcement matches org RBAC pattern |
