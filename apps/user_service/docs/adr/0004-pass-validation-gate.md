# ADR 0004: Pass validation — gate check-in/out and visitor logs

|                  |                                                                                                                                             |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Proposed                                                                                                                                    |
| **Date**         | 2026-07-17                                                                                                                                  |
| **Authors**      | Home Craft platform team                                                                                                                    |
| **Depends on**   | [ADR 0003](./0003-visitor-passes.md) (`passes` + `pass_events`), [ADR 0001](./0001-resident-onboarding.md) (staff = `organization_members`) |
| **Related docs** | [passes-validation-flow.md](../passes-validation-flow.md) (build guide), [passes-flow.md](../passes-flow.md) (resident side)                |
| **Migrations**   | `20260717120000_visitor_gate_enums.sql`, `20260717121000_visitor_gate_columns.sql` (to be created in `ats-home-craft-supabase`)             |

______________________________________________________________________

## Context

ADR 0003 built the **resident side** of Visitor Passes: a household contact creates a pass for a
guest, producing a **4-digit `code`** (rendered as a QR) and a timeline (`pass_events`). ADR 0003
explicitly **deferred the gate/security side** ("Phase 2") but designed the schema for it:
`pass_events` already carries `gate_id`, `actor_type` (`resident` / `staff` / `system`),
`actor_user_id`, and `actor_label`.

This ADR covers that deferred half — the **pass validation flow** — driven by the **Visitor Logs**
screen (community-admin view) and the gate device used by security staff:

- **Gate (security guard):** scan the QR / key the 4-digit `code` → **verify** the pass → record
  **check-in** (entry) and later **check-out** (exit).
- **Admin (community admin):** a **Visitor Logs** dashboard — a filterable table of visits with
  overview cards (Total Visitors / IN / Deliveries / Daily Help), plus exports (entry/exit details,
  monthly report).

### Screen → capability map (from the "Visitor Logs" board)

| Screen element                                  | Capability                                                                  |
| ----------------------------------------------- | --------------------------------------------------------------------------- |
| Overview cards (Total Visitors / IN / …)        | `GET /v1/visitor-logs/overview` (org + month aggregates)                    |
| Filters (type / entry / access / tower / month) | `GET /v1/visitor-logs` query params                                         |
| Table row (type, flat, created by, scheduled …) | `GET /v1/visitor-logs` — joins `passes` + latest check-in/out events        |
| Entry Type column (QR / Pass)                   | `pass_events.entry_method` (`qr` / `code` / `manual`) on the check-in event |
| Guard column                                    | `pass_events.actor_label` / `actor_user_id` (staff)                         |
| Access Status (Approved / Granted / Expired)    | `pass_events.access_status` on the check-in event                           |
| IN Time / OUT Time / Time Spent                 | derived from `checked_in` / `checked_out` event `occurred_at`               |
| Details (eye)                                   | `GET /v1/visitor-logs/{pass_id}` (pass + full timeline)                     |
| Entry/Exit details (download)                   | `GET /v1/visitor-logs/export`                                               |
| Monthly report                                  | `GET /v1/visitor-logs/monthly-report`                                       |

### Constraints (carried from ADR 0001 / 0003)

- Multi-tenancy via **`organization_id`** on every query.
- Gate + admin actors are **`organization_members`** (staff), **not** `contacts`. So — unlike the
  resident routes — these routes **do** use RBAC codes (`visitor_management.*`) via
  `check_permissions()`, resolving an `organization_member` context.
- Reuse existing tables — **no new core tables**. Gate actions are `pass_events` rows.
- RLS enabled but **policies deferred** (backend uses `service_role`), matching earlier phases.
- The gate identifies a pass by its **4-digit `code`** (scoped to the org, usable passes only).

______________________________________________________________________

## Decision

### 1. No new core tables — gate actions are `pass_events` rows

Check-in and check-out are **append-only events** on the pass that ADR 0003 already provisioned.
A gate action inserts one `pass_events` row and updates a couple of counters on `passes`:

| Action    | `pass_events` row                                                                           | `passes` side effect                                   |
| --------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| check-in  | `event_type='checked_in'`, `actor_type='staff'`, `gate_id`, `entry_method`, `access_status` | `entry_count += 1`                                     |
| check-out | `event_type='checked_out'`, `actor_type='staff'`, `gate_id`                                 | `status='completed'` for one-time passes on final exit |

This keeps the "one visit → many entry/exit events" model (recurring passes) intact, gives the
Visitor Logs timeline for free, and needs **no schema restructuring** — only two small enum-typed
columns on `pass_events` (below).

### 2. Two new columns on `pass_events` (the only schema change)

The Visitor Logs table shows **Entry Type** and **Access Status** as first-class columns, so they
are promoted from `metadata` to typed columns for cheap filtering/aggregation:

