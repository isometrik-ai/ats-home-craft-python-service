# ADR 0018: Fee configuration — three seeded fee heads, project dunning

|                  |                                                                                                                                                                                                               |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Proposed                                                                                                                                                                                                      |
| **Date**         | 2026-09-29                                                                                                                                                                                                    |
| **Authors**      | Home Craft platform team                                                                                                                                                                                      |
| **Depends on**   | [ADR 0011](./0011-project-membership.md) (staff project access), [ADR 0015](./0015-project-level-rbac.md) (`finance_management.*` already seeded)                                                             |
| **Related docs** | [fee-configuration-flow.md](../fee-configuration-flow.md), [project-setup-flow.md](../project-setup-flow.md), [facility-booking-flow.md](../facility-booking-flow.md), [ADR 0017](./0017-facility-booking.md) |
| **Migrations**   | Proposed, not written: `20260929120000_fee_configuration_enums.sql`, `20260929121000_fee_configuration_tables.sql` (`ats-home-craft-supabase`)                                                                |

______________________________________________________________________

## Context

Community admins maintain the rules a society uses to bill residents: maintenance by area,
electricity from a dual-source meter, and a flat club charge. The MyNest admin already has a
Finance nav item. Permission codes already describe this screen:

| Code                       | Seeded description                                               |
| -------------------------- | ---------------------------------------------------------------- |
| `finance_management.view`  | View fee configuration and maintenance fee invoices              |
| `finance_management.edit`  | Create and update project fee configuration                      |
| `finance_management.admin` | Generate invoices, run billing scheduler, and manage escalations |

The screen in the agreed prototype is configuration only. It does not generate invoices.
Facility-booking wallets and invoices ([ADR 0017](./0017-facility-booking.md)) stay a separate
ledger keyed by contact, not by unit.

### What already exists to hang this on

| Need                     | Existing model                                                                                                                                              |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Property type of a unit  | `resolve_unit_property_type()` → `residential`, `commercial`, `plots`                                                                                       |
| Area                     | `unit_configs.area_sqft` (apartment), `unit_configs.carpet_area_sqft` (commercial), `plot_config_items.size_sqft` (plot). `units` itself has no area column |
| Club covering a facility | `FacilityPriceMode.INCLUDED` on facility booking config. Bookings in that mode are not charged per booking                                                  |
| Concurrent edits         | Integer `version` compare-and-swap, as on `facility_booking_configs`                                                                                        |
| Staff access             | `ensure_staff_project_access(project_id)` plus project permission codes                                                                                     |

`units` has no possession date. `projects.possession_date` is the project's date. Pro-rata
billing cannot be executed until Inventory grows a per-unit possession month.

### Product decisions (agreed, do not relitigate)

| #   | Decision                                                                                                                     |
| --- | ---------------------------------------------------------------------------------------------------------------------------- |
| 1   | Up to three fee heads, created by the API. One per kind. No delete. Inactive keeps the row                                   |
| 2   | Category is derived from kind. An admin must not be able to file Maintenance under Amenity                                   |
| 3   | No fee codes and no SAC codes                                                                                                |
| 4   | Maintenance is per square foot only                                                                                          |
| 5   | Electricity fixed charges are a flat amount, not per kW or kVA. Sanctioned load stays on the unit config and is not an input |
| 6   | Unit charges are a single rate, not slabs. Postpaid only                                                                     |
| 7   | Rounding to the nearest rupee is always on. No toggle                                                                        |
| 8   | Billing cycle and invoice day sit on the fee head, not in Settings                                                           |
| 9   | Status is Active or Inactive                                                                                                 |
| 10  | No sample-bill panel. The late-fee sentence is a formula over a fixed sample, not a live unit                                |
| 11  | Every fee is charged to the unit. There is no owner or occupant payer                                                        |
| 12  | Tax is one rate on the whole fee, stored as an object so exemption rules can be added later                                  |
| 13  | No grace period. Due days are the only delay before a late fee                                                               |
| 14  | A late fee is added to the next invoice, not rewritten onto the overdue one                                                  |

