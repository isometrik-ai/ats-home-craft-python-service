# ADR 0019: App access control — overdue dues drop the resident app to a hold

|                  |                                                                                                                                                                                                                                                                                               |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Proposed                                                                                                                                                                                                                                                                                      |
| **Date**         | 2026-10-28                                                                                                                                                                                                                                                                                    |
| **Authors**      | Home Craft platform team                                                                                                                                                                                                                                                                      |
| **Depends on**   | [ADR 0010](./0010-project-membership.md), [ADR 0014](./0014-project-level-rbac.md), [ADR 0001](./0001-resident-onboarding.md) (contacts + `portal_access`), [ADR 0017](./0017-fee-configuration.md) (fee heads, invoices, outstanding)                                                        |
| **Related docs** | [0019-app-access-control-flow.md](../0019-app-access-control-flow.md), [0017-fee-configuration-flow.md](../0017-fee-configuration-flow.md), [0001-contact-onboarding-flow.md](../0001-contact-onboarding-flow.md), prototype `mynest_admin_module.html` (Administration → App Access Control) |
| **Migrations**   | Proposed, not written: `20261028120000_app_access_enums.sql`, `20261028121000_app_access_tables.sql`, `20261028122000_app_access_permissions.sql` (`ats-home-craft-supabase`)                                                                                                                 |

______________________________________________________________________

## Context

The MyNest admin prototype (`mynest_admin_module.html`) opens on **Administration → App Access
Control**. The screen has two tabs:

1. **Rule** — a master switch, a payment window, and what triggers a block past due (amount,
   days overdue, or both with AND / OR).
1. **Customers** — one row per billed party: outstanding, invoice age, days past due, access
   state, blocked-on, and a one-off **Release** / **Block now** / **Back to rule**.

A block is fixed: the resident app drops to a hold until the dues are paid or a staff override
releases them. The rule is project-wide. Overrides are per customer.

That is the flow this ADR covers. The same HTML file also sketches Users, Teams, Roles,
Sessions, Audit Logs, and a Tasks module. Those are **not** this ADR:

| Prototype screen       | Already specified / built                                              | Action                                       |
| ---------------------- | ---------------------------------------------------------------------- | -------------------------------------------- |
| User Management        | Org invites, `organization_members`, `users_management.*`              | Reuse. Do not rebuild                        |
| Roles & Permissions    | Org roles + [ADR 0014](./0014-project-level-rbac.md) project roles     | Reuse Home Craft permission codes            |
| Teams                  | [ADR 0010](./0010-project-membership.md) — grouping only, not the gate | Reuse. `project_members` stays authoritative |
| User Sessions          | `sessions_management.view` / `revoke`                                  | Reuse                                        |
| Audit Logs             | `/v1/audit-logs`                                                       | Reuse                                        |
| Tasks / forms / stages | Not specified                                                          | Separate ADR if product asks                 |
| AI employees           | Not specified                                                          | Out of scope                                 |

The prototype's permission catalogue (Cases, Leads, Postcards, Email Builder, `roles.view`, …)
is a CRM-era list. It must **not** replace the seeded Home Craft codes.

### What already exists to hang this on

| Need                      | Existing model                                                                                                                                              |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Who is billed             | The **unit**. [ADR 0017](./0017-fee-configuration.md): no owner / occupant payer                                                                            |
| Outstanding and overdue   | `fee_invoices` (`issued` / `partial`) + `fee_invoice_payments`. `GET /v1/units/{unit_id}/fee-outstanding` already returns the pending-payments total        |
| Due date                  | Snapshot on each invoice (`invoice_date` + `fee_heads.due_within_days` at generation). Issued invoices are immutable                                        |
| Dunning reminders         | `finance_settings` — retry count and pre-due reminder count. Emails only. Does **not** restrict the app                                                     |
| Who uses the resident app | `contacts` with `portal_access`, linked by `contact_units` + `contact_roles` (Owner / Tenant / Family)                                                      |
| Staff vs resident         | Staff JWT + `ensure_staff_project_access`. **Hold checks never run for staff** — not on staff routes, not on a staff session that also lives in a held unit |
| Facility-booking ledger   | Separate invoices and wallets ([ADR 0016](./0016-facility-booking.md)). Not in the outstanding used here                                                    |

