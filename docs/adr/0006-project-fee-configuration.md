# ADR 0006: Project fee configuration — schema and backend model

|                  |                                                                                                                                                 |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Accepted (Phase 1–3 implemented)                                                                                                                |
| **Date**         | 2026-07-20                                                                                                                                      |
| **Authors**      | Home Craft platform team                                                                                                                        |
| **Depends on**   | Project setup (`[project-setup-flow.md](../project-setup-flow.md)`), contact onboarding ([ADR 0001](./0001-resident-onboarding.md))             |
| **Related docs** | [fee-flow.md](../fee-flow.md) (flow & change guide), `[project-setup-schema.md](../../../ats-home-craft-supabase/docs/project-setup-schema.md)` |
| **Migrations**   | `2026XXXXXXXXXX_project_fee_enums.sql`, `2026XXXXXXXXXX_project_fee_tables.sql` (to be created in `ats-home-craft-supabase`)                    |

______________________________________________________________________

## Context

Community admins need to configure **maintenance fees** for a project before billing can run. The
**Fee Configuration** screen (Finance → Settings) lets an admin set:

1. **Per property-category rates** — Apartments / Plots / Commercial tabs (only tabs matching
   `projects.property_types`: residential → Apartments, plots → Plots, commercial → Commercial).
   Each tab defines a **rate per unit area**, **billing frequency**, **fee start trigger**, and
   optional **minimum fee floor**.
1. **Global retry & reminder policy** — failed payment retries, pre-due reminders (email + in-app).
1. **Escalation policy** — what happens when retries are exhausted (currently: escalate to billing
   team).
1. **Billing cycle alignment** — calendar year, financial year (Apr–Mar), or pro-rata per unit.

This mirrors the platform's existing flows:

- **Project Setup (admin)** creates inventory (`projects`, `units`, `unit_configs`) and selects
  `property_types`.
- **Contact Onboarding (resident)** links owners to units via `contact_units` and sets
  `activated_at` on finalize.
- **Fee Configuration (admin)** — this ADR — defines how maintenance fees are calculated and
  collected for that project.

Today, `GET /v1/projects/{project_id}/units/{unit_id}/detail` returns
`financials.base_fee_monthly` and `financials.outstanding_amount` as `null` placeholders until
billing is implemented (`units_service.py`). This ADR covers the **configuration layer** (Phase 1)
and defines the **invoice / retry / reminder tables** (Phase 2) so the full fee lifecycle has a
stable schema upfront.

Constraints (same as prior ADRs):

- Multi-tenancy via `organization_id` on every new table.
- Project scope via `project_id` on all fee tables (denormalized where a row also references
  `units`).
- Admin routes use org-member RBAC (`FINANCE_MANAGEMENT_*` — new codes, see below); resident
  payment routes are a follow-up.
- Money stored in **minor units** (`bigint`) with `currency text DEFAULT 'INR'` (see
  `project-setup-schema.md` conventions).
- Area rates reference `measurement_unit`; stored amounts are always computed from the unit's
  area in the configured unit (backend normalizes using `projects.primary_measurement_unit` and
  config overrides).
- RLS enabled on new tables; **policies deferred** (backend uses `service_role`), matching earlier
  phases.

______________________________________________________________________

## Decision

### 1. Split configuration into two tables: global settings + per-category rates

| Table                  | Cardinality              | Purpose                                                                     |
| ---------------------- | ------------------------ | --------------------------------------------------------------------------- |
| `project_fee_settings` | **1 row per project**    | Retry/reminder policy, escalation, billing cycle, currency, configured flag |
| `project_fee_rates`    | **0–3 rows per project** | Rate, frequency, start trigger, minimum floor **per** `unit_config_kind`    |

> **Why two tables:** the UI explicitly states "Set a separate rate for apartments. Other settings
> below apply to all property types." Global policy belongs on the project row; category-specific
> calculation inputs belong on child rows keyed by `unit_config_kind` (`apartment`, `commercial`,
> `plot`).

Only rate tabs whose `unit_config_kind` maps to a selected `property_types` value are **required**
before fee configuration is considered complete:

| `property_types` contains | Required `project_fee_rates.unit_config_kind` |
| ------------------------- | --------------------------------------------- |
| `residential`             | `apartment`                                   |
| `commercial`              | `commercial`                                  |
| `plots`                   | `plot`                                        |

### 2. Fee amount formula (stored rate → computed charge)

For a unit in billing period *P*:

