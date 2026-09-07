# ADR 0003: Contracts and scheduler — lazy AMC generation

|              |                                                                  |
| ------------ | ---------------------------------------------------------------- |
| **Status**   | Accepted (V1)                                                    |
| **Date**     | 2026-09-02                                                       |
| **Flow doc** | [../contracts-scheduler-flow.md](../contracts-scheduler-flow.md) |
| **Schema**   | `work_order.maintenance_contracts` → `work_order.work_orders`    |

______________________________________________________________________

## Context

AMC contracts specify visit frequency (monthly, quarterly, …), covered assets, vendor (`company_id`),
and contract window. Work orders must appear **ahead of visit dates** (lead window, default 5 days)
without duplicate rows if the scheduler runs twice or multiple app instances are deployed.

Contract PATCH/DELETE must reconcile already-generated upcoming work orders.

______________________________________________________________________

## Decision

### Contract row drives generation

`maintenance_contracts` stores frequency, dates, `auto_generate_lead_days`, `asset_ids`, `company_id`,
`form_template_id`, `status`.

### Lazy idempotent scheduler

`SchedulerService.generate_due_work_orders()`:

1. Load active contracts for org (or all orgs in background loop).
1. Iterate visit dates from `start_date` / `last_serviced_date` using frequency step.
1. Create WO only if `scheduled_date <= today + lead_days` and date not in `existing_contract_schedule_dates`.
1. Each new WO gets vendor token hash + timeline entry (`by=Scheduler`).

### Reconciliation

`ContractsService` create/update/delete → `SchedulerService.recompute_contract()`:

- Terms change → cancel stale upcoming WOs, regenerate.
- Terminate/delete → cancel contract-linked upcoming WOs.

### Multi-instance safety

Background `run_scheduler_loop()` in lifespan; each tick uses `pg_try_advisory_lock(824731, 1)`.

Manual run: `POST /projects/{project_id}/scheduler/run` (staff JWT or `X-Internal-Token`).

Env: `WOM_SCHEDULER_ENABLED`, `WOM_SCHEDULER_INTERVAL_MINUTES`, `WOM_INTERNAL_SERVICE_TOKEN`.

______________________________________________________________________

## Consequences

**Positive:** Prototype scheduler logic preserved; safe at scale with advisory lock; unit-tested date iteration.

**Negative:** Raw vendor token embedded in scheduler timeline note for FM — improve secure delivery later.

**Follow-ups:** Metrics on `created`/`skipped` per run; alert if lock always held.

______________________________________________________________________

## Alternatives considered

| Alternative                 | Rejected because                                 |
| --------------------------- | ------------------------------------------------ |
| Cron outside app            | Extra infra; in-process + lock sufficient for V1 |
| Pre-generate all future WOs | DB bloat; lazy + lead window matches ops         |
| No lock                     | Duplicate WOs under horizontal scale             |
