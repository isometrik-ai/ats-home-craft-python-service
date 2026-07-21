# ADR 0005: Move events — move-in / move-out records

|                  |                                                                                                                                  |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Accepted                                                                                                                         |
| **Date**         | 2026-07-20                                                                                                                       |
| **Authors**      | Home Craft platform team                                                                                                         |
| **Depends on**   | [ADR 0001](./0001-resident-onboarding.md) (contacts + `contact_units`), [ADR 0002](./0002-resident-onboarding-implementation.md) |
| **Related docs** | [move-events-flow.md](../move-events-flow.md) (build guide), [contact-onboarding-flow.md](../contact-onboarding-flow.md)         |
| **Migrations**   | `20260720150000_move_events_enums.sql`, `20260720151000_move_events_tables.sql`                                                  |

______________________________________________________________________

## Context

Project Setup builds inventory (`projects`, `towers`, `units`) and Contact Onboarding links a
resident (`contacts`) to `units` via `contact_units`. The next admin feature is **Move Events**: a
**community-admin** screen that records **move-in** and **move-out** events for units across
apartments, commercial units, and plots.

From the "Move Events" board each row is a single move record:

| Column      | Source                                                                            |
| ----------- | --------------------------------------------------------------------------------- |
| **Unit**    | `units.code` + tower/building name (`RP-0104 / Retail Plaza`, `A-0202 / Tower A`) |
| **Type**    | the unit's config kind (`Apartment` / `Commercial` / `Plot`)                      |
| **Contact** | the person moving (`contacts.display_name`) + their role (`Tenant` / `Owner`)     |
| **Status**  | the move **type** badge (`Move-In` / `Move-Out`)                                  |
| **Date**    | when the move happened (`event_date`)                                             |
| **Fee**     | the move charge (`fee_amount`, e.g. `₹5,000`)                                     |
| **Actions** | view details (eye)                                                                |

Top controls: an **All / Move-In / Move-Out** filter, a **search unit or contact** box, and a
**"+ Move In/Out"** button that opens the create form.

The same unit + contact can appear **multiple times** (e.g. `A-0101 / Arjun Babu` shows both a
Move-In on 13 May and a Move-Out on 08 May; `Vinod Bose` recurs across units). So this is an
**append-only ledger of move records**, not a single current-status value.

### Constraints (carried from ADR 0001 / 0003 / 0004)

- Multi-tenancy via **`organization_id`** on every new table and every query.
- The actor is a **community admin** — an `organization_member` (staff), **not** a `contacts` row.
  So — like the `projects` / `contacts` admin routes and unlike the resident-facing passes routes —
  these routes **use RBAC codes** (`contacts_management.*`) via `check_permissions()`.
- Reuse existing inventory + person tables (`units`, `contacts`, `contact_units`) — do not duplicate.
- The person who moves is an existing **`contacts`** row (Owner/Tenant), referenced by id — no snapshot.
- Media (inspection photos / documents) stores **paths only** — no raw blobs (same as `vehicles.photo_paths`).
- RLS enabled on the new table but **policies deferred** (backend uses `service_role`), matching earlier phases.

______________________________________________________________________

## Decision

### 1. One new table `move_events` (append-only ledger) — no second table

A move-in and a move-out are each a single, immutable-ish record. Everything the screen and the
detail view needs fits on one row, so we add **one** table `move_events` and reuse everything else.

| Table             | Purpose                                                                     |
| ----------------- | --------------------------------------------------------------------------- |
| **`move_events`** | One move-in or move-out record: unit, contact, type, date, fee, notes, docs |

It carries **`organization_id NOT NULL`** and **`project_id`** (denormalized from `units`, exactly
like `contact_units` / `vehicles` / `passes`).

> **Why not two tables (like `passes` + `pass_events`):** a pass has *many* entry/exit events over
> its life, so those needed a child table. A move event is itself the leaf record — there is no
> per-move sub-timeline — so a single ledger table is the right shape. Inspection photos/documents
> are stored as a `text[]` of paths on the row (like `vehicles.photo_paths`) rather than a child
> table.

### 2. `move_events` is the history; `contact_units` holds current occupancy

The two are complementary and kept consistent by the service (no DB trigger, same trade-off as
`contact_units`):

| Action recorded           | `move_events` row      | `contact_units` side effect (the link for that unit+contact)                                  |
| ------------------------- | ---------------------- | --------------------------------------------------------------------------------------------- |
| **move-in** (`move_in`)   | `move_type='move_in'`  | `status='active'`, `activated_at = COALESCE(activated_at, event_date)`, `moved_out_at = NULL` |
| **move-out** (`move_out`) | `move_type='move_out'` | `status='moved_out'`, `moved_out_at = event_date`                                             |

