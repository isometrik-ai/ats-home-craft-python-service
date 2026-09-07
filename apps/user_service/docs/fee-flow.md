# Project Fee Configuration Flow — Context & Change Guide

> **Status: Built (Phases 1–3).** Admin fee configuration, invoice generation/scheduling, and resident payment APIs are implemented in `user_service`. See implementation files under `app/api/fee_configuration.py`, `fee_invoices.py`, `maintenance_fees.py`.

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **API prefix:** `/v1/projects/{project_id}/fee-configuration`
- **DB schema:** `ats-home-craft-supabase` (new migrations `2026XXXXXXXXXX_project_fee_`\*)
- **Design decision:** [`docs/adr/0006-project-fee-configuration.md`](./adr/0006-project-fee-configuration.md)

______________________________________________________________________

## 1. What this flow does

After **project setup** is complete (`projects.status = active`), a **community admin** (or finance
admin) configures **maintenance fees** for the project. The Fee Configuration screen has four
sections:

### 1.1 Maintenance fee rate (per property category)

Tabs: **Apartments**, **Plots**, **Commercial** — only tabs whose category matches
`projects.property_types`:

| Tab (UI)   | `property_types` | `project_fee_rates.unit_config_kind` |
| ---------- | ---------------- | ------------------------------------ |
| Apartments | `residential`    | `apartment`                          |
| Commercial | `commercial`     | `commercial`                         |
| Plots      | `plots`          | `plot`                               |

Per tab (independent rate rows; other sections are global):