### Product decisions from the prototype (keep)

| #   | Decision                                                                                                      |
| --- | ------------------------------------------------------------------------------------------------------------- |
| 1   | One rule per project. Master switch on / off                                                                  |
| 2   | A block is read-only / hold until paid. No configurable "what a block does"                                   |
| 3   | Trigger is amount, days past due, or both. When both are on, staff pick **all** (AND) or **any** (OR)         |
| 4   | Zero outstanding is always clear, even if the rule is on                                                      |
| 5   | Not yet due is never blocked by the rule. Manual **Block now** can still hold a unit ahead of due             |
| 6   | **Release** and **Block now** are per-unit overrides. **Back to rule** deletes the override                   |
| 7   | Saving the rule re-evaluates every unit in the project. Confirm copy names how many would block               |
| 8   | `blocked_on` is a stamp: when the unit first became blocked, and whether that was the rule or a hand override |

### Product decisions that change the prototype (do not copy as-is)

The HTML is a self-contained demo. It invents a live payment window that "re-dates every open
invoice" and treats a "customer" as a person with one amount. That fights the model we already
shipped.

| Prototype                                 | Decision in this ADR                                                                                                              | Why                                                                                                               |
| ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Step 1 "payment window" re-dates invoices | **Do not add a second due date.** Days past due uses each invoice's stored `due_date`                                             | [ADR 0017](./0017-fee-configuration.md): due days live on the fee head; issued invoices stay as issued            |
| Hours as a due unit                       | **Days only**                                                                                                                     | `fee_invoices.due_date` is a date, not a timestamp. Hours were for "booking amounts"; that ledger is out of scope |
| "Customer" (`ATS-1041` + name)            | **The unit** is the subject. Display unit code + primary resident name                                                            | Fees are charged to the unit. One unit, many contacts                                                             |
| One `raised` age for the row              | Oldest unpaid `invoice_date`; days past due is the **max** across unpaid invoices                                                 | A unit can have several open invoices                                                                             |
| Live JS evaluation only                   | Evaluate in the service. Persist `blocked_on` and last status. Re-run on save, payment, issue, override, daily job                | Staff list and the hold screen need a stable stamp, not a flicker on every paint                                  |
| Block the whole app blindly               | Hold is **per unit**. A contact with another clear unit still uses that unit. Writes against a blocked unit return the hold error | Residents can belong to several units ([ADR 0001](./0001-resident-onboarding.md))                                 |

______________________________________________________________________

## Decision

### 1. Two new tables, plus a materialized state row

Do **not** extend `finance_settings`. That row is reminder / retry counts for invoice email.
App access is a different screen, a different permission, and a different writer.

```
projects
  └── app_access_rules          one row per project
  └── app_access_overrides      zero or one row per (project, unit)
  └── app_access_states         one row per unit that has been evaluated
```

| Table                      | Purpose                                                                                   |
| -------------------------- | ----------------------------------------------------------------------------------------- |
| **`app_access_rules`**     | Master switch, amount condition, days condition, AND/OR, optimistic `version`             |
| **`app_access_overrides`** | Staff `hold` or `release` for one unit. Deleting the row returns the unit to the rule     |
| **`app_access_states`**    | Last evaluation: status, outstanding, days past due, `blocked_on`, reason, `evaluated_at` |

`app_access_states` is the staff Customers tab and the resident hold payload. It is not the
rule. The rule plus open invoices plus the override **compute** the state. The daily job and
the write paths (save rule, record payment, issue invoice, set/clear override) refresh it.

### 2. The unit is the billed party; only the resident app is held