```
area = resolve_unit_area_sqft(unit)   -- from unit_configs / plot_config_items
rate = project_fee_rates.rate_amount_minor_per_unit
area_in_rate_unit = convert_area(area, rate.measurement_unit)
raw_amount = rate_amount * area_in_rate_unit
period_amount = apply_frequency(raw_amount, billing_frequency)
charge = max(period_amount, minimum_fee_minor)
```

- `rate_amount_minor_per_unit` is the admin-entered "₹ X / sq ft" value converted to minor units
  (paise).
- `minimum_fee_minor` is the floor (`0` = no minimum).
- `resolve_unit_area_sqft` reuses the same logic as `units_service.resolve_carpet_area_sqft`
  (apartment `area_sqft`, commercial `carpet_area_sqft`, plot `size_sqft` on `plot_config_items`).

Preview helper text in the UI ("e.g. 1,500 sq ft unit = ₹3,000 / month") is computed server-side
on read — not persisted.

### 3. Fee start trigger (when a unit begins billing)

Enum `fee_start_trigger` on each `project_fee_rates` row:

| Value                 | Anchor date source                                              |
| --------------------- | --------------------------------------------------------------- |
| `onboarding_date`     | Primary owner `contact_units.activated_at` for the unit         |
| `possession_date`     | `projects.possession_date` (project-level today)                |
| `first_of_next_month` | First day of the month after the anchor event                   |
| `after_one_year`      | Anchor + 1 calendar year                                        |
| `after_days`          | Anchor + `start_offset_days` (requires `start_offset_days > 0`) |

For `onboarding_date`, if no primary owner has `activated_at` yet, the unit is **not billable**
(scheduler skips until onboarding completes). For `possession_date`, if `projects.possession_date`
is NULL, configuration save is allowed but invoice generation must skip with a logged reason.

> **Unit-level possession** is not in the schema today. If product later needs per-unit possession,
> add `units.possession_date` in a follow-up migration — do not block Phase 1 on it.

### 4. Billing cycle (period alignment)

Enum `billing_cycle_type` on `project_fee_settings`:

| Value            | Behavior                                                       |
| ---------------- | -------------------------------------------------------------- |
| `calendar_year`  | Periods align Jan–Dec across all units                         |
| `financial_year` | Periods align Apr–Mar (India standard)                         |
| `pro_rata`       | Each unit's periods start from its computed **fee start date** |

`billing_frequency` on each rate row (`monthly`, `quarterly`, `half_yearly`, `annually`) defines
the **length of each charge** within the chosen cycle. Pro-rata may produce a partial first period
when a unit's start date falls mid-cycle.

### 5. Retry, reminder, and escalation policy

Stored on `project_fee_settings` (global):

| Field                      | UI control                | Notes                                                          |
| -------------------------- | ------------------------- | -------------------------------------------------------------- |
| `retry_count`              | Retry failed payments     | 2,3,4,5 (UI dropdown)                                          |
| `retry_interval_days`      | Days between each retry   | 1, 2, 3, 7                                                     |
| `reminder_count`           | Number of reminders       | 0–3                                                            |
| `reminder_interval_days`   | Days between reminders    | 1, 2, 3, 7                                                     |
| `first_reminder_lead_days` | *(derived, not stored)*   | `reminder_count * reminder_interval_days` days before due date |
| `exhausted_retry_action`   | When all retries are done | Currently only `escalate_to_billing_team`                      |

Reminder delivery (email + in-app) and payment retry execution are **Phase 2 workers** — Phase 1
only persists the policy. The UI note ("First reminder goes out N days before due date") matches
the derived formula above.

### 6. Invoice + operational tables (Phase 2)

| Table                            | Purpose                                                                |
| -------------------------------- | ---------------------------------------------------------------------- |
| `maintenance_fee_invoices`       | One row per unit per billing period: amount, due date, status          |
| `maintenance_fee_invoice_events` | Append-only: reminder_sent, payment_attempted, paid, failed, escalated |

Invoice `status` (`maintenance_fee_invoice_status`): `draft`, `issued`, `paid`, `partially_paid`,
`overdue`, `failed`, `escalated`, `cancelled`.

Payment gateway integration is out of scope for this ADR; retries model **collection attempts**
(status transitions + events), not a specific PSP.

### 7. Enums (mirror Postgres, `str, Enum` in `app/schemas/enums.py`)

