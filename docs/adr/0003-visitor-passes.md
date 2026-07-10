# ADR 0003: Visitor passes — schema and backend model

|                  |                                                                                                                                    |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Proposed                                                                                                                           |
| **Date**         | 2026-07-09                                                                                                                         |
| **Authors**      | Home Craft platform team                                                                                                           |
| **Depends on**   | [ADR 0001](./0001-resident-onboarding.md) (contacts + junction tables), [ADR 0002](./0002-resident-onboarding-implementation.md)   |
| **Related docs** | [passes-flow.md](../passes-flow.md) (flow & change guide), [contact-onboarding-flow.md](../contact-onboarding-flow.md)             |
| **Migrations**   | `2026XXXXXXXXXX_visitor_passes_enums.sql`, `2026XXXXXXXXXX_visitor_passes_tables.sql` (to be created in `ats-home-craft-supabase`) |

______________________________________________________________________

## Context

A household contact (resident) who has completed onboarding lands with **active `contact_units`**
(one or more units they own/rent). The next feature is **Visitor Passes**: from the mobile app a
resident creates a **pass** for a guest (visitor, delivery, cab, daily help, etc.), which produces
a **QR code + shareable pass image / link**. The guest shows the QR at the community gate; security
scans it to **check the guest in and out**. The resident sees a **pass list** (upcoming / active /
expired) and a **timeline** of entry/exit events per pass.

This mirrors the two existing flows and must fit the same conventions:

- **Project Setup (admin)** builds inventory: `projects`, `towers`, `tower_gates`, `units`.
- **Contact Onboarding (mobile)** links a resident (`contacts`) to `units` via `contact_units`.
- **Visitor Passes (mobile)** — this ADR — lets a linked resident issue guest passes against a unit.

Constraints (same as ADR 0001):

- Multi-tenancy via **`organization_id`** on every new table.
- Residents are **`contacts`**, not `organization_members`. The acting contact is resolved from the
  JWT via `extract_onboarding_contact_context()` — **no `*_MANAGEMENT_*` RBAC codes** for the
  resident-facing routes; authorization is "you can only act on your own units / passes".
- Reuse existing inventory (`units`, `tower_gates`) — do not duplicate it.
- The **guest is not a `contacts` row**. The guest is picked from the **device's phone contacts**
  (client-side); the backend only stores a **name + phone snapshot** on the pass.
- RLS is enabled on new tables but **policies are deferred** (backend uses `service_role`), matching
  the earlier phases.
- The pass identity for the gate is a **4-digit code** (also encoded in the QR). There is **no share
  link, no token, and no SMS** — the resident shares the **generated pass image** manually.
- Media (pass image) stores **path only** — no raw blobs.

______________________________________________________________________

## Decision

### 1. One core table `passes` + one event table `pass_events`

| Table             | Purpose                                                                                      |
| ----------------- | -------------------------------------------------------------------------------------------- |
| **`passes`**      | A single visitor pass: host contact, unit, guest snapshot, validity window, status, QR token |
| **`pass_events`** | Append-only timeline for a pass: created / shared / checked_in / checked_out / cancelled …   |

Both carry **`organization_id NOT NULL`**. `passes` also carries **`project_id`** (denormalized from
`units`, exactly like `contact_units` / `vehicles`).

> **Why two tables and not more:** the "timeline" and gate check-in/out are inherently
> append-only events, so they get their own table. Everything else about a pass is a single row.

### 2. Guest is a name + phone snapshot only — no guest `contacts` row

A pass stores the guest as a plain **snapshot** (`guest_name`, `guest_phone_isd_code`,
`guest_phone_number`, `visitor_count`, `vehicle_number`). The **"Select Guest"** screen is powered by
the **device's phone contacts** (client-side): the app lets the resident pick a contact or type a
name/phone, then sends those values in `POST /passes`.

The backend therefore:

- **does not** create a `contacts` row for the guest,
- **does not** store a `guest_contact_id`,
- has **no** guest list / "frequent guests" endpoint.

This keeps the guest strictly a value on the pass and the schema minimal.

### 2b. "Make it private" on create (and edit)