This finally exercises the **`ContactUnitStatus.MOVED_OUT`** value that ADR 0001 defined but no flow
uses yet. `contact_units` answers "who lives here **now**"; `move_events` answers "who moved in/out
**and when**, for how much". A move-out does **not** delete the `contact_units` row (kept for audit
and possible re-move-in).

### 3. Type / role / unit-type are read from existing tables — not snapshotted

- **Type** column (`Apartment` / `Commercial` / `Plot`) = the unit's config kind
  (`units.config_id → unit_configs.kind`, `UnitConfigKind`).
- **Contact role** (`Owner` / `Tenant`) = `contacts.contact_type` (or the `contact_units.relationship`).
- **Unit label** = `units.code` + tower/building name.

The list query **joins** these; nothing is copied onto `move_events` except the fee/date/notes that
are intrinsic to the move.

### 4. Fee is a stored amount on the move (no billing coupling yet)

Each move carries a **`fee_amount numeric(12,2)`** + **`fee_currency text DEFAULT 'INR'`**. Both
move-in and move-out can carry a fee (the screenshot shows fees on both). This is a **recorded
charge only** — it does **not** post to any ledger/billing module (none exists yet). A future
billing integration is a follow-up; for now the amount is informational and shown in the table.

### 5. `move_type` doubles as the "Status" badge — no separate status enum (for now)

The screen's **Status** column is literally the `Move-In` / `Move-Out` badge, i.e. the `move_type`.
We therefore do **not** add a lifecycle status (`scheduled` / `completed`) in Phase 1. If the product
later needs scheduled-vs-completed moves, add a `MoveEventStatus` enum (additive) — called out as a
follow-up.

### 6. Staff routes with RBAC (not contact-scoped)

Move Events reuses existing **Contacts** admin permissions (same Community admin persona):

| Code                         | Grants                                    |
| ---------------------------- | ----------------------------------------- |
| `contacts_management.view`   | List / detail move events                 |
| `contacts_management.create` | Record a move-in / move-out               |
| `contacts_management.edit`   | Correct a move event (date / fee / notes) |
| `contacts_management.delete` | Void / soft-delete a mistaken record      |

No separate `move_events_management.*` codes — admins who can manage contacts can manage moves.

Routes use `check_permissions(current_user, db, permission_codes=...)` → `organization_member`
context (same pattern as `contacts` / `projects` admin routes). `recorded_by_user_id` on the row is
the acting admin's `auth.users.id`.

### 7. Enums (mirror Postgres, `str, Enum` in `app/schemas/enums.py`)

```python
class MoveEventType(str, Enum):
    MOVE_IN = "move_in"
    MOVE_OUT = "move_out"

# List filter buckets for GET /move-events (All is "no filter"):
class MoveEventListBucket(str, Enum):
    MOVE_IN = "move_in"
    MOVE_OUT = "move_out"
```

______________________________________________________________________

## New tables (what's needed)

> DDL below is the **intended shape** for the Supabase migrations. Column names/types follow the
> conventions of `contact_units`, `vehicles`, and `passes`.

### `move_event_type` enum

```sql
CREATE TYPE public.move_event_type AS ENUM ('move_in', 'move_out');
```

### `move_events`

| Column                | Type                       | Notes                                                       |
| --------------------- | -------------------------- | ----------------------------------------------------------- |
| `id`                  | `uuid` PK                  | `gen_random_uuid()`                                         |
| `organization_id`     | `uuid NOT NULL`            | tenant scope → `organizations`                              |
| `project_id`          | `uuid NOT NULL`            | denormalized from `units` (like `contact_units`)            |
| `unit_id`             | `uuid NOT NULL`            | FK `units` — the unit being moved into / out of             |
| `contact_id`          | `uuid NOT NULL`            | FK `contacts` — the person moving (Owner / Tenant)          |
| `contact_unit_id`     | `uuid NULL`                | FK `contact_units` — the link this move belongs to (if any) |
| `move_type`           | `move_event_type NOT NULL` | `move_in` / `move_out` (also the "Status" badge)            |
| `event_date`          | `date NOT NULL`            | when the move happened (table "Date")                       |
| `fee_amount`          | `numeric(12,2) NULL`       | recorded move charge                                        |
| `fee_currency`        | `text NOT NULL`            | default `'INR'`                                             |
| `notes`               | `text NULL`                | free text                                                   |
| `document_paths`      | `text[] NOT NULL`          | default `'{}'` — inspection photos / docs, **paths only**   |
| `recorded_by_user_id` | `uuid NULL`                | `auth.users.id` of the admin who recorded it                |
| `deleted_at`          | `timestamptz NULL`         | soft-void; row kept for audit                               |
| `created_at`          | `timestamptz NOT NULL`     | `now()`                                                     |
| `updated_at`          | `timestamptz NOT NULL`     | `now()`                                                     |