```python
class BillingFrequency(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    ANNUALLY = "annually"

class FeeStartTrigger(str, Enum):
    ONBOARDING_DATE = "onboarding_date"
    POSSESSION_DATE = "possession_date"
    FIRST_OF_NEXT_MONTH = "first_of_next_month"
    AFTER_ONE_YEAR = "after_one_year"
    AFTER_DAYS = "after_days"

class BillingCycleType(str, Enum):
    CALENDAR_YEAR = "calendar_year"
    FINANCIAL_YEAR = "financial_year"
    PRO_RATA = "pro_rata"

class ExhaustedRetryAction(str, Enum):
    ESCALATE_TO_BILLING_TEAM = "escalate_to_billing_team"

class MaintenanceFeeInvoiceStatus(str, Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    PAID = "paid"
    PARTIALLY_PAID = "partially_paid"
    OVERDUE = "overdue"
    FAILED = "failed"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"

class MaintenanceFeeInvoiceEventType(str, Enum):
    ISSUED = "issued"
    REMINDER_SENT = "reminder_sent"
    PAYMENT_ATTEMPTED = "payment_attempted"
    PAYMENT_SUCCEEDED = "payment_succeeded"
    PAYMENT_FAILED = "payment_failed"
    RETRY_SCHEDULED = "retry_scheduled"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"
```

Reuse existing `measurement_unit` enum (`sq_ft`, `sq_m`, `gaj`). Add `sq_yard` in the fee
enums migration if product confirms the UI dropdown (screenshots show "sq yard") — otherwise map UI
"sq yard" to `sq_m` conversion until added.

Reuse existing `unit_config_kind` (`apartment`, `commercial`, `plot`) for rate rows — do not
introduce a parallel property-type enum.

### 8. RBAC

New permission codes (seed in `common_query.py` with other `*_MANAGEMENT_*` codes):

| Code                       | Purpose                                      |
| -------------------------- | -------------------------------------------- |
| `FINANCE_MANAGEMENT_VIEW`  | Read fee configuration + invoices            |
| `FINANCE_MANAGEMENT_EDIT`  | Create/update fee configuration              |
| `FINANCE_MANAGEMENT_ADMIN` | Escalation queue, manual overrides (Phase 2) |

Community admins with `PROJECTS_MANAGEMENT_EDIT` alone do **not** automatically get finance edit;
Finance is a separate module in the UI breadcrumb.

______________________________________________________________________

## New tables (what's needed)

> DDL below is the **intended shape** for Supabase migrations. Follow conventions from
> `project-setup-schema.md` (org FK, `set_updated_at` trigger, RLS enabled).

### Enums

```sql
CREATE TYPE public.billing_frequency AS ENUM (
    'monthly', 'quarterly', 'half_yearly', 'annually'
);
CREATE TYPE public.fee_start_trigger AS ENUM (
    'onboarding_date', 'possession_date', 'first_of_next_month',
    'after_one_year', 'after_days'
);
CREATE TYPE public.billing_cycle_type AS ENUM (
    'calendar_year', 'financial_year', 'pro_rata'
);
CREATE TYPE public.exhausted_retry_action AS ENUM (
    'escalate_to_billing_team'
);
CREATE TYPE public.maintenance_fee_invoice_status AS ENUM (
    'draft', 'issued', 'paid', 'partially_paid', 'overdue',
    'failed', 'escalated', 'cancelled'
);
CREATE TYPE public.maintenance_fee_invoice_event_type AS ENUM (
    'issued', 'reminder_sent', 'payment_attempted', 'payment_succeeded',
    'payment_failed', 'retry_scheduled', 'escalated', 'cancelled'
);
-- Optional: ALTER TYPE public.measurement_unit ADD VALUE 'sq_yard';
```

### `project_fee_settings`

| Column                   | Type                            | Notes                                                |
| ------------------------ | ------------------------------- | ---------------------------------------------------- |
| `id`                     | uuid PK                         | `gen_random_uuid()`                                  |
| `organization_id`        | uuid NOT NULL                   | tenant scope                                         |
| `project_id`             | uuid NOT NULL UNIQUE            | FK `projects` — one settings row per project         |
| `currency`               | text NOT NULL                   | default `'INR'`                                      |
| `billing_cycle_type`     | billing_cycle_type NOT NULL     | default `'financial_year'`                           |
| `retry_count`            | smallint NOT NULL               | default `2`, CHECK (2,3,4,5)                         |
| `retry_interval_days`    | smallint NOT NULL               | default `1`, CHECK IN (1,2,3,7)                      |
| `reminder_count`         | smallint NOT NULL               | default `1`, CHECK 0–3                               |
| `reminder_interval_days` | smallint NOT NULL               | default `1`, CHECK IN (1,2,3,7)                      |
| `exhausted_retry_action` | exhausted_retry_action NOT NULL | default `'escalate_to_billing_team'`                 |
| `is_configured`          | boolean NOT NULL                | default `false` — true when all required rates exist |
| `configured_at`          | timestamptz NULL                | set when admin saves a complete configuration        |
| `configured_by`          | uuid NULL                       | FK `auth.users`                                      |
| `created_at`             | timestamptz NOT NULL            | `now()`                                              |
| `updated_at`             | timestamptz NOT NULL            | `now()`                                              |