| Column          | Type                      | Notes                                                            |
| --------------- | ------------------------- | ---------------------------------------------------------------- |
| `entry_method`  | `pass_entry_method NULL`  | how the guard admitted the guest: `qr` / `code` / `manual`       |
| `access_status` | `pass_access_status NULL` | decision at entry: `approved` / `granted` / `denied` / `expired` |

Both are `NULL` for non-entry events (`created`, `cancelled`, …) and set on `checked_in` rows.

### 3. `access_status` — the gate decision (Approved / Granted / Expired / Denied)

`verify` computes an outcome; `check-in` persists it on the event:

| `access_status` | Meaning                                                                             |
| --------------- | ----------------------------------------------------------------------------------- |
| `approved`      | Valid pre-created pass, inside its window → auto-admitted                           |
| `granted`       | Guard admitted after manual/tele approval (e.g. edge case, resident confirmed)      |
| `expired`       | Pass validity window had lapsed (surfaced in logs; entry blocked unless overridden) |
| `denied`        | Cancelled pass or one-time already used / `max_entries` reached                     |

The **display access status** in the log row is simply the `access_status` of the latest
`checked_in` event; a pass that was verified but refused entry records a `checked_in` row with
`access_status='denied'`/`expired` and `entry_count` unchanged (audit trail of refusals).

### 4. `verify` is a read; `check-in` / `check-out` are writes

- `POST /v1/passes/verify` — **read-only** lookup by 4-digit `code`; returns the pass + a computed
  `access_status` + guest/unit snapshot so the guard sees who is at the gate **before** admitting.
- `POST /v1/passes/{pass_id}/check-in` — records entry (event + `entry_count`).
- `POST /v1/passes/{pass_id}/check-out` — records exit (event + one-time `completed`).

Splitting verify (read) from check-in (write) matches the guard UX (scan → confirm → admit) and the
`db_conn` vs `db_uow` convention.

### 5. Gate + admin are staff routes with RBAC (not contact-scoped)

New permission codes (in `libs/shared_utils/common_query.py`, added to `DEFAULT_PERMISSIONS`):

| Code                        | Grants                                             |
| --------------------------- | -------------------------------------------------- |
| `visitor_management.view`   | View Visitor Logs, overview, pass details, exports |
| `visitor_management.verify` | Gate: `verify`, `check-in`, `check-out`            |

Routes use `check_permissions(current_user, db, permission_codes=...)` → `organization_member`
context (same pattern as `contacts` / `projects` admin routes). A guard role gets `verify` (+ `view`);
a community-admin role gets `view`. `actor_user_id` on the event is the staff member's `auth.users.id`;
`actor_label` stores the guard's display name for the logs table.

### 6. Visitor Logs is a query over `passes` ⋈ `pass_events`

No materialized "logs" table. `GET /v1/visitor-logs` joins `passes` to their **latest** `checked_in`
and `checked_out` events (lateral / window query) to produce each row:

```
row = pass (type, unit/flat, created_by, scheduled window)
    + latest checked_in  (in_time, guard, gate, entry_method, access_status)
    + latest checked_out (out_time)  → time_spent = out_time − in_time
```

Overview cards are `COUNT`/`SUM` aggregates over the same join for the selected month + org.
Exports reuse the list query and stream CSV/XLSX (path/stream only, no blob table).

### 7. Enums (mirror Postgres, `str, Enum` in `app/schemas/enums.py`)

```python
class PassEntryMethod(str, Enum):
    QR = "qr"        # guard scanned the QR (encodes the 4-digit code)
    CODE = "code"    # guard keyed the 4-digit code manually
    MANUAL = "manual"  # guard admitted without a code (walk-in / override)

class PassAccessStatus(str, Enum):
    APPROVED = "approved"  # valid pre-created pass, inside window
    GRANTED = "granted"    # guard/resident manual approval
    EXPIRED = "expired"    # validity window lapsed
    DENIED = "denied"      # cancelled / used / max-entries
```

`PassEventType` (ADR 0003) already includes `checked_in` / `checked_out`; `PassActorType` already
includes `staff`. No changes there.

### 8. Pass-type coverage for the logs filter (follow-up, optional)

The Visitor Logs "Type" column shows `Delivery`, `Guest`, `Daily Visitors`, `Vendor`,
`Society Staff`, while `pass_type` (ADR 0003) is `guest` / `delivery` / `cab` / `service` / `other`.
To make the overview cards (e.g. **Daily Help**) and the type filter first-class, extend `pass_type`
with `daily_help`, `vendor`, `staff` (additive enum change). Until then, `service` ≈ Daily Visitors
and `other` ≈ Vendor/Society Staff. This is called out as a follow-up, not required for the core
validate flow.

______________________________________________________________________

