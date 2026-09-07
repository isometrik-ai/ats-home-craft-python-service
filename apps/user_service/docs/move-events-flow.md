# Move Events Flow — Context & Change Guide

> **Status: Implemented (Phase 1 + 2 + occupancy turnover).** This document describes the **Move Events** feature in
> `user_service`, written in the same style as
> [`passes-flow.md`](./passes-flow.md), [`contact-onboarding-flow.md`](./contact-onboarding-flow.md),
> and [`project-setup-flow.md`](./project-setup-flow.md) so it drops straight into the codebase.
> The schema/architecture rationale lives in [ADR 0005](./adr/0005-move-events.md).

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **API prefix:** `/v1/move-events`
- **DB schema:** `ats-home-craft-supabase` (migrations `20260720150000_*`, `20260720151000_*`)
- **Design decision:** [`docs/adr/0005-move-events.md`](./adr/0005-move-events.md)

______________________________________________________________________

## 1. What this flow does

A **community admin** records **move-in** and **move-out** events for units across apartments,
commercial units, and plots. Each event:

1. names a **unit** (`units`) and the **contact** moving (`contacts`, an Owner/Tenant),
1. has a **type** (`move_in` / `move_out`) — this is also the "Status" badge in the table,
1. has an **event date** and an optional **fee** (amount + currency, default INR),
1. may carry **inspection documents/photos** (paths only), and
1. **syncs occupancy** on `contact_units` (activate on move-in, mark `moved_out` on move-out), and
1. **clears household artifacts** on turnover via `UnitOccupancyTurnoverService` (family, vehicles,
   passes, daily help — owner preserved).

The acting admin is an `organization_member` resolved via `check_permissions()` — this is a
**staff/admin** feature (like `contacts`), so it **uses the same RBAC codes as Contacts**
(`contacts_management.*`), unlike the resident-facing passes flow.

### Screen → capability map (from the "Move Events" board)

| Screen element                          | Capability                                                    |
| --------------------------------------- | ------------------------------------------------------------- |
| Move Events table (Unit/Type/Contact/…) | `GET /move-events` — joins `move_events` ⋈ `units`/`contacts` |
| All / Move-In / Move-Out filter         | `GET /move-events?bucket=move_in\|move_out` (omit = All)      |
| Search unit or contact                  | `GET /move-events?search=` (unit code / contact name)         |
| Status badge (Move-In / Move-Out)       | `move_events.move_type`                                       |
| Fee column (₹5,000)                     | `move_events.fee_amount` + `fee_currency`                     |
| + Move In/Out (create form)             | `POST /move-events`                                           |
| Actions → view (eye)                    | `GET /move-events/{move_event_id}`                            |

______________________________________________________________________

## 2. Architecture (layers)

Same 3-layer FastAPI pattern as the rest of the service:

```
HTTP → API router → Service (business rules) → Repository (SQL) → Postgres
```

### File map (to create)

| Concern                             | File                                                                   |
| ----------------------------------- | ---------------------------------------------------------------------- |
| API endpoints                       | `app/api/move_events.py`                                               |
| Route registration                  | `app/api/routes.py` (`move_events_router`)                             |
| Move orchestration + occupancy sync | `app/services/move_events_service.py`                                  |
| Owner-change tenant move-out hook   | `MoveEventsService.record_tenant_move_out_for_owner_change`            |
| Household turnover cleanup          | `app/services/unit_occupancy_turnover_service.py`                      |
| Tenant-request mirror on move-in    | `app/services/tenant_requests_service.py` (`sync_after_admin_move_in`) |
| Occupancy link updates (reused)     | `app/db/repositories/contact_units_repository.py` (existing)           |
| Move persistence                    | `app/db/repositories/move_events_repository.py`                        |
| Request/response models             | `app/schemas/move_events.py`                                           |
| Enums (mirror Postgres)             | `app/schemas/enums.py`                                                 |
| RBAC codes                          | Reuses `contacts_management.*` (same as Contacts admin routes)         |
| i18n messages                       | `app/locales/en.json` (`move_events.*`)                                |

`MoveEventsService` **composes** the existing `ContactUnitsRepository` to keep occupancy in sync
and **`UnitOccupancyTurnoverService`** to release outgoing household data on turnover (same rules
as tenant-request approval).

______________________________________________________________________

## 2.1 Unit occupancy turnover (admin move-in / move-out)

When a **new tenant** is recorded or the **active tenant** moves out, the service clears outgoing
household data on the unit while **preserving the owner** (`contact_units` + `Owner` role).

Implemented in `UnitOccupancyTurnoverService.release_outgoing_tenant_household()`:

