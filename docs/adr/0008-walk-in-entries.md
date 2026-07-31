# ADR 0008: Walk-in entries — security request, resident approval

|                  |                                                                                                                                                                                              |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Accepted (Phase 1)                                                                                                                                                                           |
| **Date**         | 2026-07-27                                                                                                                                                                                   |
| **Authors**      | Home Craft platform team                                                                                                                                                                     |
| **Depends on**   | [ADR 0001](./0001-resident-onboarding.md) (`contacts`, `contact_units`), [ADR 0003](./0003-visitor-passes.md), [ADR 0004](./0004-pass-validation-gate.md)                                    |
| **Related docs** | [walk-in-flow.md](../walk-in-flow.md), [passes-flow.md](../passes-flow.md), [passes-validation-flow.md](../passes-validation-flow.md), [tenant-requests-flow.md](../tenant-requests-flow.md) |
| **Migrations**   | `20260727120000_walk_in_enums.sql`, `20260727121000_walk_in_tables.sql` (to be created in `ats-home-craft-supabase`)                                                                         |

______________________________________________________________________

## Context

[ADR 0003](./0003-visitor-passes.md) covers **resident-issued visitor passes**: a household contact
creates a pass with a **4-digit QR code**; security **verifies the code** at the gate and records
check-in / check-out ([ADR 0004](./0004-pass-validation-gate.md)).

The mobile **Walk-in** flow (security app) covers the opposite case: a guest arrives **without a
pre-created pass**. A **security guard** (`organization_member` with `visitor_management.verify`)
registers the visitor at the gate. **Residents on the target flat(s)** may **approve or reject**
their portion of the visit before the guest is admitted.

A single visit may target **multiple flats** (e.g. a courier delivering to 3–4 units). The product
requires **one walk-in record** in the security list (with flat count), **per-flat approval**, and
**one enter / one exit** for the whole visit.

### Product decisions (confirmed)

| #   | Decision                                                                                                                                            |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Gate entry:** security verification (create with photos/details) **and at least one flat approved**. Not all flats must approve.                  |
| 2   | **Partial approval:** if 2 of 4 flats approve, the visitor may enter for those approved flats only; rejected flats are skipped.                     |
| 3   | **Security list:** one card per visit; show **number of flats** (`flats_count`). Detail shows all visit units.                                      |
| 4   | **Exit:** **one exit** for the whole visit (header-level), not per flat.                                                                            |
| 5   | **API naming:** no `batch` endpoints — use a single `POST /walk-ins` with a **`flats[]`** array. Each item includes **`tower_id`** + **`unit_id`**. |

### Screens (product)

**Security mobile — Create Walk-in**

| Field            | Required | Notes                                   |
| ---------------- | -------- | --------------------------------------- |
| Visitor photo(s) | Yes      | At least one image (storage path)       |
| First name       | Yes      |                                         |
| Last name        | No       |                                         |
| Phone number     | Yes      | ISD + number                            |
| Flats            | Yes      | **≥1** row: `tower_id` + `unit_id` each |
| Notes            | No       | e.g. "Swish Delivery Parcel"            |
| Vehicle photo    | No       | Optional storage path                   |

**Security mobile — Walk-in list**

| Element    | Meaning                                      |
| ---------- | -------------------------------------------- |
| One card   | One visit (may cover multiple flats)         |
| Flat count | e.g. "3 flats" on the card                   |
| Awaiting   | Created; no flat approved yet                |
| Approved   | ≥1 flat approved; not yet physically entered |
| Entered    | Security marked guest inside                 |
| Exit       | Security marked guest left (terminal)        |

**Pass Details**

| State    | Header status | Timeline (minimum)           | Primary action  |
| -------- | ------------- | ---------------------------- | --------------- |
| Waiting  | `awaiting`    | Entry requested              | Call (client)   |
| Approved | `approved`    | + visit unit approved events | Mark as entered |
| Entered  | `entered`     | + User entered               | Mark as exit    |
| Exit     | `exited`      | + Exit                       | —               |

Detail includes **`visit_units[]`** — one row per flat with its own approval status.

