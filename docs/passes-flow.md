# Visitor Passes Flow — Context & Change Guide

> **Status: Planned / target design.** This flow is **not built yet**. This document is the
> build guide for the **Visitor Passes** feature in `user_service`, written in the same style as
> [`contact-onboarding-flow.md`](./contact-onboarding-flow.md) and
> [`project-setup-flow.md`](./project-setup-flow.md) so it drops straight into the codebase.
> The schema/architecture rationale lives in [ADR 0003](./adr/0003-visitor-passes.md).

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **API prefix:** `/v1/passes`
- **DB schema:** `ats-home-craft-supabase` (new migrations `2026XXXXXXXXXX_visitor_passes_*`)
- **Design decision:** [`docs/adr/0003-visitor-passes.md`](./adr/0003-visitor-passes.md)

______________________________________________________________________

## 1. What this flow does

A **household contact (resident)** who has completed onboarding — i.e. has one or more **active
`contact_units`** — can issue **visitor passes** for guests against a unit they own. Each pass:

1. captures a **guest** as a plain **name + phone** (picked from the **device's phone contacts** or
   typed by the resident — the backend does **not** create a contact for the guest),
1. has a **validity window** and an **entry model** (one-time or recurring / multi-entry),
1. produces a **4-digit code** rendered as a **QR** plus a **generated pass image** the resident
   shares manually (there is **no share link, no token, and no SMS**),
1. is scanned at the **community gate** (or the 4-digit code is keyed in), producing **check-in /
   check-out** events,
1. shows a **timeline** of those events and a **status** the resident tracks (upcoming / active /
   expired / used / cancelled).

The acting resident is resolved from the JWT via `extract_onboarding_contact_context()` (same as
onboarding). There are **no `*_MANAGEMENT_*` RBAC codes** on the resident routes — authorization is
"you can only create/see passes for units you actively own".

### Screen → capability map (from the Figma "Passes" board)