| Cleared on full turnover | Action                                                                                  |
| ------------------------ | --------------------------------------------------------------------------------------- |
| Family members           | Release non-owner `contact_units` (`relationship <> self`); end `Family` roles          |
| Outgoing tenant          | Move out tenant primary link; end `Tenant` role                                         |
| Vehicles                 | `VehiclesService.release_for_move_out(unit_id=...)` — all vehicles on the unit          |
| Passes                   | Cancel active unit-scoped visitor passes (`passes` where `unit_id` + `status = active`) |
| Daily help               | Set `daily_help_household_links.status = removed` for all active links on unit          |
| Walk-ins                 | Reject open `walk_in_visit_units` on awaiting/approved entries for the unit             |
| Household invitations    | Cancel pending invites for released `contact_unit_id`s                                  |
| Portal sessions          | Revoke sessions for outgoing tenant + family (not owner)                                |
| Orphan family contacts   | Soft-delete contacts with zero remaining unit links (optional cleanup)                  |

**Not cleared:** owner allotment, unit parking allotments (`unit_parking_allotments`), billing /
move-event history.

### When full turnover runs (`release_outgoing_tenant_household`)

| Trigger                    | Timing                                                                                        |
| -------------------------- | --------------------------------------------------------------------------------------------- |
| **Admin move-in**          | Before resolving/creating the new tenant `contact_units` link (sanitizes stale data)          |
| **Admin move-out**         | After insert, when `contact_id` is the **active tenant** on the unit                          |
| **Tenant request approve** | Before provisioning the new tenant (see [tenant-requests-flow.md](./tenant-requests-flow.md)) |
| **Owner contact deleted**  | Before `vacate_unit_completely`, when the unit has an active tenant (see below)               |
| **Owner unassigned**       | Before `vacate_unit_completely`, when the unit has an active tenant (see below)               |
| **Owner reassigned**       | Before `vacate_unit_completely`, when the unit has an active tenant (see below)               |

### Owner-change tenant move-out (system-triggered)

When an **owner** is removed from a unit but an **active tenant** still occupies it, the service
records a proper admin move-out for the tenant **before** vacating the owner. This keeps the
`move_events` ledger and `tenant_requests` history aligned with manual `POST /move-events` move-outs.

Implemented in `MoveEventsService.record_tenant_move_out_for_owner_change()` (called from
`ContactUnitsService._vacate_unit_for_owner_change` and
`ContactDeleteCascadeService._release_primary_holdings`):

| Step | Action                                                                                                         |
| ---- | -------------------------------------------------------------------------------------------------------------- |
| 1    | Detect active tenant via `contact_roles.get_active_tenant_contact_for_unit` + active `contact_units` link      |
| 2    | Insert `move_events` row (`move_type=move_out`, `event_date=today`, system notes)                              |
| 3    | `_release_occupancy_for_move_out` → `release_outgoing_tenant_household` (owner preserved)                      |
| 4    | `TenantRequestsService.sync_after_admin_move_out` (supersedes approved request with `move_event_id`)           |
| 5    | `UnitOccupancyTurnoverService.vacate_unit_completely(..., tenant_already_moved_out=True)` — owner-only release |

System notes on the move event:

| Owner change   | `move_events.notes`                                               |
| -------------- | ----------------------------------------------------------------- |
| Contact delete | `Tenant move-out recorded because the unit owner was deleted.`    |
| Unassign       | `Tenant move-out recorded because the unit owner was unassigned.` |
| Reassign       | `Tenant move-out recorded because the unit owner was reassigned.` |

**Differences from manual `POST /move-events` move-out:**

- No push notification to the tenant (owner change is staff-initiated).
- `vacate_unit_completely` skips duplicate tenant supersede and unit-scoped asset cleanup when
  `tenant_already_moved_out=True` (steps 3–4 already ran).
- Owner contact row is **not** deleted on unassign/reassign (only on contact delete).

**When no tenant exists:** owner vacate runs `vacate_unit_completely` with full cleanup as before.

### Single-occupant move-out

When admin move-out is for a **family member** (not the active tenant),
`release_single_occupant()` runs instead: only that contact's link, role, vehicles, passes,
invitations, and portal session — the rest of the household stays on the unit.

### Historical backfill

SQL migrations (no portal session revoke — app/runtime only):

- `20260827140000_backfill_tenant_requests_from_move_ins.sql` — owner-visible `tenant_requests`
- `20260827150000_backfill_stale_unit_household_artifacts.sql` — stale family / vehicles / passes / daily help

After **service deploy**, new admin move-in/out paths invoke turnover in Python (including portal
session revoke). Backfills only repair pre-deploy data gaps.

