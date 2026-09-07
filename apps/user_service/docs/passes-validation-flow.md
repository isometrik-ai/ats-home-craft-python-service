# Pass Validation Flow — Context & Change Guide

> **Status: Planned / target design.** This flow is **not built yet**. It is the build guide for the
> **gate/security + admin Visitor Logs** half of Visitor Passes in `user_service`, written in the same
> style as [`passes-flow.md`](./passes-flow.md) (resident side) so it drops straight into the codebase.
> The schema/architecture rationale lives in [ADR 0004](./adr/0004-pass-validation-gate.md); the
> resident side that produces the passes being validated is [ADR 0003](./adr/0003-visitor-passes.md) /
> [`passes-flow.md`](./passes-flow.md).

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **API prefixes:** `/v1/passes` (gate actions) and `/v1/visitor-logs` (admin dashboard)
- **DB schema:** `ats-home-craft-supabase` (new migrations `20260717XXXXXX_visitor_gate_*`)
- **Design decision:** [`docs/adr/0004-pass-validation-gate.md`](./adr/0004-pass-validation-gate.md)

______________________________________________________________________

## 1. What this flow does

The resident flow ([`passes-flow.md`](./passes-flow.md)) produces an **active pass** with a **4-digit
`code`** (rendered as a QR). This flow is what happens **at the gate** and in the **community-admin
dashboard**:

1. A **security guard** (an `organization_member`) **scans the QR** or **keys the 4-digit code** at a
   gate → the app calls **verify** and shows who is at the gate (guest, flat, validity, decision).
1. The guard **admits** the guest → **check-in** records an entry event (with `entry_method` and
   `access_status`), increments `entry_count`, and stamps the guard + gate.
1. On exit, the guard records a **check-out** → exit event; a **one-time** pass becomes `completed`.
1. A **community admin** opens **Visitor Logs** → a filterable table of visits (type, flat, created
   by, scheduled window, entry type, guard, access status, IN/OUT, time spent) with **overview cards**
   and **exports** (entry/exit details, monthly report).

Unlike the resident routes, gate + admin actors are **staff**, so these routes use **RBAC codes**
(`visitor_management.*`) resolved via `check_permissions()` → `organization_member` context.

### Screen → capability map (from the "Visitor Logs" board)

| Screen element                               | Capability                                              |
| -------------------------------------------- | ------------------------------------------------------- |
| (gate) scan QR / key code                    | `POST /v1/passes/verify` (read-only lookup + decision)  |
| (gate) admit guest                           | `POST /v1/passes/{pass_id}/check-in`                    |
| (gate) mark exit                             | `POST /v1/passes/{pass_id}/check-out`                   |
| Overview cards (Total Visitors / IN / …)     | `GET /v1/visitor-logs/overview`                         |
| Visitor Logs table + filters                 | `GET /v1/visitor-logs`                                  |
| Entry Type column (QR / Pass)                | `pass_events.entry_method` (`qr` / `code` / `manual`)   |
| Access Status (Approved / Granted / Expired) | `pass_events.access_status`                             |
| Guard column                                 | `pass_events.actor_label` / `actor_user_id`             |
| IN / OUT / Time Spent                        | derived from `checked_in` / `checked_out` `occurred_at` |
| Details (eye)                                | `GET /v1/visitor-logs/{pass_id}`                        |
| Entry/Exit details (download)                | `GET /v1/visitor-logs/export`                           |
| Monthly report                               | `GET /v1/visitor-logs/monthly-report`                   |

______________________________________________________________________

## 2. Architecture (layers)

Same 3-layer FastAPI pattern as the rest of the service:

```
apps/user_service/app/
├── api/
│   ├── gate_passes.py        # /v1/passes/verify, /{id}/check-in, /{id}/check-out  (RBAC: visitor_management.verify)
│   └── visitor_logs.py       # /v1/visitor-logs*                                    (RBAC: visitor_management.view)
├── services/
│   ├── pass_verification_service.py  # verify + check-in/out business rules
│   └── visitor_logs_service.py       # list/overview/export shaping
├── db/repositories/
│   ├── passes_repository.py          # (exists) + get_by_code, increment_entry_count, complete
│   ├── pass_events_repository.py     # (exists) + insert check-in/out, latest-event join
│   └── visitor_logs_repository.py    # log list/overview aggregate queries
└── schemas/
    ├── gate_passes.py        # VerifyRequest/Response, CheckInRequest, CheckOutRequest
    └── visitor_logs.py       # VisitorLogQuery, VisitorLogItem, VisitorLogOverview
```