______________________________________________________________________

## Decision

### 1. Two new tables, plus a scope table

| Table                          | Purpose                                                                                                                                             |
| ------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`fee_heads`**                | One row per `(project_id, kind)`. Name, status, schedule, tax object, late-fee object, kind-specific charge, optimistic `version`                   |
| **`fee_head_property_scopes`** | Three rows per fee head. The Applies-to flag. Maintenance also stores rate and minimum on these rows, because the tick and the rate are one control |
| **`finance_settings`**         | One row per project. Payment retries and pre-due reminders. Its own `version`                                                                       |

Charge amounts for electricity and club live in `fee_heads.charge` (jsonb), validated by
Pydantic per `kind`. Maintenance rates do not, because the billing run filters units by
property type and those columns want a check constraint.

Tax and late fee stay jsonb objects. Open questions 1, 2, and 4 add keys to those objects.
They must not be flattened into bare columns.

### 2. Kind and category are not editable, and the bill is the unit's

| `kind`        | Category      | Frequency           |
| ------------- | ------------- | ------------------- |
| `maintenance` | `maintenance` | Admin choice        |
| `electricity` | `utility`     | Locked to `monthly` |
| `club`        | `amenity`     | Admin choice        |

Create accepts `kind` and derives `category`. Update rejects both. There is no `payer` column. Maintenance, electricity,
and club all charge the unit that matches Applies to. A let-out unit still gets one set of lines
for that unit. Owner and occupant are not selected here and are not stored.

### 3. Staff create the heads

`POST /fee-heads` inserts one fee head and its three scope rows. `POST /finance-settings`
inserts the dunning row. A project starts with neither. Unique `(project_id, kind)` rejects a
second head of the same kind with 409. There is no delete route.

The rupee figures in the prototype are one society's example. The client sends the amounts,
the name, and which property types are enabled. Adding water or a sinking fund later is a new
`kind`. Create still accepts only the three kinds until open question 6.

### 4. Schedule columns, not a global settings blob

On `fee_heads`:

| Column               | Rule                                                                                                       |
| -------------------- | ---------------------------------------------------------------------------------------------------------- |
| `frequency`          | `monthly`, `quarterly`, `half_yearly`, `annual`. Electricity check forces `monthly`                        |
| `billing_cycle`      | Null when monthly. Otherwise `calendar_year`, `financial_year`, `custom`, `pro_rata`                       |
| `cycle_anchor_month` | Server writes 1 for calendar, 4 for financial. Client sends 1–12 for custom. Null for monthly and pro-rata |
| `fee_start_rule`     | `first_of_next_month` only, in this phase                                                                  |
| `due_within_days`    | 0–365. Due date is the invoice date plus this many days. Late fees start the following day                 |
| `invoice_day`        | 1–28                                                                                                       |
| `meter_read_day`     | 1–28 for electricity, otherwise null                                                                       |

`fee_start_rule` is the "Fee starts from" control. With only one legal value it still matches
the prototype dropdown. It is not an effective-date for the rate. A saved rate is what the
next billing run reads. Issued invoices keep the snapshot they were generated with.

Pro-rata may be stored. Generation of a pro-rata head waits until each unit has a possession
month. This ADR does not add that column to `units`.

### 5. Tax object and late-fee object

```json
{ "applicable": true, "rate_percent": "18.00" }
```

Applicable false means the billing run adds no tax. The last rate may remain stored so the
editor can show it again when the admin turns tax back on. The tax amount is split into two equal
parts at invoice time, not stored as two rates.

```json
{ "mode": "none" }
```

```json
{ "mode": "flat", "steps": [{ "days_overdue": 1, "amount": "100.00" }, { "days_overdue": 15, "amount": "250.00" }] }
```

```json
{ "mode": "interest", "annual_percent": "18.00" }
```

Flat mode stores one to four steps. The charge is the single step whose day threshold the bill
has reached. Interest is simple, charged once per started month on the outstanding amount, with
no cap. A part payment reduces that base. Both behaviours belong to Collections; this screen
only stores the rule and returns the example sentence defined in the flow guide.