**Staff are exempt.** `ensure_resident_unit_not_held` is never called on staff routes. A
staff JWT (`organization_members`) can always operate on a held unit: record a payment, issue
a pass, post a notice, book a facility as staff, manage the rule. Gate security
(`project_members`) is staff. A person who is both staff and a resident is held **only** in
the resident app; their staff session skips the check.

```
Is this request held?

1. If the caller is staff (organization_members / staff API): skip. Never 403 unit_held.
2. If the caller is resident contact C acting on unit U:
   C is the active contact for the session user in the session org
   AND C has an active contact_units row for U (Owner / Tenant / Family)
   AND U's app_access_states.status is `blocked`
   → 403 unit_held (except the resident allow-list in the flow guide)
```

When a **resident** unit is `blocked`:

- `GET /v1/contact-onboarding/status` (and the unit-scoped resident profile) includes an
  `app_access` object so the resident app can show the hold screen **before** a write fails.
  Staff `GET /v1/users/profile` does **not** include this object.
- Resident write routes that take `unit_id` (passes, bookings, marketplace, pets, daily help,
  notices like, events book, …) return **403** `app_access.errors.unit_held`.
- Resident allow-list while held: login, profile / onboarding status, fee outstanding, fee
  invoices, record a payment (resident pay path once it exists).

A contact with unit A held and unit B clear keeps full access on B in the resident app. The
hold screen is tied to the unit they are acting on (default unit when the shell has no unit
in the request).

Who is held on a blocked unit: every active Owner, Tenant, or Family contact with
`portal_access`, **in the resident app only**. Guest / Vendor / Staff `contact_roles` are
not held. `project_members` are not held.

### 3. Outstanding and days past due come from fee invoices only

For unit U as of date D (Asia/Kolkata calendar day, same clock as fee billing):

```
open          = fee_invoices where unit = U, status in (issued, partial)
outstanding   = sum(total_amount - payments) for open, minus unapplied unit credit
                (same figure as GET /units/{id}/fee-outstanding)
days_past_due = max(0, D - due_date) across open invoices that are past due
                (due date itself is not overdue — same as fee_late_fee.days_overdue)
oldest_raised = min(invoice_date) across open, or null when outstanding is 0
```

Facility-booking invoices and wallets are **out**. Club membership in ADR 0016 is a different
ledger. If product later wants bookings in the same hold, that is a new condition, not a silent
join.

### 4. Evaluation formula (matches the prototype's `abEvaluate`)

```
evaluate(unit U):

  if override is hold:     blocked   ("Blocked by hand")
  if override is release:  released  ("Released by hand, rule skipped")
  if outstanding <= 0:     clear     ("Nothing outstanding")
  if days_past_due == 0
     and there is an open invoice
     that is not yet past due:       notdue    (due today or still inside the invoice due date)
  if rule.enabled is false: clear    ("Blocking is switched off")
  if neither by_amount nor by_days:  clear    ("No condition is switched on")

  hit_amount = by_amount AND outstanding >= amount_threshold
  hit_days   = by_days   AND days_past_due > days_threshold
  blocked    = (both on)
                 ? (match_mode == all ? hit_amount AND hit_days
                                      : hit_amount OR  hit_days)
                 : (hit_amount OR hit_days)

  return blocked ? blocked : watch
```

States, same labels as the prototype:

| Status     | Meaning                                           |
| ---------- | ------------------------------------------------- |
| `blocked`  | Hold screen. Rule or hand                         |
| `notdue`   | Open bill, not past `due_date` yet                |
| `watch`    | Past due, but under the amount / days thresholds  |
| `released` | Hand override. Rule would otherwise apply         |
| `clear`    | Nothing outstanding, rule off, or no condition on |

`days_past_due > days_threshold` is **strictly more than**, as in the HTML (`od > daysThreshold`).
Amount is **at or above** the threshold.

Saving a rule with `enabled` true and both conditions off is **422**. Same guard as the
prototype's "Pick a condition first" modal.

### 5. `blocked_on` is a stamp, not a live clock