The Create Pass screen includes a **"Make it private"** toggle. When `is_private = true`, the guest
may still enter normally at the gate, but **other household members must not be notified** on
check-in (silent entry). The flag is stored on `passes.is_private` (`boolean NOT NULL DEFAULT false`).
Notification suppression is enforced in the **gate/check-in phase** (follow-up) — the resident API
only persists and returns the flag on create, get, list, and patch.

### 3. Validity + entry model on the `passes` row

| Concept               | Columns                                                                          |
| --------------------- | -------------------------------------------------------------------------------- |
| Window                | `valid_from timestamptz`, `valid_until timestamptz` (`valid_until > valid_from`) |
| One-time vs recurring | `validity_type pass_validity_type` (`one_time` / `recurring`)                    |
| Multi-entry           | `allow_multiple_entries bool`, `max_entries int NULL`, `entry_count int`         |

**Persisted `status`** (`pass_status`): `active`, `completed`, `expired`, `cancelled`. The UI buckets
(**upcoming / active / expired**) are **derived** in the service from `status` + validity window +
`now()` — not stored — so we never run a cron to flip `active → expired`. A nightly sweep to persist
`expired`/`completed` is a possible optimization (follow-up), not required for correctness.

### 4. QR is a 4-digit code; sharing is an image (no link/token/SMS)

Each pass gets a **4-digit numeric `code`** (e.g. `4821`). The **QR encodes exactly this code**, and
the gate identifies a pass by looking up the `code` (scoped to the organization). There is **no
opaque token, no share URL, and no SMS**.

Because 4 digits is a small space (10 000 values), `code` is **unique per org among passes that are
still usable** — i.e. a partial unique index over `passes` that are not `expired`/`cancelled`/
`completed`. Codes are freed for reuse once a pass leaves the usable set. Generation retries on
collision.

The resident shares the pass by generating a **pass image** (QR + guest/unit/validity details) which
they send however they like (screenshot, chat app, etc.). Only the image **path** is stored
(`pass_image_path`) — no blob, matching the media convention.

### 5. Gate check-in / check-out is a distinct, bounded concern

Security-side verification (scan QR → validate → check-in → check-out) is modeled as `pass_events`
rows and a separate service. It is **not** part of the resident's contact-scoped routes:

- Resident routes: contact context, prefix `/v1/passes` (create / list / detail / cancel).
- Gate routes: an **org-member** (security) endpoint guarded by a new RBAC code (see below), which
  resolves a pass by its **4-digit `code`**. Phase 2.

### 6. Enums (mirror Postgres, `str, Enum` in `app/schemas/enums.py`)

```python
class PassType(str, Enum):
    GUEST = "guest"
    DELIVERY = "delivery"
    CAB = "cab"
    SERVICE = "service"      # daily help / maintenance
    OTHER = "other"

class PassValidityType(str, Enum):
    ONE_TIME = "one_time"
    RECURRING = "recurring"

class PassStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

class PassEventType(str, Enum):
    CREATED = "created"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    EXTENDED = "extended"

class PassActorType(str, Enum):
    RESIDENT = "resident"   # the host contact (created / cancelled)
    STAFF = "staff"         # gate / security org member (checked_in / checked_out)
    SYSTEM = "system"       # automated (e.g. auto-expiry)

# Derived-only (API response), not stored:
class PassDisplayStatus(str, Enum):
    UPCOMING = "upcoming"
    ACTIVE = "active"
    EXPIRED = "expired"
    USED = "used"
    CANCELLED = "cancelled"
```

______________________________________________________________________

## New tables (what's needed)

> DDL below is the **intended shape** for the Supabase migrations. Column names/types follow the
> conventions of `contact_units` and `vehicles`.

### `pass_type`, `pass_validity_type`, `pass_status`, `pass_event_type` enums

```sql
CREATE TYPE pass_type AS ENUM ('guest', 'delivery', 'cab', 'service', 'other');
CREATE TYPE pass_validity_type AS ENUM ('one_time', 'recurring');
CREATE TYPE pass_status AS ENUM ('active', 'completed', 'expired', 'cancelled');
CREATE TYPE pass_event_type AS ENUM (
    'created', 'checked_in', 'checked_out', 'cancelled', 'expired', 'extended'
);
CREATE TYPE pass_actor_type AS ENUM ('resident', 'staff', 'system');
```