Suggested index: `(organization_id, project_id)`.

### `project_fee_rates`

| Column                       | Type                       | Notes                                           |
| ---------------------------- | -------------------------- | ----------------------------------------------- |
| `id`                         | uuid PK                    | `gen_random_uuid()`                             |
| `organization_id`            | uuid NOT NULL              | tenant scope                                    |
| `project_id`                 | uuid NOT NULL              | FK `projects`                                   |
| `unit_config_kind`           | unit_config_kind NOT NULL  | `apartment` / `commercial` / `plot`             |
| `rate_amount_minor_per_unit` | bigint NOT NULL            | paise per 1 unit of `measurement_unit`          |
| `measurement_unit`           | measurement_unit NOT NULL  | sq ft / sq m / gaj (+ sq_yard if added)         |
| `billing_frequency`          | billing_frequency NOT NULL | default `'monthly'`                             |
| `fee_start_trigger`          | fee_start_trigger NOT NULL | default `'possession_date'`                     |
| `start_offset_days`          | smallint NULL              | required when trigger = `after_days`; CHECK > 0 |
| `minimum_fee_minor`          | bigint NOT NULL            | default `0` (no floor)                          |
| `created_at`                 | timestamptz NOT NULL       | `now()`                                         |
| `updated_at`                 | timestamptz NOT NULL       | `now()`                                         |

Unique: `(project_id, unit_config_kind)`.

CHECK constraint: `(fee_start_trigger <> 'after_days') OR (start_offset_days IS NOT NULL AND start_offset_days > 0)`.

### `maintenance_fee_invoices` (Phase 2)

| Column              | Type                           | Notes                                           |
| ------------------- | ------------------------------ | ----------------------------------------------- |
| `id`                | uuid PK                        | `gen_random_uuid()`                             |
| `organization_id`   | uuid NOT NULL                  | tenant scope                                    |
| `project_id`        | uuid NOT NULL                  | FK `projects`                                   |
| `unit_id`           | uuid NOT NULL                  | FK `units`                                      |
| `contact_unit_id`   | uuid NULL                      | FK `contact_units` — billed owner link          |
| `unit_config_kind`  | unit_config_kind NOT NULL      | denormalized from unit's config                 |
| `period_start`      | date NOT NULL                  | billing period start (inclusive)                |
| `period_end`        | date NOT NULL                  | billing period end (inclusive)                  |
| `due_date`          | date NOT NULL                  | payment due                                     |
| `amount_minor`      | bigint NOT NULL                | charge after floor                              |
| `amount_paid_minor` | bigint NOT NULL                | default `0`                                     |
| `currency`          | text NOT NULL                  | copied from settings                            |
| `status`            | maintenance_fee_invoice_status | default `'draft'`                               |
| `retry_attempts`    | smallint NOT NULL              | default `0`                                     |
| `next_retry_at`     | timestamptz NULL               | scheduler sets from settings                    |
| `reminders_sent`    | smallint NOT NULL              | default `0`                                     |
| `next_reminder_at`  | timestamptz NULL               | derived from settings                           |
| `escalated_at`      | timestamptz NULL               | set when status → `escalated`                   |
| `issued_at`         | timestamptz NULL               | when moved from `draft` → `issued`              |
| `paid_at`           | timestamptz NULL               | full payment                                    |
| `metadata`          | jsonb NOT NULL                 | default `'{}'` — rate snapshot, area used, etc. |
| `created_at`        | timestamptz NOT NULL           | `now()`                                         |
| `updated_at`        | timestamptz NOT NULL           | `now()`                                         |

Unique: `(unit_id, period_start, period_end)` among non-cancelled rows (partial index
`WHERE status <> 'cancelled'`).

Suggested indexes: `(organization_id, project_id, status, due_date)`,
`(organization_id, unit_id, period_start DESC)`.

### `maintenance_fee_invoice_events` (Phase 2)