| Transition                                 | `blocked_on`                             |
| ------------------------------------------ | ---------------------------------------- |
| First time status becomes `blocked` (rule) | `evaluated_at` of that run               |
| Manual hold                                | now, labelled "by hand" on the staff row |
| Status leaves `blocked`                    | cleared to null                          |
| Still blocked on a later run               | **keep** the original stamp              |

The staff "Blocked on" column reads this stamp. "at next run" is only for a dirty unsaved
rule in the UI, not a persisted state.

### 6. When evaluation runs

| Event                                       | What happens                                                                                |
| ------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `PUT` rule saved                            | Re-evaluate every unit in the project                                                       |
| Fee invoice issued or cancelled             | Re-evaluate that unit                                                                       |
| Payment posted (staff or resident)          | Re-evaluate that unit                                                                       |
| Override set or deleted                     | Re-evaluate that unit                                                                       |
| Daily job (same Dkron clock as fee billing) | Re-evaluate units with open invoices or a current state of `blocked` / `watch` / `released` |

Resident requests do **not** recompute the project. They read `app_access_states`. A payment
that clears the unit refreshes the row in that request so the hold lifts without waiting for
the daily job.

### 7. Permissions

New project-scoped codes. The screen sits under Administration in the prototype, but the data
is dues, so seed them next to finance on the default roles.

| Code                  | Use                                          |
| --------------------- | -------------------------------------------- |
| `app_access.view`     | Read the rule and the unit list              |
| `app_access.edit`     | Create / update the rule                     |
| `app_access.override` | **Block now**, **Release**, **Back to rule** |

| Default role       | view | edit | override |
| ------------------ | :--: | :--: | :------: |
| `community_admin`  |  ✓   |  ✓   |    ✓     |
| `accountant`       |  ✓   |  ✓   |    ✓     |
| `facility_manager` |  ✓   |  —   |    —     |
| `viewer`           |  ✓   |  —   |    —     |
| `security`         |  —   |  —   |    —     |

HQ with `projects_management.view` still bypasses assignment ([ADR 0014](./0014-project-level-rbac.md)).
They still need the `app_access.*` org ceiling to call these routes.

Do not reuse `finance_management.admin` for overrides. Generating invoices and holding a
resident's app are different actions.

### 8. API surface

Staff, project-scoped, `ensure_staff_project_access`:

| Method | Path                                                    | Permission | Purpose                           |
| ------ | ------------------------------------------------------- | ---------- | --------------------------------- |
| GET    | `/v1/projects/{id}/app-access/rule`                     | view       | Rule + live impact counts         |
| PUT    | `/v1/projects/{id}/app-access/rule`                     | edit       | Create-or-replace, `version` CAS  |
| GET    | `/v1/projects/{id}/app-access/units`                    | view       | Customers tab                     |
| POST   | `/v1/projects/{id}/app-access/units/{unit_id}/override` | override   | `{ "kind": "hold" \| "release" }` |
| DELETE | `/v1/projects/{id}/app-access/units/{unit_id}/override` | override   | Back to rule                      |

A project with no rule row behaves as `enabled: false` (nobody blocked). First save inserts
the row. There is no delete route.

Resident:

- `app_access` on `GET /v1/contact-onboarding/status` for the default / requested unit.
- `GET /v1/units/{unit_id}/app-access` when the shell already has a unit.
- Helper `ensure_resident_unit_not_held(unit_id)` on **resident** write routes only. Staff
  routers must not import or call it.

Internal: refresh is invoked from fee-billing and payment services. A dedicated
`POST /v1/internal/app-access/evaluate` is optional; the daily fee-billing job can call into
the same service after it issues invoices.

### 9. Rule document

```json
{
  "enabled": true,
  "by_amount": true,
  "amount_threshold": "25000.00",
  "by_days": true,
  "days_threshold": 15,
  "match_mode": "all",
  "version": 1
}
```

No payment-window fields. The prototype's step 1 is already `fee_heads.due_within_days`,
shown on Finance → Fee Configuration. The App Access screen helper copy points there:
days past due are counted from each invoice's due date.