______________________________________________________________________

## 3. Data model

Reuses `units`, `unit_configs`, `contacts`, `contact_units`, `towers`; adds **one** table. Full
column reference and rationale in [ADR 0005](./adr/0005-move-events.md).

| Table                      | Purpose                                                                        |
| -------------------------- | ------------------------------------------------------------------------------ |
| `move_events` (new)        | One move-in / move-out record: unit, contact, type, date, fee, notes, docs     |
| `units` (existing)         | Unit label/code + `project_id`; join `unit_configs.kind` for the "Type" column |
| `contacts` (existing)      | The person moving (identity)                                                   |
| `contact_roles` (existing) | Active role on the unit (`Owner` / `Tenant`) — joined for list “Role” column   |
| `contact_units` (existing) | Current occupancy — synced on each record (`active` / `moved_out`)             |

Key enum (Postgres `move_event_type` + `schemas/enums.py` `MoveEventType`): `move_in` / `move_out`,
plus a list-filter helper `MoveEventListBucket`.

> **History vs current state.** `move_events` is an append-only **ledger** (a unit+contact can move
> in and out repeatedly). `contact_units` holds the **current** occupancy and is updated by the
> service in the same transaction. See [ADR 0005 §2](./adr/0005-move-events.md).

______________________________________________________________________

## 4. API catalog

All routes under `/v1/move-events`, authenticated + org-scoped, guarded by `contacts_management.*`.

| Method | Path                   | RBAC                         | Purpose                                                                 |
| ------ | ---------------------- | ---------------------------- | ----------------------------------------------------------------------- |
| GET    | `/v1/move-events`      | `contacts_management.view`   | List — `bucket`, `search`, `unit_id`, `project_id`, `page`, `page_size` |
| POST   | `/v1/move-events`      | `contacts_management.create` | Record a move-in / move-out (syncs `contact_units`)                     |
| GET    | `/v1/move-events/{id}` | `contacts_management.view`   | Move detail (unit, contact, fee, notes, documents)                      |
| PATCH  | `/v1/move-events/{id}` | `contacts_management.edit`   | Correct `event_date` / `fee_amount` / `notes` / docs                    |
| DELETE | `/v1/move-events/{id}` | `contacts_management.delete` | Soft-void a mistaken record (`deleted_at`)                              |

### Example: `POST /v1/move-events`

**Request:**

```json
{
  "unit_id": "uuid-of-unit",
  "contact_id": "uuid-of-contact",
  "move_type": "move_in",
  "event_date": "2026-05-25",
  "fee_amount": 5000,
  "fee_currency": "INR",
  "notes": "Handover complete; keys issued.",
  "document_paths": ["moves/2026/05/inspection-front.jpg"]
}
```

**Behavior (`POST /v1/move-events`):**

1. Resolve admin + `organization_id`; verify `unit_id` exists in the org
   (`move_events.errors.unit_not_found`) and `contact_id` exists (`move_events.errors.contact_not_found`).
1. Derive `project_id` from the unit.
1. **Move-in guards:** unit must be sold/allotted; no other **active Tenant** role on the unit
   (`move_events.errors.unit_not_sold`, `move_events.errors.unit_occupied_by_other_tenant`).
1. **Move-in turnover:** `UnitOccupancyTurnoverService.release_outgoing_tenant_household()` clears
   stale household artifacts on the unit (owner preserved) before the new tenant is linked.
1. **Move-out guard:** contact must have an `active`/`pending` link on the unit
   (`move_events.errors.not_currently_occupying`).
1. Resolve the `contact_units` link for that unit+contact (create/attach if the admin is recording a
   fresh move-in and no link exists — see §5).
1. Insert the `move_events` row (`recorded_by_user_id = admin's auth user id`).
1. **Sync occupancy** on `contact_units` in the same `db_uow`:
   - `move_in` → `status='active'`, `activated_at = COALESCE(activated_at, event_date)`, `moved_out_at = NULL`;
   - `move_out` → turnover service handles link release (no separate `sync_move_out` on the primary row).
1. **Move-out turnover:** if `contact_id` is the active tenant → full household cleanup; else
   `release_single_occupant()` for one family member.
1. **Move-in only:** assign `Tenant` role when missing; call
   `TenantRequestsService.sync_after_admin_move_in()` so the owner mobile list shows an approved
   tenant request (mirrors ledger row — see [tenant-requests-flow.md](./tenant-requests-flow.md)).
1. **Move-out only:** call `TenantRequestsService.sync_after_admin_move_out()` to mark the active
   approved `tenant_requests` row as `superseded` (so owner history no longer shows a current tenant).