Suggested indexes:

```sql
CREATE INDEX move_events_org_created_idx     ON public.move_events (organization_id, created_at DESC);
CREATE INDEX move_events_org_type_date_idx   ON public.move_events (organization_id, move_type, event_date);
CREATE INDEX move_events_org_unit_idx        ON public.move_events (organization_id, unit_id);
CREATE INDEX move_events_org_contact_idx     ON public.move_events (organization_id, contact_id);
CREATE INDEX move_events_org_project_idx     ON public.move_events (organization_id, project_id);

ALTER TABLE public.move_events ENABLE ROW LEVEL SECURITY;
```

Constraint: `CHECK (fee_amount IS NULL OR fee_amount >= 0)`.

### Tables reused (not created)

| Table                  | Used for                                                    |
| ---------------------- | ----------------------------------------------------------- |
| `units`                | unit label/code, `project_id` derivation, tower + type join |
| `unit_configs`         | the "Type" column (`kind`: apartment / commercial / plot)   |
| `contacts`             | the person moving + their role (`contact_type`)             |
| `contact_units`        | current occupancy — synced on record (active / moved_out)   |
| `towers`               | tower/building name in the unit label                       |
| `organization_members` | acting community admin (RBAC + `recorded_by_user_id`)       |

______________________________________________________________________

## Consequences

### Positive

- **Minimal schema surface** — 1 new table + 1 enum; reuses `units`, `contacts`, `contact_units`,
  `unit_configs`, `towers`.
- **Consistent patterns** — org scope, denormalized `project_id`, deferred RLS, staff RBAC — all
  match the existing admin flows.
- **Full history** — repeated move-in/move-out for the same unit+contact are all retained (ledger).
- **Occupancy stays authoritative** — `contact_units` is kept current on each record and finally
  uses the `moved_out` status ADR 0001 reserved.
- **Cheap reporting** — the list + All/Move-In/Move-Out filter is one indexed query with joins; no
  cron and no denormalized status to maintain.

### Negative / trade-offs

- **Denormalization** — `organization_id`/`project_id` on `move_events` must be kept consistent with
  the parent `units` row (backend-enforced, no trigger; same trade-off as `contact_units`).
- **Two sources of "moved out"** — `contact_units.status/moved_out_at` (current) and `move_events`
  (history) must be kept in sync by the service; a mis-ordered record could disagree (mitigated by
  recording both in one `db_uow` transaction).
- **Fee is informational** — not wired to billing; a real charge/receipt is a follow-up.
- **No RLS policies yet** — backend-only access until a follow-up migration.

### Follow-ups

1. **Implementation** — see [move-events-flow.md](../move-events-flow.md) (endpoints, services, phases).
1. Add RLS policies keyed on `organization_id` + admin role.
1. Optional `MoveEventStatus` (`scheduled` / `completed`) if moves are pre-booked.
1. Wire `fee_amount` into a billing/receipts module when one exists.
1. Optional CSV/XLSX export of the move register (reuse the list query, path/stream only).
1. Optional Kafka event (`move_events.recorded`) for notifications.

______________________________________________________________________

## Alternatives considered

| Alternative                                              | Why rejected                                                                |
| -------------------------------------------------------- | --------------------------------------------------------------------------- |
| Store move-in/out as columns on `contact_units`          | Loses history; a unit+contact can move in/out repeatedly — needs a ledger   |
| Reuse `contact_units.activated_at` / `moved_out_at` only | Single timestamps can't represent repeated moves or per-move fee/notes/docs |
| `passes` + `pass_events`-style parent + child tables     | A move has no per-move sub-timeline; one leaf row is enough                 |
| Snapshot unit/contact/type onto the row                  | They're stable FKs already; join for display, keep the row lean             |
| Contact-scoped (resident) routes                         | This is a community-admin register — staff RBAC, like `projects`/`contacts` |
| Add a `MoveEventStatus` now                              | Screenshot's "Status" is just the type badge; add later if moves get booked |

______________________________________________________________________

## References

- Build guide: [`move-events-flow.md`](../move-events-flow.md)
- Person model + junction-table decision: [ADR 0001](./0001-resident-onboarding.md)
- Occupancy link reused/synced: `contact_units` (`docs/contact-onboarding-flow.md`)
- Inventory reused: `units`, `unit_configs`, `towers` (see `docs/project-setup-flow.md`)
- Staff RBAC + `organization_member` model: [ADR 0004](./0004-pass-validation-gate.md)