**Resident mobile (Owner / Family / Tenant on a visit unit's flat)**

| Action  | Capability                               |
| ------- | ---------------------------------------- |
| Approve | Approve **their flat's visit unit** only |
| Reject  | Reject **their flat's visit unit** only  |

### Constraints (carried from existing flows)

- Multi-tenancy via **`organization_id`** on every new table and query.
- **Security actor** = `organization_member` via `check_permissions(visitor_management.verify)`.
- **Resident actor** = `contacts` via `extract_onboarding_contact_context()`.
- Reuse **`units`**, **`towers`**, **`projects`** — validate `unit.tower_id = visit_unit.tower_id`.
- Photos store **storage paths only** — no blobs in Postgres.
- RLS enabled, **policies deferred** (backend `service_role`).
- **Project-scoped security APIs:** `/v1/projects/{project_id}/walk-ins/*`.

______________________________________________________________________

## Decision

### 1. Separate tables — do not overload `passes`

Walk-in entries are **not** resident-created passes (see ADR 0003 comparison in prior draft). A
dedicated domain keeps gate verify and resident pass logic unchanged.

### 2. Three tables — visit header, visit units, event log

| Table                     | Purpose                                                                         |
| ------------------------- | ------------------------------------------------------------------------------- |
| **`walk_in_entries`**     | One visit: visitor snapshot, **header status**, enter/exit timestamps           |
| **`walk_in_visit_units`** | One row per flat: `tower_id`, `unit_id`, **per-unit approval status**           |
| **`walk_in_events`**      | Append-only timeline (requested, visit unit approved/rejected, entered, exited) |

```text
walk_in_entries (visitor, header status, entered_at, exited_at)
  ├── walk_in_visit_units → Tower A / A-2102  awaiting
  ├── walk_in_visit_units → Tower B / B-1204  approved
  └── walk_in_visit_units → Tower C / C-805   rejected
```

> **Why visit units table:** approval is **per flat**. A courier visit is **one card** in the security UI
> but three independent resident decisions. Rejected visit units do not block entry for approved ones.

> **Why not separate entry rows per flat:** the product shows **one visit**, **one exit**, and
> **flat count** on the list — that is a header + children model, not N duplicate headers.

### 3. Status lifecycle

#### Header — `walk_in_status`

| Status      | Meaning                                               |
| ----------- | ----------------------------------------------------- |
| `awaiting`  | Created; **no visit unit approved yet**               |
| `approved`  | **≥1 visit unit approved**; security may mark entered |
| `entered`   | Security marked visitor physically inside             |
| `exited`    | Security marked visitor left (**terminal**)           |
| `cancelled` | Security cancelled before entry (optional)            |

Header **`rejected`** is **not** used — if every visit unit is rejected the header stays `awaiting` and
enter is blocked.

```text
security POST (1+ flats) ──► awaiting
                                │
              any visit unit approved ──► approved ──security enter──► entered ──security exit──► exited
              all visit units rejected ──► (stays awaiting; cannot enter)
              security cancel      ──► cancelled
```

**Enter rule (product #1 + #2):**

- Security may `POST .../enter` only when header is `awaiting` or `approved` **and**
  **≥1 visit unit has status `approved`**.
- Security create (photos, identity check) is always required first — that is the "security
  verification" step.
- **Not all flats** need to approve; **at least one** is enough to unlock enter.

**Exit rule (product #4):**

- Single `POST .../exit` on the **entry** (`walk_in_entries.id`) when header is `entered`.

#### Visit unit — `walk_in_visit_unit_status`

| Status     | Meaning                     |
| ---------- | --------------------------- |
| `awaiting` | Resident has not acted      |
| `approved` | Resident approved this flat |
| `rejected` | Resident rejected this flat |

Visit unit transitions: `awaiting` → `approved` | `rejected` (terminal for that flat).

When a visit unit is approved/rejected, service **recomputes header status**:

```text
any visit unit approved     → header approved (if not already entered/exited)
zero visit units approved
  and all visit units rejected → header stays awaiting (enter blocked)
```

### 4. Visitor is a snapshot — no guest `contacts` row

Store visitor fields on **`walk_in_entries`** only. Visit units carry flat targeting only.

### 5. Who may approve a visit unit

Resident may act only on visit units where they have an **active `contact_units`** link to
`visit_unit.unit_id` (any relationship; **`contact_roles` is not checked**).

Record `approved_by_contact_id` / `rejected_by_contact_id` on the **visit unit** row.

### 6. RBAC and API split

| Actor    | Prefix                               | Auth                                   |
| -------- | ------------------------------------ | -------------------------------------- |
| Security | `/v1/projects/{project_id}/walk-ins` | `visitor_management.verify`            |
| Resident | `/v1/walk-ins`                       | `extract_onboarding_contact_context()` |

Resident approve/reject targets a **visit unit**:

```http
POST /v1/walk-ins/{entry_id}/visit-units/{visit_unit_id}/approve
POST /v1/walk-ins/{entry_id}/visit-units/{visit_unit_id}/reject
```

Resident list returns visit units for the contact's active flats (all statuses by default; optional
`?status=` filter). May include parent entry summary for context.

______________________________________________________________________

## Schema (proposed)

### Enums

```sql
CREATE TYPE public.walk_in_status AS ENUM (
  'awaiting',
  'approved',
  'entered',
  'exited',
  'cancelled'
);

CREATE TYPE public.walk_in_visit_unit_status AS ENUM (
  'awaiting',
  'approved',
  'rejected'
);

CREATE TYPE public.walk_in_event_type AS ENUM (
  'requested',
  'visit_unit_approved',
  'visit_unit_rejected',
  'entered',
  'exited',
  'cancelled'
);

CREATE TYPE public.walk_in_actor_type AS ENUM (
  'staff',
  'resident',
  'system'
);
```

### `walk_in_entries` (visit header — no `unit_id`)

| Column                      | Type                    | Notes                                           |
| --------------------------- | ----------------------- | ----------------------------------------------- |
| `id`                        | uuid PK                 |                                                 |
| `organization_id`           | uuid FK → organizations |                                                 |
| `project_id`                | uuid FK → projects      |                                                 |
| `visitor_first_name`        | text NOT NULL           |                                                 |
| `visitor_last_name`         | text                    |                                                 |
| `visitor_phone_isd_code`    | text NOT NULL           |                                                 |
| `visitor_phone_number`      | text NOT NULL           |                                                 |
| `visitor_photo_paths`       | text[] NOT NULL         | CHECK `cardinality > 0`                         |
| `vehicle_photo_paths`       | text[]                  | optional                                        |
| `notes`                     | text                    |                                                 |
| `status`                    | walk_in_status NOT NULL | default `awaiting`                              |
| `flats_count`               | integer NOT NULL        | denormalized count of visit units; CHECK `>= 1` |
| `approved_flats_count`      | integer NOT NULL        | default 0; maintained on visit unit approve     |
| `requested_by_user_id`      | uuid FK → auth.users    | security staff                                  |
| `gate_id`                   | uuid FK → tower_gates   | optional                                        |
| `requested_at`              | timestamptz NOT NULL    | default `now()`                                 |
| `entered_at`                | timestamptz             | set on enter                                    |
| `exited_at`                 | timestamptz             | set on exit                                     |
| `created_at` / `updated_at` | timestamptz             |                                                 |

### `walk_in_visit_units` (one row per flat)

| Column                      | Type                      | Notes                                 |
| --------------------------- | ------------------------- | ------------------------------------- |
| `id`                        | uuid PK                   |                                       |
| `organization_id`           | uuid FK                   |                                       |
| `walk_in_entry_id`          | uuid FK → walk_in_entries | ON DELETE CASCADE                     |
| `tower_id`                  | uuid FK → towers          | required                              |
| `unit_id`                   | uuid FK → units           | required; must match `units.tower_id` |
| `status`                    | walk_in_visit_unit_status | default `awaiting`                    |
| `approved_by_contact_id`    | uuid FK → contacts        |                                       |
| `rejected_by_contact_id`    | uuid FK → contacts        |                                       |
| `rejection_reason`          | text                      |                                       |
| `approved_at`               | timestamptz               |                                       |
| `rejected_at`               | timestamptz               |                                       |
| `sort_order`                | integer NOT NULL          | default 0; UI order                   |
| `created_at` / `updated_at` | timestamptz               |                                       |

```sql
CREATE UNIQUE INDEX uq_walk_in_visit_units_entry_unit
  ON walk_in_visit_units (walk_in_entry_id, unit_id);

CREATE INDEX idx_walk_in_visit_units_org_unit_status
  ON walk_in_visit_units (organization_id, unit_id, status);
```

### `walk_in_events`

| Column                  | Type                          | Notes                               |
| ----------------------- | ----------------------------- | ----------------------------------- |
| `id`                    | uuid PK                       |                                     |
| `organization_id`       | uuid FK                       |                                     |
| `walk_in_entry_id`      | uuid FK                       | ON DELETE CASCADE                   |
| `walk_in_visit_unit_id` | uuid FK → walk_in_visit_units | NULL for entry-level events         |
| `event_type`            | walk_in_event_type            |                                     |
| `actor_type`            | walk_in_actor_type            |                                     |
| `actor_user_id`         | uuid                          | staff                               |
| `actor_contact_id`      | uuid FK → contacts            | resident                            |
| `actor_label`           | text                          |                                     |
| `occurred_at`           | timestamptz NOT NULL          |                                     |
| `payload`               | jsonb NOT NULL DEFAULT `'{}'` | tower_id, unit_id, rejection_reason |
| `created_at`            | timestamptz NOT NULL          |                                     |

### Migration files

| File                                | Contents                          |
| ----------------------------------- | --------------------------------- |
| `20260727120000_walk_in_enums.sql`  | Four enums above                  |
| `20260727121000_walk_in_tables.sql` | Three tables, indexes, RLS enable |

**No changes** to `passes`, `pass_events`, `contacts`, or `contact_units` in Phase 1.

______________________________________________________________________

## Consequences

### Positive

- Matches multi-flat delivery UX without duplicate visitor data entry.
- Per-flat approval with partial entry support.
- Single enter/exit matches gate operations (one person, one visit).
- List API exposes `flats_count` for grouped cards natively.

### Negative / trade-offs

- More complex than single-unit model (three tables, header recompute).
- Resident UX lists **visit units**, not whole entries — API must join entry summary on list.
- Visitor Logs union work still deferred.

### Follow-ups

1. Push notification per visit unit on `awaiting`.
1. Visitor Logs UNION with passes.
1. Optional `purpose` enum (`guest` / `delivery`) for analytics filters.

______________________________________________________________________

## Alternatives considered

| Alternative                     | Why rejected                                            |
| ------------------------------- | ------------------------------------------------------- |
| One `unit_id` on header only    | Cannot model multi-flat delivery in one visit           |
| Separate header row per flat    | Breaks one-card list + one-exit product requirement     |
| `POST /walk-ins/batch` API      | Product asked for single create endpoint with `flats[]` |
| All flats must approve to enter | Conflicts with product decision #1 and #2               |
| Per-flat exit                   | Conflicts with product decision #4                      |