### `passes`

| Column                   | Type                          | Notes                                                           |
| ------------------------ | ----------------------------- | --------------------------------------------------------------- |
| `id`                     | `uuid` PK                     | `gen_random_uuid()`                                             |
| `organization_id`        | `uuid NOT NULL`               | tenant scope                                                    |
| `project_id`             | `uuid NOT NULL`               | denormalized from `units` (like `contact_units`)                |
| `unit_id`                | `uuid NOT NULL`               | FK `units` — the unit being visited                             |
| `host_contact_id`        | `uuid NOT NULL`               | FK `contacts` — resident who owns/created the pass              |
| `pass_type`              | `pass_type NOT NULL`          | default `'guest'`                                               |
| `guest_name`             | `text NOT NULL`               | snapshot (from device phone contacts / typed)                   |
| `guest_phone_isd_code`   | `text NULL`                   | snapshot                                                        |
| `guest_phone_number`     | `text NULL`                   | snapshot                                                        |
| `visitor_count`          | `int NOT NULL`                | default `1` (group passes)                                      |
| `vehicle_number`         | `text NULL`                   |                                                                 |
| `purpose`                | `text NULL`                   |                                                                 |
| `valid_from`             | `timestamptz NOT NULL`        |                                                                 |
| `valid_until`            | `timestamptz NOT NULL`        | `CHECK (valid_until > valid_from)`                              |
| `validity_type`          | `pass_validity_type NOT NULL` | default `'one_time'`                                            |
| `allow_multiple_entries` | `boolean NOT NULL`            | default `false`                                                 |
| `is_private`             | `boolean NOT NULL`            | default `false` — silent entry; no household notify on check-in |
| `max_entries`            | `int NULL`                    | cap for multi-entry passes                                      |
| `entry_count`            | `int NOT NULL`                | default `0` — incremented on check-in                           |
| `status`                 | `pass_status NOT NULL`        | default `'active'`                                              |
| `code`                   | `text NOT NULL`               | **4-digit** numeric code; encoded in the QR; gate lookup key    |
| `pass_image_path`        | `text NULL`                   | generated pass image, path only, no blob                        |
| `notes`                  | `text NULL`                   |                                                                 |
| `created_by_contact_id`  | `uuid NOT NULL`               | usually = `host_contact_id`                                     |
| `created_at`             | `timestamptz NOT NULL`        | `now()`                                                         |
| `updated_at`             | `timestamptz NOT NULL`        | `now()`                                                         |

Suggested indexes: `(organization_id, host_contact_id, status)`, `(organization_id, unit_id)`,
`(organization_id, valid_from, valid_until)`, and a **partial unique** index on
`(organization_id, code)` `WHERE status = 'active'` so a 4-digit code is unique only among usable
passes and can be reused after a pass expires/cancels/completes.

### `pass_events`

| Column            | Type                       | Notes                                                |
| ----------------- | -------------------------- | ---------------------------------------------------- |
| `id`              | `uuid` PK                  | `gen_random_uuid()`                                  |
| `organization_id` | `uuid NOT NULL`            | tenant scope                                         |
| `pass_id`         | `uuid NOT NULL`            | FK `passes` `ON DELETE CASCADE`                      |
| `event_type`      | `pass_event_type NOT NULL` |                                                      |
| `gate_id`         | `uuid NULL`                | FK `tower_gates` — where the scan happened           |
| `actor_type`      | `pass_actor_type NULL`     | who acted: `resident` / `staff` / `system`           |
| `actor_user_id`   | `uuid NULL`                | `auth.users.id` of the actor (see note below)        |
| `actor_label`     | `text NULL`                | free-text (e.g. "Gate 2 Guard") for `system`/no user |
| `occurred_at`     | `timestamptz NOT NULL`     | default `now()`                                      |
| `notes`           | `text NULL`                |                                                      |
| `metadata`        | `jsonb NOT NULL`           | default `'{}'`                                       |
| `created_at`      | `timestamptz NOT NULL`     | `now()`                                              |

Suggested index: `(organization_id, pass_id, occurred_at)`.

