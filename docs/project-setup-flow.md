# Project Setup Flow — Context & Change Guide

This document explains the **Project Setup wizard** implemented in `user_service`. It is
written so anyone (developer, reviewer, or product owner) can understand the flow end‑to‑end
and know exactly where to change things.

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **API prefix:** `/v1/projects`
- **DB schema:** `ats-home-craft-supabase` (migrations `20260629100000_*` enums, `20260629101000_*` tables)
- **Full column reference:** `ats-home-craft-supabase/docs/project-setup-schema.md`

______________________________________________________________________

## 1. What this flow does

A "project" is a real‑estate community/development. Creating a fully usable project is a
multi‑step **wizard**. Each project has:

- a `status` (`onboarding` → `active` / `suspended`), and
- a `setup_current_step` pointer plus a per‑step status table (`project_setup_steps`).

The wizard is **dynamic**: which steps apply depends on the project's `property_types`
(`residential`, `commercial`, `plots`). Steps that don't apply are auto‑marked `skipped`.
When every applicable step is `completed` or `skipped`, the project can be finalized to `active`.

### Wizard steps (order matters)

Enum: `ProjectSetupStep` in `apps/user_service/app/schemas/enums.py`.

| #   | Step key            | Applies when              | Purpose                                        |
| --- | ------------------- | ------------------------- | ---------------------------------------------- |
| 1   | `project_basics`    | always                    | Core project fields (created with the project) |
| 2   | `tower_builder`     | residential OR commercial | Towers, wings, gates, lifts, floors            |
| 3   | `apartment_config`  | residential               | Apartment unit configs                         |
| 4   | `commercial_config` | commercial                | Commercial unit configs                        |
| 5   | `plot_config`       | plots                     | Plot configs + plot items                      |
| 6   | `inventories`       | residential OR commercial | Floor × config quantity matrix                 |
| 7   | `facilities`        | residential OR commercial | Amenities/facilities                           |
| 8   | `floor_plans`       | residential OR commercial | Units + parking zones                          |
| 9   | `site_map`          | always                    | Lat/lng + site map overlays                    |

Step‑visibility logic lives in **one place**: `compute_visible_steps()` in
`apps/user_service/app/services/project_setup_service.py`. The "structure" steps
(`tower_builder`, `inventories`, `facilities`, `floor_plans`) are grouped in `_STRUCTURE_STEPS`.

> To change which steps apply to which property type, edit `compute_visible_steps()` only.

______________________________________________________________________

## 2. Architecture (layers)

Standard 3‑layer FastAPI pattern used across the service:

```
HTTP → API router → Service (business rules) → Repository (SQL) → Postgres
```

| Layer                                    | Responsibility                                                   |
| ---------------------------------------- | ---------------------------------------------------------------- |
| **API** (`app/api/projects.py`)          | Routing, auth, RBAC, rate limit, audit, request/response schemas |
| **Service** (`app/services/*`)           | Business rules, validation, step‑gating, orchestration           |
| **Repository** (`app/db/repositories/*`) | Raw SQL, enum/array casts, org scoping                           |
| **Schemas** (`app/schemas/*`)            | Pydantic request/response models + enums                         |

### File map