## Schema changes (what's needed)

### `pass_entry_method`, `pass_access_status` enums

```sql
CREATE TYPE public.pass_entry_method AS ENUM ('qr', 'code', 'manual');
CREATE TYPE public.pass_access_status AS ENUM ('approved', 'granted', 'expired', 'denied');
```

### `pass_events` — add two columns

```sql
ALTER TABLE public.pass_events
    ADD COLUMN entry_method  public.pass_entry_method,
    ADD COLUMN access_status public.pass_access_status;

-- Fast Visitor Logs filtering by entry outcome:
CREATE INDEX pass_events_org_access_status_idx
    ON public.pass_events (organization_id, access_status)
    WHERE event_type = 'checked_in';
```

### (Optional follow-up) extend `pass_type`

```sql
ALTER TYPE public.pass_type ADD VALUE IF NOT EXISTS 'daily_help';
ALTER TYPE public.pass_type ADD VALUE IF NOT EXISTS 'vendor';
ALTER TYPE public.pass_type ADD VALUE IF NOT EXISTS 'staff';
```

### Tables reused (not created)

| Table                  | Used for                                                  |
| ---------------------- | --------------------------------------------------------- |
| `passes`               | the pass being verified; `entry_count` / `status` updates |
| `pass_events`          | check-in / check-out timeline (+ new `entry_method` cols) |
| `tower_gates`          | `pass_events.gate_id` — where the scan happened           |
| `units`                | flat/tower display + tower filter in Visitor Logs         |
| `contacts`             | "Created By" (host resident) display                      |
| `organization_members` | guard/admin actor (RBAC + `actor_user_id`)                |

______________________________________________________________________

## Consequences

### Positive

- **Zero new tables** — the whole gate + logs feature rides on `passes` + `pass_events`; only two
  additive columns and two enums.
- **Consistent auth split** — resident routes stay contact-scoped; gate/admin routes use the same
  RBAC/`organization_member` pattern as every other staff feature.
- **Auditable** — refusals (`expired`/`denied`) are recorded as `checked_in` events, so the logs show
  attempted entries, not just successful ones.
- **Recurring passes work** — many entry/exit events per pass; time-spent and IN/OUT derive per event.
- **Cheap reporting** — overview cards and exports are aggregates over one indexed join; no cron, no
  denormalized log table to keep in sync.

### Negative / trade-offs

- **Latest-event query cost** — Visitor Logs must find the latest `checked_in`/`checked_out` per pass
  (lateral join / window). Indexed by `(organization_id, pass_id, occurred_at)` (ADR 0003) so it is
  bounded, but heavy months may want a summary table later.
- **`access_status`/`entry_method` nullable** — only meaningful on `checked_in` rows; consumers must
  ignore them elsewhere.
- **Type mismatch until enum extended** — logs "Type" filter is approximate until `pass_type` gains
  `daily_help` / `vendor` / `staff`.
- **No offline gate** — verify/check-in require connectivity; an offline scan queue is out of scope.
- **No RLS policies yet** — backend-only access until a follow-up migration.

### Follow-ups

1. **Implementation** — see [passes-validation-flow.md](../passes-validation-flow.md).
1. Extend `pass_type` (`daily_help` / `vendor` / `staff`) for exact card/filter parity.
1. RLS policies keyed on `organization_id` + gate/admin role.
1. Kafka events (`passes.checked_in` / `passes.checked_out`) for resident notifications
   (respecting `passes.is_private` — silent entry, no household notify).
1. Optional nightly sweep to persist `expired` for passes never used.
1. Gate device/session model (per-gate tokens) if guards use shared kiosks.

______________________________________________________________________

## Alternatives considered

| Alternative                                          | Why rejected                                                               |
| ---------------------------------------------------- | -------------------------------------------------------------------------- |
| New `visitor_logs` / `visits` table                  | Redundant — a "visit" is a check-in/out pair already in `pass_events`      |
| Store IN/OUT/guard as columns on `passes`            | Loses history; recurring passes have many entries — events are the source  |
| Put gate check-in on resident (contact) routes       | Conflates resident scope with security scope; gate is org/staff (ADR 0003) |
| `entry_method` / `access_status` in `metadata` jsonb | Screenshot shows them as filterable columns; typed columns index cheaply   |
| Materialized view for Visitor Logs                   | Premature; the indexed join is sufficient at expected volumes              |

______________________________________________________________________

## References

- Resident side & schema: [ADR 0003 — Visitor passes](./0003-visitor-passes.md)
- Build guide: [`passes-validation-flow.md`](../passes-validation-flow.md)
- Staff RBAC + `organization_member` model: [ADR 0001](./0001-resident-onboarding.md)
- Reused: `passes`, `pass_events`, `tower_gates`, `units`, `contacts`