Reuse: `check_permissions()` (RBAC), `db_conn` (reads) / `db_uow` (writes), `success_response` /
`list_response`, i18n `message_key`s, `@audit_api_call`, `@limiter.limit`.

______________________________________________________________________

## 3. Data model (see ADR 0004)

**No new core tables.** Gate actions are `pass_events` rows on the existing pass; only two additive
columns + two enums are added.

### New enums (Postgres + `app/schemas/enums.py`)

```python
class PassEntryMethod(str, Enum):
    QR = "qr"          # guard scanned the QR
    CODE = "code"      # guard keyed the 4-digit code
    MANUAL = "manual"  # walk-in / override, no code

class PassAccessStatus(str, Enum):
    APPROVED = "approved"  # valid pre-created pass, inside window
    GRANTED = "granted"    # manual / tele approval
    EXPIRED = "expired"    # window lapsed
    DENIED = "denied"      # cancelled / used / max-entries
```

### `pass_events` — two new columns (only schema change)

| Column          | Type                      | Set on       | Notes                                   |
| --------------- | ------------------------- | ------------ | --------------------------------------- |
| `entry_method`  | `pass_entry_method NULL`  | `checked_in` | how the guest was admitted              |
| `access_status` | `pass_access_status NULL` | `checked_in` | gate decision (also on refused entries) |

Everything else is reused (ADR 0003): `pass_events.gate_id`, `actor_type='staff'`, `actor_user_id`,
`actor_label`, `occurred_at`, `metadata`; `passes.entry_count`, `passes.status`,
`passes.allow_multiple_entries`, `passes.max_entries`, `passes.code`.

______________________________________________________________________

## 4. Gate endpoints (`/v1/passes`, RBAC `visitor_management.verify`)

### `POST /v1/passes/verify` — lookup by code (read-only)

Request:

```json
{ "code": "4821", "gate_id": "uuid (optional)" }
```

Behaviour (in `pass_verification_service.verify`):

1. `passes_repo.get_by_code(organization_id, code)` — org-scoped, usable passes only
   (`status='active'` partial unique index guarantees at most one).
1. Not found → `404 passes.errors.pass_not_found`.
1. Compute **`access_status`** (does **not** write):
   - `cancelled` → `denied`
   - `now() > valid_until` (or `status='expired'`) → `expired`
   - one-time already used / `entry_count >= max_entries` → `denied`
   - `now() < valid_from` → still `approved` but flag `too_early=true` (guard decides)
   - else → `approved`
1. Return the pass snapshot the guard needs **before** admitting:

```json
{
  "pass_id": "uuid",
  "code": "4821",
  "guest_name": "Ravi Kumar",
  "guest_phone": "+91 98765 43210",
  "visitor_count": 2,
  "vehicle_number": "KA01AB1234",
  "pass_type": "guest",
  "unit_label": "A-803",
  "tower_name": "Tower A",
  "host_name": "N. Reddy",
  "valid_from": "2026-07-17T09:00:00Z",
  "valid_until": "2026-07-17T21:00:00Z",
  "is_private": false,
  "access_status": "approved",
  "can_check_in": true
}
```

### `POST /v1/passes/{pass_id}/check-in` — record entry (write, `db_uow`)

Request:

```json
{ "gate_id": "uuid", "entry_method": "qr", "access_status": "approved", "notes": "optional" }
```

Behaviour (in `pass_verification_service.check_in`):

1. Re-fetch + re-validate the pass (guard against races / stale verify).
1. If not admissible and no override → record a `checked_in` event with
   `access_status='denied'|'expired'` and **do not** increment `entry_count` (audit of refusal),
   return `422 passes.errors.pass_invalid_or_expired` (or `max_entries_reached`).
1. If admissible:
   - `pass_events_repo.insert(event_type='checked_in', actor_type='staff', actor_user_id=<staff>, actor_label=<guard name>, gate_id, entry_method, access_status)`
   - `passes_repo.increment_entry_count(pass_id)`
   - one-time pass → leave `active` until check-out; recurring → stays `active`.
1. Return the created event + new `entry_count`.

### `POST /v1/passes/{pass_id}/check-out` — record exit (write, `db_uow`)

Request:

```json
{ "gate_id": "uuid", "notes": "optional" }
```

Behaviour:

1. Requires a prior open `checked_in` (no matching `checked_out`) → else
   `422 passes.errors.not_checked_in`.
1. Insert `checked_out` event (staff/gate stamped).
1. If `validity_type='one_time'` (or a recurring pass past `valid_until` with no remaining entries)
   → `passes_repo.complete(pass_id)` (`status='completed'`).