| Field                    | Control                        | Notes                                                       |
| ------------------------ | ------------------------------ | ----------------------------------------------------------- |
| Fee amount per unit area | Currency input + unit dropdown | Stored as `rate_amount_minor_per_unit` + `measurement_unit` |
| Billing frequency        | Dropdown                       | Monthly, Quarterly, Half-Yearly, Annually                   |
| Fee starts from          | Dropdown                       | See [Fee start triggers](#fee-start-triggers)               |
| After X days             | Number input (when applicable) | `start_offset_days` when trigger = `after_days`             |
| Minimum fee (floor)      | Currency input                 | `minimum_fee_minor`; `0` = no minimum                       |

Helper preview: *"e.g. 1,500 sq ft unit = ₹3,000 / month"* — computed on read from rate + sample area.

### 1.2 Retry & reminder settings (global)

| Field                   | UI values (examples) |
| ----------------------- | -------------------- |
| Retry failed payments   | 0–5 times            |
| Days between each retry | 1, 2, 3, 7 days      |
| Number of reminders     | 0–3 reminders        |
| Days between reminders  | 1, 2, 3, 7 days      |

**Reminder logic (derived, not stored):**

```
first_reminder_lead_days = reminder_count × reminder_interval_days
```

First reminder is sent `first_reminder_lead_days` before `due_date`; subsequent reminders are spaced
by `reminder_interval_days`. Delivery channel: **email + in-app notification** (Phase 2 worker).

### 1.3 When all retries are done (global)

| Option                   | Enum value                 |
| ------------------------ | -------------------------- |
| Escalate to billing team | `escalate_to_billing_team` |

Billing team receives a notification to follow up manually with the owner.

### 1.4 Billing cycle (global)

| Option         | Enum value       | Description                            |
| -------------- | ---------------- | -------------------------------------- |
| Calendar year  | `calendar_year`  | January – December cycle               |
| Financial year | `financial_year` | April – March (India standard)         |
| Pro-rata       | `pro_rata`       | Billed from each unit's fee start date |

Reflected on the future **Fee Management** page (invoice list — Phase 2).

______________________________________________________________________

## 2. Relationship to existing flows

```
Project Setup (admin)
  projects.property_types → which rate tabs appear
  units + unit_configs    → area for fee calculation
  projects.possession_date → possession-date trigger anchor
        ↓
Fee Configuration (admin) ← THIS FLOW (Phase 1)
  project_fee_settings + project_fee_rates
        ↓
Invoice generation (Phase 2 — background worker)
  maintenance_fee_invoices + events
        ↓
Contact Onboarding (resident)
  contact_units.activated_at → onboarding-date trigger anchor
        ↓
Unit detail / Fee Management UI
  financials.base_fee_monthly, outstanding_amount (Phase 3)
```

| Existing artifact                             | Role in fee flow                                   |
| --------------------------------------------- | -------------------------------------------------- |
| `projects.property_types`                     | Determines required rate tabs                      |
| `projects.possession_date`                    | Anchor for `possession_date` start trigger         |
| `projects.primary_measurement_unit`           | Default area unit; rate row can override           |
| `unit_configs.area_sqft` / `carpet_area_sqft` | Apartment/commercial area                          |
| `plot_config_items.size_sqft`                 | Plot area                                          |
| `contact_units.activated_at`                  | Anchor for `onboarding_date` start trigger         |
| `units_service` → `financials.*`              | Placeholders until Phase 3 populates from invoices |

______________________________________________________________________

## 3. Architecture (layers)

Same 3-layer FastAPI pattern:

```
HTTP → API router → Service (business rules) → Repository (SQL) → Postgres
```

### File map (to create)

| Concern                           | File                                                         |
| --------------------------------- | ------------------------------------------------------------ |
| API endpoints                     | `app/api/fee_configuration.py` (or section in `projects.py`) |
| Route registration                | `app/api/routes.py`                                          |
| Config orchestration / validation | `app/services/fee_configuration_service.py`                  |
| Fee calculation helpers           | `app/services/fee_calculation_service.py`                    |
| Settings persistence              | `app/db/repositories/project_fee_settings_repository.py`     |
| Rates persistence                 | `app/db/repositories/project_fee_rates_repository.py`        |
| Request/response models           | `app/schemas/fee_configuration.py`                           |
| Enums (mirror Postgres)           | `app/schemas/enums.py`                                       |
| i18n messages                     | `app/locales/en.json` (`fee_configuration.*`)                |

Phase 2 additions: `maintenance_fee_invoices_repository.py`, `fee_invoice_service.py`,
`fee_scheduler_service.py` (or external worker).

______________________________________________________________________

## 4. Data model

Phase 1 tables (full column reference in [ADR 0006](./adr/0006-project-fee-configuration.md)):

| Table                  | Purpose                                             |
| ---------------------- | --------------------------------------------------- |
| `project_fee_settings` | One row per project — global policy + billing cycle |
| `project_fee_rates`    | Per `unit_config_kind` rate, frequency, trigger     |

Phase 2 tables (invoice lifecycle):

| Table                            | Purpose                                  |
| -------------------------------- | ---------------------------------------- |
| `maintenance_fee_invoices`       | Generated charge per unit per period     |
| `maintenance_fee_invoice_events` | Reminders, payment attempts, escalations |

### Fee start triggers

| Trigger value         | Anchor used                    | Backend source                           |
| --------------------- | ------------------------------ | ---------------------------------------- |
| `onboarding_date`     | When owner finished onboarding | `contact_units.activated_at` (primary)   |
| `possession_date`     | Project possession             | `projects.possession_date`               |
| `first_of_next_month` | 1st day of month after anchor  | Computed from anchor                     |
| `after_one_year`      | Anchor + 365/366 days          | Computed from anchor                     |
| `after_days`          | Anchor + `start_offset_days`   | Requires `start_offset_days` on rate row |

### Fee calculation (preview + invoice generation)

```python
# Pseudocode — fee_calculation_service.py
area_sqft = resolve_unit_area_sqft(unit_row)  # reuse units_service logic
area_in_rate_unit = convert_measurement(area_sqft, target=rate.measurement_unit)
raw_minor = rate.rate_amount_minor_per_unit * area_in_rate_unit
period_minor = apply_billing_frequency(raw_minor, rate.billing_frequency)
charge_minor = max(period_minor, rate.minimum_fee_minor)
```

______________________________________________________________________

## 5. API catalog (Phase 1)

All routes require authentication + org context. RBAC:
`FINANCE_MANAGEMENT_VIEW` (reads), `FINANCE_MANAGEMENT_EDIT` (writes).

| Method | Path                                                  | Purpose                                      |
| ------ | ----------------------------------------------------- | -------------------------------------------- |
| GET    | `/v1/projects/{project_id}/fee-configuration`         | Full config: settings + all rate rows + tabs |
| PUT    | `/v1/projects/{project_id}/fee-configuration`         | Upsert settings + rates (atomic transaction) |
| GET    | `/v1/projects/{project_id}/fee-configuration/preview` | Sample calculation for a unit or area        |

### Example: `GET /v1/projects/{project_id}/fee-configuration`

**Response shape:**

```json
{
  "data": {
    "project_id": "uuid",
    "is_configured": true,
    "configured_at": "2026-07-20T10:00:00Z",
    "settings": {
      "currency": "INR",
      "billing_cycle_type": "financial_year",
      "retry_count": 2,
      "retry_interval_days": 3,
      "reminder_count": 2,
      "reminder_interval_days": 2,
      "first_reminder_lead_days": 4,
      "exhausted_retry_action": "escalate_to_billing_team"
    },
    "applicable_tabs": ["apartment", "plot"],
    "rates": [
      {
        "unit_config_kind": "apartment",
        "rate_amount": 1.25,
        "measurement_unit": "sq_ft",
        "billing_frequency": "monthly",
        "fee_start_trigger": "possession_date",
        "start_offset_days": null,
        "minimum_fee": 500,
        "preview": {
          "sample_area": 1500,
          "sample_area_unit": "sq_ft",
          "computed_monthly_fee": 1875,
          "minimum_applied": false
        }
      }
    ]
  }
}
```

> API responses expose **major currency units** (₹) for UI; DB stores **minor units** (paise).

### Example: `PUT /v1/projects/{project_id}/fee-configuration`

**Request:** settings object + `rates[]` array (one entry per applicable tab the admin filled in).

**Behavior:**

1. Ensure project exists and belongs to org.
1. Validate each rate row's `unit_config_kind` is applicable for `projects.property_types`.
1. Validate `start_offset_days` when `fee_start_trigger = after_days`.
1. Upsert `project_fee_settings` + `project_fee_rates` in one transaction.
1. Set `is_configured = true` when all **required** tabs have rate rows.
1. Audit log under category `FINANCE` / table `project_fee_settings`.

### Example: `GET .../fee-configuration/preview`

Query params: `unit_config_kind`, optional `unit_id` OR `area` + `measurement_unit`.

Returns computed fee for the sample without persisting.

______________________________________________________________________

## 6. Business rules & gating

Enforced in `fee_configuration_service.py`:

- **Project must exist:** 404 `fee_configuration.errors.project_not_found`.
- **Tab applicability:** reject rate rows for kinds not in project's `property_types`
  (`fee_configuration.errors.rate_tab_not_applicable`).
- **Complete configuration:** `is_configured` is false until every required tab has a rate row.
- **Offset days:** required and `> 0` when trigger is `after_days`
  (`fee_configuration.errors.start_offset_required`).
- **Possession warning:** allow save with `possession_date` trigger even if
  `projects.possession_date` is NULL — return a `warnings.possession_date_missing` flag on GET;
  invoice generator skips those units in Phase 2.
- **Onboarding trigger:** units without a primary owner `activated_at` are not billable until
  onboarding completes (Phase 2 scheduler behavior).
- **Minimum fee:** `minimum_fee_minor >= 0`; `0` means no floor.
- **Retry/reminder bounds:** match UI dropdowns (retry 0–4, intervals ∈ {1,2,3,7}).

### Reminder schedule (Phase 2 worker)

For an invoice with `due_date = D`, `reminder_count = N`, `reminder_interval_days = G`:

```
lead = N * G
reminder_dates = [D - lead, D - lead + G, D - lead + 2G, ...]  # up to N reminders
```

Matches UI copy: *"First reminder goes out 6 days before due date"* when N=2, G=3.

### Retry schedule (Phase 2 worker)

After payment failure on due date:

```
for attempt in 1..retry_count:
    retry_at = failed_at + (attempt * retry_interval_days)
```

When `retry_attempts >= retry_count` and still unpaid → `status = escalated`,
`exhausted_retry_action` applied, `maintenance_fee_invoice_events` row `escalated`.

______________________________________________________________________

## 7. Screen → API mapping (from Figma)

| UI section / control                | API field / endpoint                       |
| ----------------------------------- | ------------------------------------------ |
| Apartments / Plots / Commercial tab | `rates[].unit_config_kind`                 |
| Fee amount per unit area            | `rates[].rate_amount` + `measurement_unit` |
| Billing frequency dropdown          | `rates[].billing_frequency`                |
| Fee starts from dropdown            | `rates[].fee_start_trigger`                |
| After X days input                  | `rates[].start_offset_days`                |
| Minimum fee floor                   | `rates[].minimum_fee`                      |
| Retry failed payments               | `settings.retry_count`                     |
| Days between each retry             | `settings.retry_interval_days`             |
| Number of reminders                 | `settings.reminder_count`                  |
| Days between reminders              | `settings.reminder_interval_days`          |
| Escalate to billing team            | `settings.exhausted_retry_action`          |
| Billing cycle radio cards           | `settings.billing_cycle_type`              |
| Save changes                        | `PUT /fee-configuration`                   |
| Cancel                              | Client discards draft — no API call        |

Property type selection (project setup Step 1) determines `applicable_tabs` in GET response — not
re-edited on this screen.

______________________________________________________________________

## 8. Cross-cutting conventions

- **Auth & org scope:** every query filters by `organization_id`.
- **RBAC:** `FINANCE_MANAGEMENT_VIEW` / `FINANCE_MANAGEMENT_EDIT` (new codes — see ADR 0006).
- **Responses:** `success_response` / i18n keys under `fee_configuration.`\*.
- **Writes:** `db_uow` transaction for PUT (settings + rates together).
- **Auditing:** `@audit_api_call` on PUT (`table_name` = `project_fee_settings`, category `FINANCE`).
- **Money:** convert major ↔ minor at API boundary; never store floats in Postgres.
- **Enums:** Python enums mirror Postgres; repositories cast explicitly.

______________________________________________________________________

## 9. Error keys (add under `fee_configuration.errors.*`)

| Key                                                 | When                                           |
| --------------------------------------------------- | ---------------------------------------------- |
| `fee_configuration.errors.project_not_found`        | Invalid project / wrong org                    |
| `fee_configuration.errors.rate_tab_not_applicable`  | Rate for kind not in `property_types`          |
| `fee_configuration.errors.start_offset_required`    | `after_days` without valid `start_offset_days` |
| `fee_configuration.errors.configuration_incomplete` | Missing required tab before marking configured |
| `fee_configuration.errors.invalid_retry_count`      | Outside (2,3,4,5)                              |
| `fee_configuration.errors.invalid_interval`         | Interval not in {1,2,3,7}                      |

______________________________________________________________________

## 10. Implementation phases

### Phase 1 — Fee configuration (this UI)

- [x] Supabase migrations: enums + `project_fee_settings` + `project_fee_rates`
- [x] Enums in `schemas/enums.py`
- [x] `FeeConfigurationService` + repositories
- [x] `GET` / `PUT` / `preview` endpoints
- [x] Seed `FINANCE_MANAGEMENT_*` RBAC codes
- [x] Unit tests: tab applicability, offset validation, preview math, configured flag

### Phase 2 — Invoice generation & automation

- [x] Migrations: `maintenance_fee_invoices` + `maintenance_fee_invoice_events`
- [x] Scheduler: generate invoices from config + unit area + start triggers
- [x] Reminder worker (events recorded; notification delivery follow-up)
- [x] Retry worker + escalation queue for billing team
- [x] Fee Management list API (admin)

### Phase 3 — Resident payments & unit financials

- [x] Payment recording API (PSP webhooks — follow-up)
- [x] Populate `units/{id}/detail` → `financials.base_fee_monthly`, `outstanding_amount`
- [x] Resident invoice list + pay flow (contact-scoped routes)

______________________________________________________________________

## 11. Tests

- `tests/unit/test_fee_configuration_service.py` — tab gating, validation, `is_configured` logic.
- `tests/unit/test_fee_calculation_service.py` — area resolution, frequency, minimum floor, preview.
- `tests/unit/test_fee_invoice_service.py` — payment recording guards

Run: `.venv/bin/python -m pytest apps/user_service/tests/unit`

______________________________________________________________________

## 12. How to make common changes

| I want to…                     | Change here                                                  |
| ------------------------------ | ------------------------------------------------------------ |
| Add a billing frequency option | `BillingFrequency` enum + Postgres enum                      |
| Add a fee start trigger        | `FeeStartTrigger` enum + validation + scheduler anchor logic |
| Add escalation action          | `ExhaustedRetryAction` enum + Phase 2 worker                 |
| Change which tabs appear       | `property_types` mapping in `fee_configuration_service.py`   |
| Change fee formula             | `fee_calculation_service.py`                                 |
| Add sq yard unit               | extend `measurement_unit` enum + conversion table            |
| Wire unit detail financials    | `units_service.py` + invoice aggregation query (Phase 3)     |
| Add an endpoint                | `api/fee_configuration.py` → service → repository            |

______________________________________________________________________

## Related

- Design decision & table DDL: [ADR 0006 — Project fee configuration](./adr/0006-project-fee-configuration.md)
- Upstream inventory: [project-setup-flow.md](./project-setup-flow.md)
- Onboarding date anchor: [contact-onboarding-flow.md](./contact-onboarding-flow.md)
- Schema conventions: `ats-home-craft-supabase/docs/project-setup-schema.md`