| Concern                                   | File                                                                                                        |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| API endpoints (all 60+)                   | `app/api/projects.py`                                                                                       |
| Route registration                        | `app/api/routes.py`                                                                                         |
| Wizard orchestration / step‑gating        | `app/services/project_setup_service.py`                                                                     |
| Project CRUD + media + members            | `app/services/projects_service.py`                                                                          |
| Towers/wings/gates/lifts/floors           | `app/services/towers_service.py`                                                                            |
| Unit configs + plot items + config media  | `app/services/unit_configs_service.py`                                                                      |
| Inventory / Facilities / Units / Site map | `app/services/inventory_service.py`                                                                         |
| Facilities + parking slot provisioning    | `app/services/facilities_service.py`                                                                        |
| Conditional field validation              | `app/services/project_setup_validation.py`                                                                  |
| Units + parking zones                     | `app/services/units_service.py`                                                                             |
| Vehicle admin review (parking assignment) | `app/services/vehicles_service.py`                                                                          |
| Site map location + overlays              | `app/services/site_map_service.py`                                                                          |
| Step persistence                          | `app/db/repositories/project_setup_repository.py`                                                           |
| Project persistence                       | `app/db/repositories/projects_repository.py`                                                                |
| Other repositories                        | `app/db/repositories/{towers,unit_configs,inventory,facilities,parking_slots,units,site_map}_repository.py` |
| Request/response models                   | `app/schemas/project_setup.py`, `app/schemas/project_inventory.py`                                          |
| Enums (mirror Postgres)                   | `app/schemas/enums.py`                                                                                      |
| Row → JSON serialization                  | `app/utils/project_serialization.py`                                                                        |
| i18n messages                             | `app/locales/en.json` (`project_setup.*`)                                                                   |

______________________________________________________________________

## 3. Data model (18 tables)

Defined in `20260629101000_property_setup_tables.sql` (+ `20260716120000_project_setup_field_extensions.sql` for existing DBs). Every table carries
`organization_id` for tenant scoping.

| Group         | Tables                                                                |
| ------------- | --------------------------------------------------------------------- |
| Project       | `projects`, `project_media`, `project_setup_steps`, `project_members` |
| Tower builder | `towers`, `tower_wings`, `tower_gates`, `tower_lifts`, `floors`       |
| Configs       | `unit_configs`, `plot_config_items`, `config_media`                   |
| Inventory     | `floor_inventory`                                                     |
| Facilities    | `facilities`, `facility_parking_slots`                                |
| Floor plans   | `units`, `parking_zones`                                              |
| Site map      | `site_map_overlays`                                                   |

See `ats-home-craft-supabase/docs/project-setup-schema.md` for every column.

### Media handling (important)

Media rows (`project_media`, `config_media`) store **metadata only** — `path`, `mime`,
`size_bytes`, `original_name`, `sort_order`, `kind`. The actual file upload is handled
elsewhere; these endpoints just record what the client sends. (There is intentionally **no
`bucket` field** — it was removed.)

______________________________________________________________________

## 4. API catalog

All routes are under `/v1/projects` and require authentication + an org context. RBAC codes:
`PROJECTS_MANAGEMENT_VIEW` (reads), `PROJECTS_MANAGEMENT_CREATE`, `PROJECTS_MANAGEMENT_EDIT`,
`PROJECTS_MANAGEMENT_DELETE` (writes).

### Project + wizard

| Method | Path                                                  | Purpose                                                                                |
| ------ | ----------------------------------------------------- | -------------------------------------------------------------------------------------- |
| POST   | `/v1/projects`                                        | Create project (seeds applicable steps)                                                |
| GET    | `/v1/projects`                                        | List projects — query params: `search`, `status`, `property_type`, `page`, `page_size` |
| GET    | `/v1/projects/mine`                                   | List projects assigned to the current user (`project_members`)                         |
| GET    | `/v1/projects/{project_id}`                           | Project details                                                                        |
| PATCH  | `/v1/projects/{project_id}`                           | Update project (re‑syncs steps if `property_types` change)                             |
| DELETE | `/v1/projects/{project_id}`                           | Delete project                                                                         |
| GET    | `/v1/projects/{project_id}/status`                    | Wizard status snapshot (steps + current step + is_completed)                           |
| POST   | `/v1/projects/{project_id}/steps/{step_key}/complete` | Mark a step complete                                                                   |
| POST   | `/v1/projects/{project_id}/complete`                  | Finalize wizard → project `active`                                                     |

### Project media

| Method     | Path                                         |
| ---------- | -------------------------------------------- |
| POST / GET | `/v1/projects/{project_id}/media`            |
| DELETE     | `/v1/projects/{project_id}/media/{media_id}` |

### Tower builder