1. Return the created event.

______________________________________________________________________

## 5. Admin endpoints (`/v1/visitor-logs`, RBAC `visitor_management.view`)

### `GET /v1/visitor-logs` — the table

Query params (map 1:1 to the screenshot filters):

| Param              | Type               | Notes                                                     |
| ------------------ | ------------------ | --------------------------------------------------------- |
| `search`           | `str`              | name / flat / mobile (ilike over guest_name, unit, phone) |
| `month`            | `YYYY-MM`          | scheduled window month (default current)                  |
| `pass_type`        | `PassType`         | "All types"                                               |
| `entry_method`     | `PassEntryMethod`  | "All entry" (QR / code / manual)                          |
| `access_status`    | `PassAccessStatus` | "All access" (approved / granted / expired / denied)      |
| `tower_id`         | `uuid`             | "All towers"                                              |
| `page`/`page_size` | `int`              | pagination                                                |

Each row (from `visitor_logs_repository` — `passes` ⋈ latest `checked_in`/`checked_out`):

```json
{
  "pass_id": "uuid",
  "pass_type": "delivery",
  "unit_label": "B-1204",
  "tower_name": "Tower B",
  "created_by": "T. Nair",
  "scheduled_from": "2026-06-09T09:00:00Z",
  "scheduled_until": "2026-06-09T10:00:00Z",
  "entry_method": "qr",
  "guard_name": "Ramesh Kumar",
  "access_status": "approved",
  "in_time": "2026-06-09T09:12:00Z",
  "out_time": "2026-06-09T09:18:00Z",
  "time_spent_minutes": 6
}
```

### `GET /v1/visitor-logs/overview` — the cards

```json
{
  "month": "2026-06",
  "total_visitors": 28137,
  "in_count": 7316,
  "deliveries": 5065,
  "daily_help": 11255
}
```

Aggregates over the same join for the org + month:
`total_visitors` = distinct passes scheduled in month; `in_count` = `checked_in` events;
`deliveries` = `pass_type='delivery'`; `daily_help` = `pass_type IN ('service','daily_help')`
(see ADR 0004 §8 for the optional `pass_type` extension).

### `GET /v1/visitor-logs/{pass_id}` — details (eye icon)

Reuses the resident details shape + full `pass_events` timeline (all check-in/out with guard, gate,
entry method, access status).

### Exports

- `GET /v1/visitor-logs/export` — "Entry/Exit details" → CSV/XLSX stream of the filtered list.
- `GET /v1/visitor-logs/monthly-report` — "Monthly report" → summary export for the month.

Streamed responses (path/stream only — no blob table), same filters as the list.

______________________________________________________________________

## 6. RBAC (staff, unlike resident routes)

Add to `libs/shared_utils/common_query.py` (constants + `DEFAULT_PERMISSIONS`, group `visitor_logs`):

```python
VISITOR_MANAGEMENT_VIEW = "visitor_management.view"      # logs, overview, details, export
VISITOR_MANAGEMENT_VERIFY = "visitor_management.verify"  # gate verify + check-in/out
```

Routes:

```python
# gate_passes.py
user_context = await check_permissions(
    current_user, db_connection, VISITOR_MANAGEMENT_VERIFY, request=request
)

# visitor_logs.py
user_context = await check_permissions(
    current_user, db_connection, VISITOR_MANAGEMENT_VIEW, request=request
)
```

Suggested default role grants: **Security Guard** → `verify` (+ `view`); **Community Admin** →
`view`. `actor_user_id` = staff `auth.users.id`; `actor_label` = guard display name for the table.

______________________________________________________________________

## 7. Cross-cutting conventions (match existing flows)

- **Org scope:** every query filters by `organization_id` (from `check_permissions` context).
- **Writes vs reads:** verify + logs use `db_conn`; check-in / check-out use `db_uow` (transaction —
  event insert + counter/status update are atomic).
- **Responses:** `success_response` for single objects, `list_response` for collections; i18n
  `message_key`s under `passes.*` / `visitor_logs.*` in `app/locales/en.json`.
- **Auditing:** check-in / check-out wrapped with `@audit_api_call`
  (`table_name='pass_events'`, `category='VISITOR_PASSES'`).
- **Rate limiting:** reads `100/minute`, gate writes `60/minute` (gate is high-frequency).
- **Enums:** repositories cast explicitly (`$N::pass_entry_method`, `$N::pass_access_status`,
  `$N::pass_event_type`).