1. Return the created move with joined display fields (unit label, type, contact name/role).

### Example: `GET /v1/move-events?bucket=move_out&search=A-0101`

**Response (shape):**

```json
{
  "data": {
    "items": [
      {
        "id": "uuid",
        "unit_id": "uuid",
        "unit_code": "A-0101",
        "unit_tower_name": "Tower A",
        "unit_type": "apartment",
        "contact_id": "uuid",
        "contact_name": "Arjun Babu",
        "contact_role": "Tenant",
        "move_type": "move_out",
        "event_date": "2026-05-08",
        "fee_amount": "2000.00",
        "fee_currency": "INR"
      }
    ],
    "total": 1
  }
}
```

______________________________________________________________________

## 5. Business rules & gating

Enforced in `move_events_service.py`:

- **Org scope:** every query filters by `organization_id`; unknown/mismatched ids → 404
  (`move_events.errors.move_event_not_found` / `unit_not_found` / `contact_not_found`).
- **Occupancy sync (the core rule):** recording a move **and** updating `contact_units` happen in
  **one `db_uow` transaction** so history and current state never diverge.
  - `move_in`: pre-turnover sanitize → link → `active`; set `activated_at` if unset; clear `moved_out_at`;
    ensure `Tenant` role; sync owner-visible `tenant_requests`.
  - `move_out`: requires the contact to currently occupy the unit (an `active`/`pending` `contact_units`
    link), else `move_events.errors.not_currently_occupying`; turnover releases links and clears
    household artifacts (full household when contact is the active tenant).
- **Fresh move-in without a link:** if no `contact_units` row exists for the unit+contact, create one
  (`relationship` defaults to `self`; `is_primary=false`) and activate it. (Alternatively require the
  link first — decide per product; default is auto-create.)
- **Move-in guards:**
  - Unit must be **sold/allotted** (`resolve_is_sold`: inventory `occupied`/`blocked`, or `vacant`
    with an active Owner allotment) — else `move_events.errors.unit_not_sold`.
  - No other contact may hold an **active Tenant** role on the unit — else
    `move_events.errors.unit_occupied_by_other_tenant`.
- **Fee:** `fee_amount` optional, must be `>= 0` (`move_events.errors.invalid_fee`); `fee_currency`
  defaults to `INR`.
- **Edit:** `PATCH` may correct `event_date`, `fee_amount`, `fee_currency`, `notes`, `document_paths`
  only — **not** `move_type`/`unit_id`/`contact_id` (record a new event instead). Editing an
  `event_date` on a **move-in** re-syncs the corresponding `contact_units` `activated_at` timestamp
  (turnover is **not** re-run on patch).
- **Delete:** soft-void via `deleted_at` (row kept for audit); voiding the **latest** move for a
  unit+contact should re-derive occupancy from the previous move (or leave `contact_units` untouched
  with an admin note — decide per product; default: re-derive from the prior non-deleted move).
- **Type = Status:** the UI "Status" badge is `move_type`; there is no separate lifecycle status in
  Phase 1 (see [ADR 0005 §5](./adr/0005-move-events.md)).

______________________________________________________________________

## 6. Cross-cutting conventions (match existing flows)

- **Auth & org scope:** every request resolves an admin + `organization_id`; every query filters by
  `organization_id`. Missing/mismatched row → 404 `move_events.errors.move_event_not_found`.
- **RBAC:** enforced per endpoint via `check_permissions` with `contacts_management.*` codes.
- **Responses:** `success_response` for single objects, `list_response` for collections; user-facing
  text via i18n `message_key`s under `move_events.*` in `app/locales/en.json`.
- **Writes vs reads:** writes use `db_uow` (transaction), reads use `db_conn`.
- **Auditing:** mutations wrapped with `@audit_api_call` (`table_name="move_events"`,
  `category="MOVE_EVENTS"`).
- **Rate limiting:** `@limiter.limit` — reads `100/minute`, writes `30/minute`.
- **Enums:** Python enums mirror Postgres; repositories cast explicitly (e.g. `$N::move_event_type`).
- **Media:** `document_paths` stores storage paths only — no raw blobs (like `vehicles.photo_paths`).

______________________________________________________________________

## 7. How to make common changes