| Method         | Path                                                            |
| -------------- | --------------------------------------------------------------- |
| POST / GET     | `/v1/projects/{project_id}/towers`                              |
| PATCH / DELETE | `/v1/projects/{project_id}/towers/{tower_id}`                   |
| POST / GET     | `.../towers/{tower_id}/wings` · DELETE `.../wings/{wing_id}`    |
| POST / GET     | `.../towers/{tower_id}/gates` · DELETE `.../gates/{gate_id}`    |
| POST / GET     | `.../towers/{tower_id}/lifts` · DELETE `.../lifts/{lift_id}`    |
| POST / GET     | `.../towers/{tower_id}/floors` · DELETE `.../floors/{floor_id}` |

### Unit configs

| Method         | Path                                                                     |
| -------------- | ------------------------------------------------------------------------ |
| POST / GET     | `/v1/projects/{project_id}/configs`                                      |
| PATCH / DELETE | `.../configs/{config_id}`                                                |
| POST / GET     | `.../configs/{config_id}/plot-items` · DELETE `.../plot-items/{item_id}` |
| POST / GET     | `.../configs/{config_id}/media` · DELETE `.../media/{media_id}`          |

> Config `kind` maps to a wizard step via `_KIND_TO_STEP` in `unit_configs_service.py`:
> `apartment → apartment_config`, `commercial → commercial_config`, `plot → plot_config`.

### Inventory / Facilities / Units / Site map

| Method     | Path                                                                      | Notes                                 |
| ---------- | ------------------------------------------------------------------------- | ------------------------------------- |
| PUT / GET  | `/v1/projects/{project_id}/inventory`                                     | Upsert / read floor×config matrix     |
| GET        | `/v1/projects/{project_id}/inventory/summary`                             | Post-setup inventory menu payload     |
| POST / GET | `/v1/projects/{project_id}/facilities` · PATCH/DELETE `.../{facility_id}` |                                       |
| GET        | `/v1/projects/{project_id}/facilities/{facility_id}/parking-slots`        | Slots for parking facilities          |
| POST / GET | `/v1/projects/{project_id}/units` · PATCH/DELETE `.../{unit_id}`          | Recomputes `projects.units_count`     |
| POST / GET | `/v1/projects/{project_id}/parking-zones` · DELETE `.../{zone_id}`        | Tower basement zone ranges            |
| GET        | `/v1/projects/{project_id}/vehicle-requests`                              | Admin: list resident vehicle requests |
| PATCH      | `/v1/projects/{project_id}/vehicle-requests/{vehicle_id}`                 | Admin: approve/reject + assign slot   |
| PATCH      | `/v1/projects/{project_id}/site-map/location`                             | Set project lat/lng                   |
| POST / GET | `/v1/projects/{project_id}/site-map/overlays` · DELETE `.../{overlay_id}` |                                       |

### Conditional fields (UI-driven validation)

Validated in `app/services/project_setup_validation.py` (towers + facilities).

| Step / entity | API field           | Required when                                         |
| ------------- | ------------------- | ----------------------------------------------------- |
| Tower         | `custom_prefix`     | `numbering_pattern = "custom"`                        |
| Plot item     | `description`       | Optional (e.g. "Near park, road-facing")              |
| Facility      | `wing`              | `location_type = "in_tower"`                          |
| Facility      | `capacity_persons`  | `facility_type = "events"` (integer > 0)              |
| Facility      | `parking_slots`     | `facility_type = "parking"` (integer > 0)             |
| Facility      | `parking_user_type` | `facility_type = "parking"` (`resident` / `visitors`) |
| Facility      | `extra_attributes`  | Optional JSON object; defaults to `{}` on create      |

When a **parking** facility is created, the API auto-provisions `facility_parking_slots` rows
(`slot_number` 1…N). These are separate from Step 8 `parking_zones` (tower basement ranges).

### Vehicle registration review (admin)

Residents submit vehicles during contact onboarding (`status = pending`). Community admins
review via project APIs:

1. `GET /vehicle-requests?status=pending` — queue
1. `GET /facilities/{facility_id}/parking-slots?status=available` — pick a slot
1. `PATCH /vehicle-requests/{vehicle_id}` — approve with `parking_slot_id`, or reject with `rejection_reason`