### 6. Optimistic concurrency

`fee_heads.version` and `finance_settings.version` start at 1 and increment by 1 on each
successful update. The repository updates `WHERE id = $1 AND version = $expected` and returns
no row on a miss. The service maps that miss to **409** `fee_configuration.errors.version_conflict`.

The list toggle is a separate update of `status` only. It uses the same version column, so a
toggle and a full save cannot silently overwrite each other.

This matches `facility_booking_configs`. Do not compare `updated_at` from the client.

### 7. Permissions stay the ones already seeded

| Action                                        | Code                       |
| --------------------------------------------- | -------------------------- |
| Read fee heads and settings                   | `finance_management.view`  |
| Save a fee head, toggle status, save settings | `finance_management.edit`  |
| Future billing run                            | `finance_management.admin` |

Implementation adds `finance_management.view` to the `facility_manager` role default.
Community admin, accountant, and viewer already have view. Community admin and accountant
already have edit. No new permission code.

The seeded edit description says "Create and update". This phase updates only. The code stays,
because a later kind still uses it.

### 8. Club and facilities stay decoupled

The club editor shows a static sentence about facilities priced Included in membership.
Fee configuration does not query or update `facility_booking_configs`. Turning the club head
inactive does not change those facilities to a per-booking price. An admin who retires the
club charge changes facility pricing in the booking workspace.

### 9. Rounding and money

Rates and amounts are `numeric(12, 2)` rupees (decimal strings on the API). Unit rates such
as 8.50 fit. Invoice arithmetic rounds half up, away from zero, to the nearest rupee, and
posts the difference as a round-off line. There is no per-fee rounding flag. The example
sentence uses that same rounding so the editor and the future invoice agree.

### 10. Out of scope for this ADR's implementation

- Invoice generation, preview, and Collections accrual
- Resident APIs
- Effective-dated rate history
- Per-unit fee overrides
- Accounting export
- Per-component tax and the ₹7,500 RWA threshold
- An interest ceiling
- A dated history of the DG unit rate

The flow guide §13 records the promises those later builds must keep.

______________________________________________________________________

## Schema (proposed)

### Enums

```sql
CREATE TYPE public.fee_head_kind AS ENUM (
  'maintenance',
  'electricity',
  'club'
);

CREATE TYPE public.fee_head_category AS ENUM (
  'maintenance',
  'utility',
  'amenity'
);

CREATE TYPE public.fee_head_status AS ENUM (
  'active',
  'inactive'
);

CREATE TYPE public.fee_frequency AS ENUM (
  'monthly',
  'quarterly',
  'half_yearly',
  'annual'
);

CREATE TYPE public.fee_billing_cycle AS ENUM (
  'calendar_year',
  'financial_year',
  'custom',
  'pro_rata'
);

CREATE TYPE public.fee_start_rule AS ENUM (
  'first_of_next_month'
);
```

Property type on the scope table reuses `public.property_type` (`residential`, `commercial`, `plots`).

### `fee_heads`

| Column                      | Type                       | Notes                                                                  |
| --------------------------- | -------------------------- | ---------------------------------------------------------------------- |
| `id`                        | uuid PK                    |                                                                        |
| `organization_id`           | uuid FK → organizations    |                                                                        |
| `project_id`                | uuid FK → projects         |                                                                        |
| `kind`                      | fee_head_kind NOT NULL     | Immutable                                                              |
| `category`                  | fee_head_category NOT NULL | Set from kind at insert, never updated                                 |
| `name`                      | text NOT NULL              | 1–80 trimmed                                                           |
| `line_description`          | text                       | ≤ 240, null when blank                                                 |
| `status`                    | fee_head_status NOT NULL   | Default `inactive`                                                     |
| `frequency`                 | fee_frequency NOT NULL     | Default `monthly`                                                      |
| `billing_cycle`             | fee_billing_cycle          | Null when monthly                                                      |
| `cycle_anchor_month`        | smallint                   | 1–12 or null. Check matches the cycle rules in the flow guide          |
| `fee_start_rule`            | fee_start_rule NOT NULL    | Default `first_of_next_month`                                          |
| `due_within_days`           | smallint NOT NULL          | 0–365, default 10                                                      |
| `invoice_day`               | smallint NOT NULL          | 1–28, default 1                                                        |
| `meter_read_day`            | smallint                   | 1–28 for electricity, else null                                        |
| `charge`                    | jsonb NOT NULL             | Electricity four rates, or club `{ "amount" }`. `'{}'` for maintenance |
| `tax`                       | jsonb NOT NULL             | `{ "applicable": false }` at seed                                      |
| `late_fee`                  | jsonb NOT NULL             | `{ "mode": "none" }` at seed                                           |
| `version`                   | integer NOT NULL           | Default 1                                                              |
| `created_at` / `updated_at` | timestamptz                |                                                                        |
| `updated_by`                | uuid FK → auth.users       | Staff user, null on seed                                               |