| Screen                         | Capability                                                           |
| ------------------------------ | -------------------------------------------------------------------- |
| Passes (list, tabbed)          | \`GET /passes?bucket=upcoming                                        |
| Create Pass / Add Pass         | `POST /passes` (incl. `is_private` toggle)                           |
| Select Guest                   | **Client-side** (device phone contacts) — no backend endpoint        |
| Add New (guest)                | **Client-side** (typed name + phone) — sent inline in `POST /passes` |
| Pass / View Details            | `GET /passes/{pass_id}` (incl. 4-digit `code` + timeline)            |
| MyPass (QR)                    | `code` (4-digit) in the details response → app renders the QR        |
| Sharable Image                 | `pass_image_path` (generated image; resident shares it manually)     |
| Section – Timeline             | `pass_events` returned in details / `GET /passes/{pass_id}/events`   |
| (gate side) scan QR / key code | `POST /passes/verify` → `check-in` / `check-out` (Phase 2)           |

______________________________________________________________________

## 2. Architecture (layers)

Same 3-layer FastAPI pattern as the rest of the service:

```
HTTP → API router → Service (business rules) → Repository (SQL) → Postgres
```

### File map (to create)

| Concern                              | File                                                                |
| ------------------------------------ | ------------------------------------------------------------------- |
| API endpoints                        | `app/api/passes.py`                                                 |
| Route registration                   | `app/api/routes.py` (`passes_router`)                               |
| Pass orchestration / status-gating   | `app/services/passes_service.py`                                    |
| Unit ownership check                 | `app/db/repositories/contact_units_repository.py` (existing)        |
| Gate verify / check-in-out (Phase 2) | `app/services/pass_verification_service.py`                         |
| Pass persistence                     | `app/db/repositories/passes_repository.py`                          |
| Timeline persistence                 | `app/db/repositories/pass_events_repository.py`                     |
| Request/response models              | `app/schemas/passes.py`                                             |
| Enums (mirror Postgres)              | `app/schemas/enums.py`                                              |
| Contact context resolver             | `extract_onboarding_contact_context` in `app/utils/common_utils.py` |
| 4-digit code generation              | helper in `passes_service.py` (retry on collision)                  |
| i18n messages                        | `app/locales/en.json` (`passes.*`)                                  |

`PassesService` **composes** the existing `ContactUnitsRepository` for ownership checks rather than
duplicating it. No guest contacts are created, so `ContactsService` is **not** involved.

______________________________________________________________________

## 3. Data model

Reuses `contacts`, `contact_units`, `units`, `tower_gates`; adds **two** tables. Full column
reference and rationale in [ADR 0003](./adr/0003-visitor-passes.md).

| Table                      | Purpose                                                                             |
| -------------------------- | ----------------------------------------------------------------------------------- |
| `contacts` (existing)      | Host resident only — the **guest is not a contact**                                 |
| `contact_units` (existing) | Proves the host actively owns the unit the pass is issued for                       |
| `passes` (new)             | The pass: host, unit, guest snapshot, validity, entry model, status, 4-digit `code` |
| `pass_events` (new)        | Append-only timeline: created / checked_in / checked_out / cancelled …              |

Key enums (Postgres + `schemas/enums.py`): `PassType`, `PassValidityType`, `PassStatus`,
`PassEventType`, `PassActorType` (who logged a timeline event: `resident` / `staff` / `system`),
plus a **derived-only** `PassDisplayStatus`.

> `pass_events.actor_user_id` stores `auth.users.id` (works for both a resident `contacts.user_id`
> and a staff/security `organization_member`); `actor_type` disambiguates. See
> [ADR 0003 → `pass_events`](./adr/0003-visitor-passes.md) for the rationale.

**Status is derived for the UI, stored simply in the DB.** Persisted `passes.status` is one of
`active | completed | expired | cancelled`. The list buckets **upcoming / active / expired / used**
are computed on read from `status` + `valid_from`/`valid_until` + `now()` (see `_derive_display_status`).

______________________________________________________________________

## 4. API catalog

All routes under `/v1/passes`. The acting resident is resolved from the JWT, so resident endpoints
take **no** contact id in the path.

### Resident (contact-scoped)

| Method | Path                          | Purpose                                                                |
| ------ | ----------------------------- | ---------------------------------------------------------------------- |
| GET    | `/v1/passes`                  | List my passes — `bucket`, `unit_id`, `pass_type`, `page`, `page_size` |
| POST   | `/v1/passes`                  | Create a pass (inline guest name + phone) → returns 4-digit `code`     |
| GET    | `/v1/passes/{pass_id}`        | Pass details incl. 4-digit `code`, `pass_image_path`, timeline         |
| PATCH  | `/v1/passes/{pass_id}`        | Edit an `active`/upcoming pass (validity, guest, notes)                |
| POST   | `/v1/passes/{pass_id}/cancel` | Cancel a pass (→ `cancelled` + `pass_events` row)                      |
| GET    | `/v1/passes/{pass_id}/events` | Timeline for a pass                                                    |

> **No guest endpoints.** The "Select Guest" / "Add New" screens are handled entirely on the device
> (phone contacts); the chosen name + phone are sent inline in `POST /passes`.
> **No share endpoint.** Sharing is the resident sending the generated pass image manually.

### Gate / security (Phase 2 — `VISITOR_MANAGEMENT_*`)

| Method | Path                             | Purpose                                                    |
| ------ | -------------------------------- | ---------------------------------------------------------- |
| POST   | `/v1/passes/verify`              | Validate a scanned/keyed **4-digit `code`** (returns pass) |
| POST   | `/v1/passes/{pass_id}/check-in`  | Record entry (`entry_count++`, `checked_in` event)         |
| POST   | `/v1/passes/{pass_id}/check-out` | Record exit (`checked_out` event; `completed` if one-time) |

### Example: `POST /v1/passes`

**Request:**

```json
{
  "unit_id": "uuid-of-owned-unit",
  "pass_type": "guest",
  "guest_name": "Ravi Kumar",
  "guest_phone_isd_code": "+91",
  "guest_phone_number": "9876543210",
  "visitor_count": 2,
  "vehicle_number": "KA01AB1234",
  "valid_from": "2026-07-10T09:00:00Z",
  "valid_until": "2026-07-10T21:00:00Z",
  "validity_type": "one_time",
  "is_private": true,
  "purpose": "Family visit"
}
```

**`is_private`:** maps to the **"Make it private"** toggle. When `true`, guest check-in should not
notify other household members (silent entry). Stored on the pass; notification logic is gate-phase.

**Behavior:**

1. Resolve current contact; verify `unit_id` is in the contact's **active** `contact_units`
   (`ContactUnitsRepository.contact_has_active_unit`) else `passes.errors.unit_not_owned`.
1. Persist the guest **snapshot** (`guest_name` + phone) straight onto the pass — no contact row.
1. Generate a **4-digit `code`** unique among the org's usable passes (retry on collision).
1. Insert `passes` (`status='active'`, `entry_count=0`); insert `pass_events` `created`.
1. Return the pass incl. the 4-digit `code` (the app renders the QR from it).

### Example: `GET /v1/passes?bucket=active`

**Response (shape):**

```json
{
  "data": {
    "items": [
      {
        "id": "uuid",
        "code": "4821",
        "guest_name": "Ravi Kumar",
        "pass_type": "guest",
        "unit_label": "A-1203",
        "valid_from": "2026-07-10T09:00:00Z",
        "valid_until": "2026-07-10T21:00:00Z",
        "status": "active",
        "display_status": "active",
        "entry_count": 1
      }
    ],
    "total": 1
  }
}
```

______________________________________________________________________

## 5. Business rules & gating

Enforced in `passes_service.py`:

- **Unit ownership:** every create/edit checks the host contact has an **active** `contact_units`
  link to `unit_id` (`passes.errors.unit_not_owned`). Reuse
  `ContactUnitsRepository.contact_has_active_unit`.
- **Validity:** `valid_until > valid_from` (DB `CHECK` + service validation → `passes.errors.invalid_validity`).
- **Ownership on mutate:** cancel/edit/share only operate on a pass whose `host_contact_id` matches
  the current contact and `organization_id` matches (else 404 `passes.errors.pass_not_found`).
- **Cancel:** allowed only when `status='active'`; sets `cancelled`, writes a `cancelled` event.
- **Edit:** allowed only when derived `display_status` ∈ {`upcoming`, `active`}; not on
  `used`/`expired`/`cancelled`. `is_private` may be toggled on patch while editable.
- **Derived display status** (`_derive_display_status`):
  - `cancelled` if `status='cancelled'`;
  - `used` if one-time and `entry_count > 0` and checked out (or `status='completed'`);
  - `expired` if `now() > valid_until`;
  - `upcoming` if `now() < valid_from`;
  - else `active`.
- **Gate verify (Phase 2):** lookup by the **4-digit `code`** (scoped to the org, usable passes);
  reject if `cancelled`, outside window, or a one-time pass already used
  (`passes.errors.pass_invalid_or_expired`). Check-in increments `entry_count` (respecting
  `max_entries` for multi-entry); check-out writes `checked_out` and, for one-time passes, sets
  `status='completed'`.

______________________________________________________________________

## 6. QR + image (no link, no token, no SMS)

- **`code`**: a **4-digit numeric** value (e.g. `4821`). It is the gate lookup key and the exact
  payload encoded in the QR — there is no URL or opaque token.
- **Uniqueness:** the code is unique per org **only among usable passes** (partial unique index
  `WHERE status = 'active'`), so codes are recycled after a pass expires/cancels/completes. The
  generator picks a random 4-digit value and retries on collision.
- **QR:** the mobile app renders the QR directly from `code` (the "MyPass" screen). The backend does
  not need to produce the QR bitmap.
- **Sharable image:** the "Sharable Image" screen is a generated pass card (guest, unit, validity,
  QR). Only its **path** is stored in `pass_image_path` (no blob) if server-side generation is used;
  otherwise the app generates and shares it locally. The resident shares it **manually** — the
  backend sends **no SMS/link**.

______________________________________________________________________

## 7. Cross-cutting conventions (match existing flows)

- **Auth & org scope:** resident routes resolve contact + `organization_id`; every query filters by
  `organization_id`. Missing/mismatched pass → 404 `passes.errors.pass_not_found`.
- **Responses:** `success_response` for single objects, `list_response` for collections; user-facing
  text via i18n `message_key`s under `passes.*` in `app/locales/en.json`.
- **Writes vs reads:** writes use `db_uow` (transaction), reads use `db_conn`.
- **Auditing:** mutations wrapped with `@audit_api_call` (`table_name` = `passes` | `pass_events`,
  `category="VISITOR_PASSES"`).
- **Rate limiting:** `@limiter.limit` — reads `100/minute`, writes `30/minute`.
- **Enums:** Python enums mirror Postgres; repositories cast explicitly
  (e.g. `$N::pass_status`, `$N::pass_event_type`).

______________________________________________________________________

## 8. How to make common changes

| I want to…                      | Change here                                                      |
| ------------------------------- | ---------------------------------------------------------------- |
| Add a pass type (e.g. `event`)  | `PassType` enum + Postgres `pass_type` enum                      |
| Add/rename a pass field         | new migration + `passes_repository.py` SQL + `schemas/passes.py` |
| Change status/bucket derivation | `_derive_display_status` in `passes_service.py`                  |
| Change ownership/validity rules | `passes_service.py` create/edit/cancel guards                    |
| Add a timeline event type       | `PassEventType` enum + Postgres `pass_event_type` enum           |
| Add an endpoint                 | route in `api/passes.py` → service method → repository method    |
| Change a user-facing message    | `app/locales/en.json` under `passes.*`                           |
| Change the 4-digit code format  | code generator in `passes_service.py` + partial unique index     |
| Add gate scanning               | `pass_verification_service.py` + Phase 2 routes + RBAC code      |

______________________________________________________________________

## 9. Error keys (add under `passes.errors.*`)

| Key                                     | When                                                  |
| --------------------------------------- | ----------------------------------------------------- |
| `passes.errors.unit_not_owned`          | `unit_id` not in host's active `contact_units`        |
| `passes.errors.pass_not_found`          | Invalid `pass_id` / wrong org / not host's pass       |
| `passes.errors.invalid_validity`        | `valid_until <= valid_from`                           |
| `passes.errors.pass_not_editable`       | Edit/cancel on a used/expired/cancelled pass          |
| `passes.errors.pass_invalid_or_expired` | Gate verify fails (cancelled / outside window / used) |
| `passes.errors.max_entries_reached`     | Multi-entry pass hit `max_entries` on check-in        |

______________________________________________________________________

## 10. Implementation phases

### Phase 1 — Foundation

- [ ] Add `PassType`, `PassValidityType`, `PassStatus`, `PassEventType`, `PassActorType` to `schemas/enums.py`
- [ ] Supabase migrations: enums + `passes` + `pass_events`
- [ ] `schemas/passes.py` request/response models
- [ ] `PassesRepository` (create/list/get/cancel) + `PassEventsRepository`
- [ ] Register `passes_router` in `routes.py`

### Phase 2 — Create & list (resident)

- [ ] `POST /passes` (ownership check, 4-digit code generation, `created` event)
- [ ] `GET /passes` with bucket filter + `_derive_display_status`
- [ ] `GET /passes/{id}` details incl. `code` + timeline
- [ ] `PATCH /passes/{id}`, `POST /passes/{id}/cancel`
- [ ] `GET /passes/{id}/events`
- [ ] Unit tests: ownership, validity, status derivation, cancel guard, code collision retry

### Phase 3 — Gate side

- [ ] `POST /passes/verify` (4-digit `code` lookup)
- [ ] `POST /passes/{id}/check-in` / `check-out` (entry_count, events, `completed`)
- [ ] `VISITOR_MANAGEMENT_*` RBAC + gate/device auth model

### Phase 4 — Hardening

- [ ] RLS policies (host `contacts.user_id` + gate role)
- [ ] Optional nightly sweep to persist `expired`/`completed`
- [ ] Optional Kafka events (`passes.created`, `passes.checked_in`)

______________________________________________________________________

## 11. Tests

- `tests/unit/test_passes_service.py` — ownership check, validity, `_derive_display_status`, cancel/edit guards, code generation/collision.
- `tests/unit/test_passes_repository.py` — SQL generation (insert / list-by-bucket / code lookup).

Run: `.venv/bin/python -m pytest apps/user_service/tests/unit`

______________________________________________________________________

## Related

- Design decision & new tables: [ADR 0003 — Visitor passes](./adr/0003-visitor-passes.md)
- Downstream flow (gate check-in/out + Visitor Logs): [ADR 0004](./adr/0004-pass-validation-gate.md) / [passes-validation-flow.md](./passes-validation-flow.md)
- Upstream flow (produces active `contact_units`): [contact-onboarding-flow.md](./contact-onboarding-flow.md)
- Inventory (`units`, `tower_gates`): [project-setup-flow.md](./project-setup-flow.md)
- Unit-ownership check reused: `ContactUnitsRepository.contact_has_active_unit`