`match_mode` is stored even when only one condition is on; evaluation ignores it until both
are on. Default `all`.

Optimistic concurrency: same `version` compare-and-swap as `finance_settings`.

______________________________________________________________________

## Schema (proposed)

### Enums

```sql
CREATE TYPE public.app_access_status AS ENUM (
  'blocked',
  'notdue',
  'watch',
  'released',
  'clear'
);

CREATE TYPE public.app_access_match_mode AS ENUM (
  'all',
  'any'
);

CREATE TYPE public.app_access_override_kind AS ENUM (
  'hold',
  'release'
);
```

### `app_access_rules`

| Column                      | Type                           | Notes                                              |
| --------------------------- | ------------------------------ | -------------------------------------------------- |
| `id`                        | uuid PK                        |                                                    |
| `organization_id`           | uuid FK → organizations        |                                                    |
| `project_id`                | uuid FK → projects             | Unique                                             |
| `enabled`                   | boolean NOT NULL               | Default false                                      |
| `by_amount`                 | boolean NOT NULL               | Default false                                      |
| `amount_threshold`          | numeric(14, 2) NOT NULL        | ≥ 0, default 0. Ignored when `by_amount` is false  |
| `by_days`                   | boolean NOT NULL               | Default false                                      |
| `days_threshold`            | smallint NOT NULL              | 1–365, default 15. Ignored when `by_days` is false |
| `match_mode`                | app_access_match_mode NOT NULL | Default `all`                                      |
| `version`                   | integer NOT NULL               | Default 1                                          |
| `created_at` / `updated_at` | timestamptz                    |                                                    |
| `updated_by`                | uuid FK → auth.users           | Staff user                                         |

**Checks:** `enabled` true requires `by_amount OR by_days`. Amount ≥ 0. Days 1–365. Version ≥ 1.

### `app_access_overrides`

| Column            | Type                     | Notes                    |
| ----------------- | ------------------------ | ------------------------ |
| `id`              | uuid PK                  |                          |
| `organization_id` | uuid                     |                          |
| `project_id`      | uuid                     |                          |
| `unit_id`         | uuid FK → units          | Unique with `project_id` |
| `kind`            | app_access_override_kind |                          |
| `set_by`          | uuid                     | Staff user               |
| `set_at`          | timestamptz              |                          |

No note column in v1. Prototype confirm copy is enough.

### `app_access_states`

| Column                | Type              | Notes                                      |
| --------------------- | ----------------- | ------------------------------------------ |
| `project_id`          | uuid              | PK with `unit_id`                          |
| `unit_id`             | uuid              |                                            |
| `organization_id`     | uuid              |                                            |
| `status`              | app_access_status |                                            |
| `outstanding_amount`  | numeric(14, 2)    |                                            |
| `days_past_due`       | integer           | 0 when not overdue; null when no open bill |
| `oldest_invoice_date` | date              | null when no open bill                     |
| `blocked_on`          | timestamptz       | null unless status is `blocked`            |
| `blocked_by`          | text              | `rule` or `hand`, null unless blocked      |
| `reason`              | text              | Staff-visible why string                   |
| `evaluated_at`        | timestamptz       |                                            |

Index: `(organization_id, project_id, status)` for the Customers tab and impact counts.

______________________________________________________________________

## Implementation phases

| Phase | Scope                                                                                                                                       | Status  |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| **1** | This ADR. Migrations. Rule CRUD. Evaluate from fee invoices. Staff units list + overrides                                                   | Planned |
| **2** | Materialize state on invoice issue and payment. `blocked_on` stamp                                                                          | Planned |
| **3** | Resident `app_access` on status. `ensure_resident_unit_not_held` on **resident** write routes only. Hold error. Staff routes stay unchecked | Planned |
| **4** | Daily job hook from fee billing. Confirm-on-save impact counts                                                                              | Planned |

The build contract is [0019-app-access-control-flow.md](../0019-app-access-control-flow.md).
Do not start implementation from the HTML file's in-memory `STORE`.