**Constraints:**

- Unique `(organization_id, project_id, kind)`
- Electricity: `frequency = monthly`, `billing_cycle` is null, `meter_read_day` is not null, `charge` has the four numeric keys
- Maintenance and club: `meter_read_day` is null
- Maintenance: `charge = '{}'`
- Club: `charge` has `amount`
- `late_fee->>'mode'` in `none`, `flat`, `interest`
- `tax` has boolean `applicable`
- Non-monthly rows have a non-null `billing_cycle`
- Monthly rows have null `billing_cycle` and null `cycle_anchor_month`

### `fee_head_property_scopes`

| Column            | Type                                  | Notes                                                        |
| ----------------- | ------------------------------------- | ------------------------------------------------------------ |
| `fee_head_id`     | uuid FK → fee_heads ON DELETE CASCADE |                                                              |
| `organization_id` | uuid NOT NULL                         | Denormalized for org-scoped queries                          |
| `property_type`   | property_type NOT NULL                |                                                              |
| `enabled`         | boolean NOT NULL                      | Default true                                                 |
| `rate_per_sqft`   | numeric(12, 2)                        | Maintenance only, ≥ 0, required even when `enabled` is false |
| `minimum_amount`  | numeric(12, 2)                        | Maintenance only, ≥ 0. 0 means no floor                      |

Primary key `(fee_head_id, property_type)`.

The service keeps each fee head at exactly three scope rows.
Non-maintenance rows have a null rate and minimum. Maintenance rows have both.

### `finance_settings`

| Column                           | Type                    | Notes                                                    |
| -------------------------------- | ----------------------- | -------------------------------------------------------- |
| `id`                             | uuid PK                 |                                                          |
| `organization_id`                | uuid FK → organizations |                                                          |
| `project_id`                     | uuid FK → projects      | Unique                                                   |
| `payment_retry_count`            | smallint NOT NULL       | 0–12, default 3                                          |
| `payment_retry_interval_days`    | smallint NOT NULL       | 1–30, default 2. Ignored by the sentence when count is 0 |
| `pre_due_reminder_count`         | smallint NOT NULL       | 0–12, default 2                                          |
| `pre_due_reminder_interval_days` | smallint NOT NULL       | 1–30, default 3                                          |
| `version`                        | integer NOT NULL        | Default 1                                                |
| `created_at` / `updated_at`      | timestamptz             |                                                          |
| `updated_by`                     | uuid                    |                                                          |

### Indexes

- `fee_heads (organization_id, project_id, status)` for the billing run's active set
- `fee_head_property_scopes (organization_id, fee_head_id)` for the editor load

______________________________________________________________________

## Consequences

### Positive

- The admin creates each of the three kinds when the society needs it. Settings stay free of per-fee fields.
- Category cannot be saved wrong, which is the failure the first prototype screenshot actually hit.
- The bill is always the unit's. A later invoices screen does not have to split electricity to a tenant and maintenance to an owner.
- Tax and late fee can grow keys for the open tax and interest questions without a new table.
- Version conflicts use a pattern this service already ships.
- Finance permission codes already exist, including the split between editing configuration and running billing.
- Facility-booking invoices stay untouched, so club membership and court charges are not one ledger by accident.