______________________________________________________________________

## 8. How to make common changes

| I want to…                       | Change here                                                         |
| -------------------------------- | ------------------------------------------------------------------- |
| Add an entry method (e.g. `nfc`) | `PassEntryMethod` enum + Postgres `pass_entry_method` enum          |
| Add an access outcome            | `PassAccessStatus` enum + Postgres `pass_access_status` enum        |
| Change the verify decision rules | `pass_verification_service.verify` / `check_in`                     |
| Add a Visitor Logs filter        | `VisitorLogQuery` + `visitor_logs_repository` WHERE clause          |
| Add/adjust an overview card      | `visitor_logs_repository` aggregate + `VisitorLogOverview`          |
| Change who can verify / view     | `visitor_management.*` codes + role grants                          |
| Add an export format             | `visitor_logs_service` export writer                                |
| Notify residents on entry        | Kafka `passes.checked_in` (respect `passes.is_private`) — follow-up |

______________________________________________________________________

## 9. Error keys (add under `passes.errors.*` / `visitor_logs.errors.*`)

| Key                                     | When                                              |
| --------------------------------------- | ------------------------------------------------- |
| `passes.errors.pass_not_found`          | Code not found / wrong org                        |
| `passes.errors.pass_invalid_or_expired` | Verify/check-in on cancelled / out-of-window pass |
| `passes.errors.max_entries_reached`     | Multi-entry pass hit `max_entries` on check-in    |
| `passes.errors.not_checked_in`          | Check-out with no open check-in                   |
| `visitor_logs.errors.invalid_month`     | Bad `month` filter format                         |

______________________________________________________________________

## 10. Implementation phases

### Phase A — Schema

- [ ] Migration `20260717120000_visitor_gate_enums.sql` — `pass_entry_method`, `pass_access_status`
- [ ] Migration `20260717121000_visitor_gate_columns.sql` — `pass_events.entry_method`,
  `pass_events.access_status` + index
- [ ] Add `PassEntryMethod`, `PassAccessStatus` to `schemas/enums.py`
- [ ] Add `VISITOR_MANAGEMENT_VIEW` / `VISITOR_MANAGEMENT_VERIFY` to `common_query.py`

### Phase B — Gate (verify + check-in/out)

- [ ] `passes_repository`: `get_by_code`, `increment_entry_count`, `complete`
- [ ] `pass_events_repository`: insert check-in/out; `latest_event_by_type`
- [ ] `pass_verification_service`: `verify`, `check_in`, `check_out` (decision rules)
- [ ] `api/gate_passes.py` routes (RBAC `visitor_management.verify`) + register in `routes.py`
- [ ] Unit tests: decision matrix, refusal audit, one-time completion, max-entries, race re-check

### Phase C — Visitor Logs (admin)

- [ ] `visitor_logs_repository`: list (latest-event join), overview aggregates
- [ ] `visitor_logs_service`: list/overview shaping, time-spent derivation
- [ ] `api/visitor_logs.py` routes (RBAC `visitor_management.view`) + register
- [ ] Exports: entry/exit CSV/XLSX + monthly report
- [ ] Unit tests: filters, aggregates, time-spent, tower/type filters

### Phase D — Hardening (follow-ups)

- [ ] Extend `pass_type` (`daily_help` / `vendor` / `staff`) for exact card/filter parity
- [ ] RLS policies (org + gate/admin role)
- [ ] Kafka `passes.checked_in` / `checked_out` (respect `is_private`)
- [ ] Optional gate device/session model

______________________________________________________________________

## 11. Tests

- `tests/unit/test_pass_verification_service.py` — verify decision matrix (approved/expired/denied),
  check-in increments + refusal audit, one-time → completed on check-out, max-entries guard.
- `tests/unit/test_visitor_logs_repository.py` — list SQL (latest-event join), filters, overview
  aggregate SQL.
- `tests/unit/test_visitor_logs_service.py` — row shaping, `time_spent_minutes` derivation.

Run: `.venv/bin/python -m pytest apps/user_service/tests/unit`

______________________________________________________________________

## Related

- Design decision & schema delta: [ADR 0004 — Pass validation](./adr/0004-pass-validation-gate.md)
- Resident side (produces the passes): [ADR 0003](./adr/0003-visitor-passes.md) /
  [`passes-flow.md`](./passes-flow.md)
- Staff RBAC + `organization_member` model: [ADR 0001](./adr/0001-resident-onboarding.md)
- Inventory (`tower_gates`, `units`, `towers`): [project-setup-flow.md](./project-setup-flow.md)