______________________________________________________________________

## Consequences

### Positive

- Dues already live on `fee_invoices`. The hold does not invent a second ledger or a second due date.
- Unit-scoped hold matches ADR 0017 (the bill is the unit's) and ADR 0001 (a contact can have many units).
- Overrides are explicit rows, so "released by hand" survives a rule change until staff click Back to rule.
- `blocked_on` is stable across daily runs, which is what the Customers column needs.
- Existing Users / Roles / Teams / Sessions / Audit screens stay as they are. This ADR does not reopen membership.

### Negative / trade-offs

- The staff UI will not have the prototype's step-1 payment window. Due dates stay on Fee Configuration. The two screens must be linked in copy so admins are not looking for a window here.
- Hours-granularity holds (club booking amounts, same-day) are not possible until a ledger with timestamps is in scope.
- Facility-booking debt will not hold the app. A resident can owe a court fee and still have full access.
- Every resident write route needs the hold helper. Missing one is a leak. Staff routes must
  never gain that helper — a dual-role user would then be locked out of admin work.
- First save of a rule on a large project re-evaluates every unit with an open invoice. That work belongs in the service (or a queued job), not the HTTP request, if the society is large.

### Follow-ups

1. Flow guide: screen → route map, hold-screen JSON, i18n keys, confirm copy.
1. Wire `ensure_resident_unit_not_held` through **resident** modules only (passes, events,
   marketplace, pets, daily help, facility booking, notices writes). Staff routers stay
   unchecked. Tests: staff 200 on a held unit; resident 403.
1. Hook evaluate into `FeeBillingService` issue and payment paths.
1. Decide whether a **partial** payment that drops outstanding below the amount threshold lifts the hold immediately (this ADR: **yes**, re-evaluate on payment).
1. Tasks / AI employees in the same HTML file get their own ADR if product wants them.

______________________________________________________________________

## Alternatives considered

| Alternative                                            | Why rejected                                                                                             |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- |
| Put the rule on `finance_settings`                     | Different screen, different permission, different writer. Reminder counts are not a hold                 |
| Prototype payment window as a live re-date of invoices | Breaks ADR 0017 immutability. Late fees already use stored `due_date`                                    |
| Block the contact, not the unit                        | The bill is the unit's. An owner of two flats would be held for the paid one                             |
| Run the hold on staff sessions too                     | Community admin, accountant, and gate must keep working the held unit (pay, pass, notice)                |
| Block the whole resident login                         | Multi-unit contacts would lose the clear unit. They also could not pay                                   |
| Flip `contacts.portal_access` to false                 | Portal access is household onboarding, not dues. Restoring it would fight the household toggle           |
| Compute only at request time, no `app_access_states`   | `blocked_on` and the staff table would change on every paint. Impact counts would scan all invoices live |
| Include facility-booking outstanding                   | Separate ledger. Prototype copy is maintenance / construction-linked installments                        |
| Hours due unit                                         | Invoice dates are calendar days. Short-cycle booking dues are out of this flow                           |
| Reuse `finance_management.*` only                      | Holding the app is not "run billing". Overrides need their own code                                      |
| Rebuild Users / Roles / Teams from the HTML catalogue  | Those screens and permission keys already exist in Home Craft. The HTML list is CRM leftovers            |

______________________________________________________________________

## Open questions

| #   | Question                                                                                           | Default until the client answers                                      |
| --- | -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 1   | Does a hold also hide **reads** of notices, events, marketplace, or only writes plus a hold shell? | Hold shell + write 403. GET outstanding / invoices / status stay open |
| 2   | Should Family on a let-out unit be held, or only Owner / Tenant?                                   | Owner, Tenant, **and** Family with `portal_access`                    |
| 3   | Is the daily evaluate a new Dkron job or a step at the end of fee billing?                         | Step at the end of the existing fee-billing job                       |
| 4   | Large-project rule save: wait on HTTP, or 202 + job?                                               | HTTP if under a few thousand open-invoice units; otherwise 202        |