### Negative / trade-offs

- A society cannot add water or a sinking fund until we extend `fee_head_kind`. That is intentional for the pilot.
- Nothing is billed until an admin creates a fee head and turns it active. The prototype's ₹3.25 / ₹8.5 / ₹1,500 are not copied into every project.
- Pro-rata can be saved and cannot yet be generated.
- Deactivating club does not reprice Included-in-membership facilities.
- `facility_manager` gains finance view. That role can read rates. It still cannot edit them or run billing.
- One tax rate applies to grid and DG together, and to maintenance below ₹7,500, until the client answers questions 1 and 2.

### Follow-ups

1. Migrations and the service in [fee-configuration-flow.md](../fee-configuration-flow.md).
1. Add `finance_management.view` to `DEFAULT_PROJECT_ROLE_PERMISSIONS["facility_manager"]`.
1. Per-unit possession month, then pro-rata generation.
1. Billing-run snapshot, merge rule, and the inactive-fee line in the run preview.
1. Revisit tax and interest when the six client questions close.

______________________________________________________________________

## Alternatives considered

| Alternative                                             | Why rejected                                                                                                                                                                                                         |
| ------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Generic fee-head builder with create and delete         | The pilot society has these three charges. A builder is more to get wrong and more to test. The `kind` enum still leaves room to add a fourth by migration                                                           |
| Admin-picked category                                   | The first prototype filed Maintenance under Amenity. If it can be set wrong, it will be                                                                                                                              |
| Single jsonb document for the whole fee head            | Invoice day, status, and Applies-to need columns the billing run can filter on. Applicability also needs a check that maintenance rates survive an untick                                                            |
| A table per charge kind                                 | Electricity and club are small, known shapes. jsonb plus Pydantic keeps a future water head from adding a table before we know its fields. Maintenance rates stay relational because they are the Applies-to control |
| Tax as a single nullable column                         | Questions 1 and 2 need an object (per-component flags, or a threshold). Flattening it forces a rewrite of every reader                                                                                               |
| Global invoice date and a "one invoice per unit" switch | Fees in this society do not share a date. Merge is "same unit and same invoice day"                                                                                                                                  |
| Status values draft and paused                          | They would not behave differently from inactive. Draft is an invoice word                                                                                                                                            |
| Effective-dated rate rows now                           | The client has not asked for "from 1 April". A version snapshot at generation covers audit until they do                                                                                                             |
| Compare `updated_at` for conflicts                      | Sub-second clashes and clock display make it a poor token. `version` is already the local pattern                                                                                                                    |
| Seed the prototype's rupee rates                        | Those figures are ATS Nobility's example. Seeding them would bill every new project the wrong amount the moment someone marks the head Active                                                                        |
| Owner vs occupant payer per fee head                    | Fees are unit charges. A let-out unit does not split electricity to the tenant and the other heads to the owner                                                                                                      |
| Reuse facility-booking invoices for maintenance         | Those invoices belong to a contact's booking ledger and a wallet. Maintenance is a charge on the unit                                                                                                                |

______________________________________________________________________

## Open questions

Ordered by how much a yes changes the build. The default in the right-hand column is what this
ADR implements.

| #   | Question                                                    | Default until the client answers                       |
| --- | ----------------------------------------------------------- | ------------------------------------------------------ |
| 1   | Is grid electricity tax-exempt while DG is taxed?           | One `rate_percent` on all four components              |
| 2   | Does the ₹7,500 / month RWA exemption apply to maintenance? | No threshold. Tax, when on, applies to the full amount |
| 3   | Can a rate be scheduled ("from 1 April")?                   | The next billing run reads the current row             |
| 4   | Is interest capped, and is it compound?                     | Simple, per started month, uncapped                    |
| 5   | Is the DG unit rate reset monthly against diesel spend?     | One current value, no history                          |
| 6   | Do water, piped gas, or a sinking fund return?              | Create accepts the three kinds only                    |