| I want to…                              | Change here                                                                                                                                |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Add a move type                         | `MoveEventType` enum + Postgres `move_event_type` enum                                                                                     |
| Add/rename a move field                 | new migration + `move_events_repository.py` SQL + `schemas/move_events.py`                                                                 |
| Change occupancy-sync / turnover rules  | `move_events_service.py` + `unit_occupancy_turnover_service.py`                                                                            |
| Change tenant-request mirror on move-in | `tenant_requests_service.sync_after_admin_move_in`                                                                                         |
| Change tenant-request close on move-out | `tenant_requests_service.sync_after_admin_move_out`                                                                                        |
| Change owner-change tenant move-out     | `move_events_service.record_tenant_move_out_for_owner_change` + callers in `contact_units_service.py`, `contact_delete_cascade_service.py` |
| Change list filter / search             | `move_events_repository.py` list query + `MoveEventListBucket`                                                                             |
| Add scheduled/completed lifecycle       | add `MoveEventStatus` enum + Postgres enum + column (additive)                                                                             |
| Add an endpoint                         | route in `api/move_events.py` → service method → repository method                                                                         |
| Change a user-facing message            | `app/locales/en.json` under `move_events.*`                                                                                                |
| Change RBAC required for an action      | the `check_permissions(...)` call on that endpoint                                                                                         |
| Wire the fee to billing                 | `move_events_service.py` (emit/charge) when a billing module lands                                                                         |

______________________________________________________________________

## 8. Error keys (add under `move_events.errors.*`)

| Key                                                | When                                                     |
| -------------------------------------------------- | -------------------------------------------------------- |
| `move_events.errors.move_event_not_found`          | Invalid `move_event_id` / wrong org                      |
| `move_events.errors.unit_not_found`                | `unit_id` not in the org                                 |
| `move_events.errors.contact_not_found`             | `contact_id` not in the org                              |
| `move_events.errors.not_currently_occupying`       | move-out for a contact with no `active` link to the unit |
| `move_events.errors.unit_not_sold`                 | move-in on a unit that is not sold or owner-allotted     |
| `move_events.errors.unit_occupied_by_other_tenant` | move-in while another tenant is active on the unit       |
| `move_events.errors.invalid_fee`                   | `fee_amount < 0`                                         |

______________________________________________________________________

## 9. Implementation phases

### Phase 1 — Foundation

- [x] Add `MoveEventType` (+ `MoveEventListBucket`) to `schemas/enums.py`
- [x] Supabase migrations: `move_event_type` enum + `move_events` table + indexes
- [x] Reuse `contacts_management.*` RBAC codes (no separate move-events permissions)
- [x] `schemas/move_events.py` request/response models
- [x] `MoveEventsRepository` (create/list/get/update/soft-delete)
- [x] Register `move_events_router` in `routes.py`

### Phase 2 — Record & list

- [x] `POST /move-events` (validation + `contact_units` occupancy sync in one `db_uow`)
- [x] `GET /move-events` (All / move_in / move_out bucket + search + joins)
- [x] `GET /move-events/{id}` detail
- [x] `PATCH /move-events/{id}`, `DELETE /move-events/{id}` (soft-void + re-derive occupancy)
- [x] Unit occupancy turnover on move-in / tenant move-out (`UnitOccupancyTurnoverService`)
- [x] `sync_after_admin_move_in` — owner tenant-requests list stays in sync
- [x] `sync_after_admin_move_out` — approved request closed on tenant move-out
- [x] Unit tests: occupancy sync, move-out guard, fee validation, list filter/search, turnover hooks

### Phase 3 — Hardening (optional)

- [ ] CSV/XLSX export of the move register
- [ ] RLS policies (`organization_id` + admin role)
- [ ] `MoveEventStatus` (scheduled / completed) if moves get pre-booked
- [ ] Kafka event `move_events.recorded`; billing integration for `fee_amount`

______________________________________________________________________

## 10. Tests

- `tests/unit/test_move_events_service.py` — occupancy sync (in), move-out guard, fee validation,
  soft-delete re-derivation, turnover service hooks.
- `tests/unit/test_unit_occupancy_turnover_service.py` — household cleanup (links, vehicles, passes,
  daily help, invitations).
- `tests/unit/test_move_events_repository.py` — SQL generation (insert / list-by-bucket+search / get).

Run: `.venv/bin/python -m pytest apps/user_service/tests/unit`

______________________________________________________________________

## Related

- Design decision & new tables: [ADR 0005 — Move events](./adr/0005-move-events.md)
- Occupancy link produced/synced: [contact-onboarding-flow.md](./contact-onboarding-flow.md) (`contact_units`)
- Tenant approval + supersede (same turnover rules): [tenant-requests-flow.md](./tenant-requests-flow.md)
- Inventory (`units`, `unit_configs`, `towers`): [project-setup-flow.md](./project-setup-flow.md)
- Staff RBAC + `organization_member` model: [ADR 0004](./adr/0004-pass-validation-gate.md)