> **Why `actor_user_id` (not `actor_contact_id`).** The actor differs by event: `created`/`cancelled`
> are done by the **resident** (a `contacts` row), while `checked_in`/`checked_out` are done by
> **staff/security** (an `organization_member`, per ADR 0001 "staff RBAC only"). Both a contact
> (`contacts.user_id`) and a member map to **`auth.users.id`**, so a single `actor_user_id` covers
> **both** without two competing person-FKs. `actor_type` disambiguates for display and for choosing
> which table to resolve the name from; `NULL` + `actor_type='system'` covers automated events (e.g.
> auto-expiry). When the future staff/security flow lands, gate events simply set
> `actor_type='staff'` — no schema change needed.

### Tables reused (not created)

| Table           | Used for                                            |
| --------------- | --------------------------------------------------- |
| `contacts`      | host resident only (the guest is **not** a contact) |
| `contact_units` | verify the host actively owns the pass's `unit_id`  |
| `units`         | unit display + `project_id` derivation              |
| `tower_gates`   | `pass_events.gate_id` (where a scan occurred)       |

______________________________________________________________________

## Consequences

### Positive

- **Minimal schema surface** — 2 new tables + 4 enums; reuses `contacts`, `contact_units`, `units`,
  `tower_gates`; **no guest contacts created**.
- **Consistent patterns** — org scope, denormalized `project_id`, deferred RLS, and contact-context
  auth all match the onboarding flow.
- **Self-contained passes** — guest snapshot lives on the pass; nothing else to keep in sync.
- **Simple gate identity** — a 4-digit code the guard can also key in by hand if the QR won't scan.
- **Auditable timeline** — `pass_events` gives entry/exit history and powers the UI timeline for free.
- **No cron required** — display status derived from validity window + `status`.

### Negative / trade-offs

- **Denormalization** — `organization_id`/`project_id` on `passes` must be kept consistent with the
  parent `units` row (backend-enforced, no trigger; same trade-off as `contact_units`).
- **Small code space** — 4 digits = 10 000 codes; the partial unique index limits uniqueness to
  *usable* passes, and generation must retry on collision. A busy community could exhaust codes if
  many passes are simultaneously active (acceptable at expected volumes; revisit if it bites).
- **No revocable token / link** — sharing is a manual image; a leaked 4-digit code is guessable, so
  the gate must still enforce the validity window and one-time/max-entry limits.
- **Gate side deferred** — security scanning/RBAC (`VISITOR_MANAGEMENT_*`) is a separate phase.
- **No RLS policies yet** — access is backend-only until a follow-up migration.

### Follow-ups

1. **Implementation** — see [passes-flow.md](../passes-flow.md) (endpoints, services, phases).
1. Add RLS policies keyed on `organization_id` and `contacts.user_id` (host) + gate role.
1. Gate/security app endpoints + `VISITOR_MANAGEMENT_*` RBAC codes and a scanning device model.
1. Optional nightly sweep to persist `expired`/`completed` for reporting.
1. Optional Kafka event `passes.created` / `passes.checked_in` for notifications.
1. Where/how the pass image is generated + stored (client-generated vs server-rendered).

______________________________________________________________________

## Alternatives considered

| Alternative                                        | Why rejected                                                                |
| -------------------------------------------------- | --------------------------------------------------------------------------- |
| Create a `Guest` **`contacts`** row per pass       | Not needed — guest comes from the device phone contacts; snapshot is enough |
| New **`guests`** / `visitors` table                | Same reason; guest is a value on the pass, not an entity                    |
| Store entry/exit as columns on `passes`            | Loses history; a recurring pass has many entries — needs an events table    |
| Persist `upcoming`/`expired` via cron              | Extra moving part; deriving from the validity window is simpler and correct |
| Opaque **token + share link + SMS** (like invites) | Product wants image + 4-digit QR only; no link/token/SMS to build or secure |
| Put gate check-in on resident (contact) routes     | Conflates resident scope with security scope; gate is org/staff             |

______________________________________________________________________

## References

- Flow & change guide: [`passes-flow.md`](../passes-flow.md)
- Person model + junction-table decision: [ADR 0001](./0001-resident-onboarding.md)
- Unit-ownership check reused: `ContactUnitsRepository.contact_has_active_unit`
- Inventory reused: `units`, `tower_gates` (see `docs/project-setup-flow.md`)