| Column            | Type                               | Notes                                           |
| ----------------- | ---------------------------------- | ----------------------------------------------- |
| `id`              | uuid PK                            | `gen_random_uuid()`                             |
| `organization_id` | uuid NOT NULL                      | tenant scope                                    |
| `invoice_id`      | uuid NOT NULL                      | FK `maintenance_fee_invoices` ON DELETE CASCADE |
| `event_type`      | maintenance_fee_invoice_event_type |                                                 |
| `occurred_at`     | timestamptz NOT NULL               | default `now()`                                 |
| `actor_user_id`   | uuid NULL                          | admin/system user                               |
| `notes`           | text NULL                          |                                                 |
| `metadata`        | jsonb NOT NULL                     | default `'{}'`                                  |
| `created_at`      | timestamptz NOT NULL               | `now()`                                         |

Suggested index: `(organization_id, invoice_id, occurred_at)`.

### Tables reused (not created)

| Table               | Used for                                                        |
| ------------------- | --------------------------------------------------------------- |
| `projects`          | `property_types`, `possession_date`, `primary_measurement_unit` |
| `units`             | unit identity, link to config/plot item for area                |
| `unit_configs`      | apartment/commercial area fields                                |
| `plot_config_items` | plot area (`size_sqft`)                                         |
| `contact_units`     | `activated_at` for onboarding-date trigger; invoice owner link  |
| `contacts`          | owner display on invoices / escalation queue                    |

______________________________________________________________________

## Consequences

### Positive

- **Clear separation** — configuration (Phase 1) vs operational billing (Phase 2) without rework.
- **Matches UI** — per-tab rates + global retry/reminder/cycle map 1:1 to two config tables.
- **Reuses inventory** — area and property category come from existing project-setup tables.
- **Extensible** — `maintenance_fee_invoice_events` supports reminders, retries, and escalation audit trail.
- **Unit detail ready** — Phase 2 can populate `financials.base_fee_monthly` and
  `financials.outstanding_amount` from `maintenance_fee_invoices`.

### Negative / trade-offs

- **Project-level possession** — `possession_date` trigger uses `projects.possession_date`, not
  per-unit dates. Document limitation until `units.possession_date` exists.
- **Measurement unit gap** — DB enum lacks `sq_yard`; needs migration or UI restriction.
- **No payment PSP in scope** — retry logic models attempts/events; actual charge API is follow-up.
- **Scheduler required** — invoice generation, reminders, and retries need background jobs (cron/worker).
- **Denormalization** — `organization_id` / `project_id` on child rows must match parent FKs
  (backend-enforced, same as `contact_units` / `vehicles`).

### Follow-ups

1. **Phase 1 implementation** — see [fee-flow.md](../fee-flow.md) (config API, validation, preview).
1. **Phase 2** — invoice generator, reminder/retry workers, escalation queue for billing team.
1. **Phase 3** — resident payment UI, PSP webhooks, populate unit `financials` on detail API.
1. Add RLS policies keyed on `organization_id` + finance role.
1. Optional Kafka events (`fees.invoice_issued`, `fees.escalated`) for notifications.
1. Extend `measurement_unit` with `sq_yard` if confirmed by product.
1. Per-unit `possession_date` if communities need it.

______________________________________________________________________

## Alternatives considered

| Alternative                                      | Why rejected                                                                  |
| ------------------------------------------------ | ----------------------------------------------------------------------------- |
| Single JSON blob on `projects` for all fee rules | Hard to validate, query, and audit; doesn't match relational patterns         |
| One combined `fee_configurations` table          | Mixes global policy with per-category rates; awkward NULL columns for tabs    |
| Store computed monthly fee on `units`            | Stale when rates change; compute from config + area at invoice time           |
| Reuse CRM `billing_preferences` on contacts      | Wrong domain — maintenance fees are project-scoped, not contact CRM prefs     |
| Skip invoice tables in ADR                       | Retry/reminder UI implies operational tables; defining them now avoids rework |

______________________________________________________________________

## References

- Flow & change guide: `[fee-flow.md](../fee-flow.md)`
- Project setup (inventory + property types): `[project-setup-flow.md](../project-setup-flow.md)`
- Onboarding date anchor: `[contact-onboarding-flow.md](../contact-onboarding-flow.md)`
- Unit detail financial placeholders: `apps/user_service/app/services/units_service.py`
- Money conventions: `[project-setup-schema.md](../../../ats-home-craft-supabase/docs/project-setup-schema.md)`