On approval the slot becomes `assigned` and `vehicles.parking_slot_id` is set. Deleting a
vehicle releases the slot back to `available`.

______________________________________________________________________

## 5. Cross‑cutting conventions

- **Auth & org scope:** every request resolves a user + `organization_id`; every query is
  filtered by `organization_id`. A missing/mismatched project raises 404
  (`project_setup.errors.project_not_found`).
- **RBAC:** enforced per endpoint via `check_permissions` with `PROJECTS_MANAGEMENT_*` codes.
- **Responses:** `success_response` for single objects, `list_response` for collections;
  all user‑facing text uses i18n `message_key`s from `app/locales/en.json` (`project_setup.*`).
- **Writes vs reads:** writes use `db_uow` (transaction), reads use `db_conn`.
- **Auditing:** mutations are wrapped with `@audit_api_call` (see `_set_audit` helper in `projects.py`).
- **Rate limiting:** endpoints use `@limiter.limit`.
- **Serialization:** DB rows are converted with `app/utils/project_serialization.py`
  (handles UUID, Decimal, date/datetime, lists, dicts).
- **Community admin on create/update:** `community_admin_user_id` is the Supabase auth user id
  of an **active** `organization_members` row in the same org. The UI should let project
  creators pick from org members; the API rejects non-members
  (`project_setup.errors.community_admin_not_org_member`). On create/update the selected user
  is also upserted into `project_members` with role `community_admin`.
- **Assigned vs org-wide project lists:** `GET /v1/projects` returns all projects in the org
  (requires `PROJECTS_MANAGEMENT_VIEW`). `GET /v1/projects/mine` returns only projects where
  the current user has an **active** `project_members` row (no special RBAC — org session only).
- **Enums:** Python enums in `schemas/enums.py` mirror Postgres enums; repositories cast
  explicitly (e.g. `$3::project_media_kind`, `::property_type[]`).

______________________________________________________________________

## 6. How to make common changes

| I want to…                                                     | Change here                                                                              |
| -------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Add/remove a wizard step                                       | `ProjectSetupStep` enum + Postgres `project_setup_step` enum + `compute_visible_steps()` |
| Change which property type shows which step                    | `compute_visible_steps()` in `project_setup_service.py`                                  |
| Add a field to a request/response                              | matching model in `schemas/project_setup.py` or `schemas/project_inventory.py`           |
| Add/rename a DB column                                         | new migration in `ats-home-craft-supabase` + repository SQL + schema model               |
| Change validation rules (e.g. required fields per config kind) | `project_setup_validation.py` or relevant `*_service.py`                                 |
| Add an endpoint                                                | add route in `api/projects.py` → service method → repository method                      |
| Change a user‑facing message                                   | `app/locales/en.json` under `project_setup.*`                                            |
| Change RBAC required for an action                             | the `check_permissions(...)` call on that endpoint                                       |

### Step‑gating rules to keep in mind

- `complete_step` rejects unknown step keys (`project_setup.errors.invalid_step`) and steps
  that don't apply to the project's `property_types` (`project_setup.errors.step_not_applicable`).
- `complete_wizard` requires **all** applicable steps to be `completed`/`skipped`
  (`project_setup.errors.steps_incomplete`) before setting the project `active`.
- After any step change, `_recompute_current_step` moves `setup_current_step` to the first
  unfinished step.

______________________________________________________________________

## 7. Tests

Unit tests (fake repos, no DB):

- `tests/unit/test_project_setup_service.py` — step visibility, seeding/skipping, gating, finalize.
- `tests/unit/test_projects_repository.py` — SQL generation (insert/list/recompute).
- `tests/unit/test_unit_configs_service.py` — kind‑specific validation + config→step mapping.
- `tests/unit/test_project_setup_validation.py` — tower custom prefix + facility conditional fields.
- `tests/unit/test_inventory_service.py` — inventory summary aggregation.

Run: `.venv/bin/python -m pytest apps/user_service/tests/unit`
