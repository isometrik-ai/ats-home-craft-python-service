# ADR 0014: Community events — admin create, resident book, manual payment

|                  |                                                                                                                                                                                                                                 |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Accepted                                                                                                                                                                                                                        |
| **Date**         | 2026-08-20                                                                                                                                                                                                                      |
| **Authors**      | Home Craft platform team                                                                                                                                                                                                        |
| **Depends on**   | [ADR 0011](./0011-project-membership.md) (project scoping), [ADR 0009](./0009-push-notifications-grpc.md) (push, Phase 2), project setup `facilities` ([project-setup-flow.md](../project-setup-flow.md))                       |
| **Related docs** | [events-flow.md](../events-flow.md), [community-events-schema.md](../../../ats-home-craft-supabase/docs/community-events-schema.md)                                                                                             |
| **Migrations**   | `20260820120000_community_events_enums.sql`, `20260820121000_community_events_tables.sql`, `20260820130000_community_events_phase2.sql`, `20260821180000_community_events_drop_booking_unit_id.sql` (`ats-home-craft-supabase`) |

______________________________________________________________________

## Context

Community admins need to create and manage **project-scoped events** — free or paid — and residents
book tickets from the mobile app. Product UI spans:

1. **Admin dashboard — Community → Events** — create, list, filter, export, cancel/complete, soft-delete,
   view bookings, and **manually mark paid** (no payment gateway in Phase 1).
1. **Resident mobile — Events** — browse upcoming/past events, filter by category, view detail, book tickets,
   and view existing bookings / gate tickets.

### Product rules (from screens)

| Area                  | Behaviour                                                                                                                                        |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Basics**            | Event name, category, multi-day toggle, start/end date, optional start/end time, optional total capacity (ticket count)                          |
| **Ticketing**         | Free or Paid; max tickets per resident; booking closes date + time                                                                               |
| **Paid pricing**      | Adult price (INR); child ticket mode (not applicable / free / priced); optional tax on ticket price (`apply_tax`, default 18%)                   |
| **Venue**             | Facility picker from **Project Setup → Facilities** — only **bookable venues** (`facility_type IN events, sports, recreation, services`, active) |
| **Media**             | Cover image + optional gallery (JPG/PNG paths via presigned upload)                                                                              |
| **Publish lifecycle** | Draft → Published; admin may Complete or Cancel; soft Delete (record status) separate from publish status                                        |
| **Capacity**          | Counted in **tickets** (adults + children), not bookings; waitlist opens when full (Phase 1: store waitlisted rows; auto-promote in Phase 2)     |
| **Payment**           | No online gateway — resident books → `payment_status = pending`; admin marks paid offline on booking                                             |
| **Admin metrics**     | Total events, upcoming (published + future end), total RSVPs (ticket count), revenue collected (paid bookings, calendar year)                    |
| **Resident UX**       | Upcoming/Past tabs; category chips; search; progress bar (booked/capacity); Book / Closed / View tickets CTA                                     |

### Naming note

Use table prefix **`community_events`** — not `events` — to avoid confusion with existing
`move_events`, `pass_events`, and `daily_help_events`.

### Membership alignment ([ADR 0011](./0011-project-membership.md))

- Events are **project-scoped** (`organization_id` + `project_id`).
- **Staff admin** routes: org RBAC + `ensure_staff_project_access(project_id)`.
- **Resident** routes: `extract_onboarding_contact_context()` + active `contact_units` membership
  in the project. Bookings are tied to **contact + project** only — no `unit_id` on booking rows or APIs.

______________________________________________________________________

## Decision

### 1. Four new tables (Phase 1)

| Table                           | Purpose                                                                                                 |
| ------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **`community_events`**          | Event header: schedule, category, venue, ticketing, pricing, publish + record status, stats             |
| **`community_event_media`**     | Gallery images (cover stored on header; gallery rows here)                                              |
| **`community_event_bookings`**  | Resident booking: contact, adult/child ticket counts, amount, payment + booking status (project-scoped) |
| **`community_event_audit_log`** | Append-only admin/resident actions (created, published, cancelled, marked_paid, …)                      |

Aggregates on `community_events` (`tickets_booked`, `bookings_count`, `paid_bookings_count`,
`revenue_collected_minor`) are **maintained by the service** on booking create/cancel/mark-paid for
list performance — same pattern as notice view/like counts.

### 2. Dual status model (publish vs record)

| Field            | Values                                         | Meaning                                            |
| ---------------- | ---------------------------------------------- | -------------------------------------------------- |
| `publish_status` | `draft`, `published`, `completed`, `cancelled` | Product “Publish Status” badge                     |
| `record_status`  | `active`, `deleted`                            | Product “Record Status”; soft delete retains audit |

- **Draft** — editable; not visible to residents.
- **Published** — visible; bookings allowed until `booking_closes_at` or capacity/waitlist rules.
- **Completed** — event ended; set manually or by nightly job when `end_at < now()`.
- **Cancelled** — admin cancelled; existing bookings retained; resident UI shows cancelled.
- **Deleted** — soft delete (`record_status = deleted`); hidden from default admin/resident lists.

Published events have **limited edit** — only safe fields (e.g. description, gallery, booking deadline
extension) or require cancel + recreate; full immutability is a follow-up product call. Phase 1:
**block structural edits** (dates, pricing, capacity) once `publish_status = published` and
`tickets_booked > 0` → `409 event_not_editable`.

### 3. Venue = existing `facilities` row

- `community_events.facility_id` → `facilities.id`.
- Picker lists facilities where `facility_type IN ('events', 'sports', 'recreation', 'services')`,
  `status = 'active'`, `active = true` (matches UI copy: “Only bookable venues are listed”).
- Resident detail **Venue** section joins facility name, location type, tower/floor/wing, subtype,
  `location_notes` — no snapshot on the event row.

### 4. Ticketing and pricing

| Concept          | Storage / rule                                                                      |
| ---------------- | ----------------------------------------------------------------------------------- |
| Event type       | `event_type`: `free` \| `paid`                                                      |
| Adult price      | `adult_price_minor` (0 for free)                                                    |
| Child tickets    | `child_ticket_mode`: `not_applicable` \| `free` \| `priced`                         |
| Child price      | `child_price_minor` when mode = `priced`                                            |
| Tax              | `apply_tax boolean`, `tax_rate numeric DEFAULT 18` — applied to subtotal at booking |
| Max per resident | `max_tickets_per_resident` (UI default 4)                                           |
| Total capacity   | `total_capacity` nullable — NULL = unlimited; else sum of ticket counts             |
| Booking window   | `booking_closes_at timestamptz` — after this, booking status = closed               |

**Amount at booking time** (stored on `community_event_bookings`):

```text
subtotal = adult_count × adult_price + child_count × child_price   (respect child mode)
tax      = apply_tax ? round(subtotal × tax_rate / 100) : 0
total    = subtotal + tax
```

Free events: `payment_status = not_applicable`, `total_amount_minor = 0`.

### 5. Bookings and manual payment (Phase 1)

| `booking_status` | When                                                        |
| ---------------- | ----------------------------------------------------------- |
| `confirmed`      | Capacity available; counts toward `tickets_booked`          |
| `waitlisted`     | Capacity full; does **not** count toward capacity (Phase 1) |
| `cancelled`      | Resident or admin cancelled                                 |

| `payment_status` | When                                             |
| ---------------- | ------------------------------------------------ |
| `not_applicable` | Free event                                       |
| `pending`        | Paid event; booking confirmed/waitlisted         |
| `paid`           | Admin marked paid (`paid_at`, `paid_by_user_id`) |
| `waived`         | Admin waived charge (optional Phase 1b)          |

Admin action:

```http
POST /v1/projects/{project_id}/community-events/{event_id}/bookings/{booking_id}/mark-paid
```

Revenue on admin dashboard = sum of `total_amount_minor` where `payment_status = paid` and booking not cancelled.

Admin **mark-paid** / **mark-waived** after offline collection. No online payment integration.

### 6. Display codes

- Event: `EVT-{sequence_number}` per project (monotonic), immutable.
- Booking: `BKG-{sequence_number}` per project.

### 7. API surface

**Admin prefix:** `/v1/projects/{project_id}/community-events`

Key endpoints: summary, list, CRUD, publish, cancel, complete, delete/restore, bookings list,
mark-paid, export.

**Resident prefix:** `/v1/projects/{project_id}/resident/community-events`

Key endpoints: list (upcoming/past), detail, book, my-bookings, cancel booking, ticket view,
gate QR verify. **`project_id` is mandatory in the path** on every resident endpoint.

Proposed RBAC: `community_events_management.*` or reuse `projects_management.*` (align with notices).

### 8. Booking eligibility (service rules)

Resident may book when **all** hold:

1. `publish_status = published` and `record_status = active`
1. `now() < booking_closes_at`
1. `now() < event end_at` (derived from dates + times)
1. Requested ticket count ≤ remaining capacity (or → waitlist if enabled)
1. Requested ticket count ≤ `max_tickets_per_resident` minus existing active tickets for same contact+event
1. Contact has active project membership (any active `contact_units` row in the project)

______________________________________________________________________

## Schema (proposed)

See [community-events-schema.md](../../../ats-home-craft-supabase/docs/community-events-schema.md).

### Enums (Postgres)

```sql
community_event_category          → social, workshop, sports, cultural, agm
community_event_type              → free, paid
community_event_child_ticket_mode → not_applicable, free, priced
community_event_publish_status    → draft, published, completed, cancelled
community_event_record_status     → active, deleted
community_event_booking_status    → confirmed, waitlisted, cancelled
community_event_payment_status    → not_applicable, pending, paid, waived
```

______________________________________________________________________

## Consequences

### Positive

- Reuses project setup **facilities** — no duplicate venue catalog.
- Manual offline payment is the product model; no online payment gateway is planned.
- Clear separation of publish vs record status matches admin table columns.
- Ticket-level capacity aligns with adult/child UI (4A · 2C).

### Negative / trade-offs

- Denormalized aggregates must stay consistent in transactions (booking create/cancel/mark-paid).
- Waitlist in Phase 1 stores rows but does not auto-promote on cancellation — admin/resident messaging
  should set expectations until Phase 2 job exists.
- Limited edit rules on published events need careful UX copy.

### Follow-ups

1. Migrations + RLS-deferred policies in `ats-home-craft-supabase`.
1. Admin + resident APIs in `user_service`.
1. Nightly job: `published` → `completed` when past end; optional booking-close reminders (push).
1. Phase 2: Waitlist promotion, gate QR on booking ticket.
1. Optional link to **Notices** category `event` for cross-promotion (not coupled in Phase 1).

______________________________________________________________________

## Alternatives considered

| Alternative                       | Rejected because                                              |
| --------------------------------- | ------------------------------------------------------------- |
| Table name `events`               | Collides with move/pass/daily-help event tables               |
| Snapshot venue on event row       | Facility updates should reflect on detail; join is sufficient |
| Payment gateway in Phase 1        | Explicit product decision — admin mark paid first             |
| `unit_id` on bookings             | Events are project-scoped; contact identity is sufficient     |
| Single combined status enum       | UI shows Publish Status and Record Status separately          |
| Child table for ticket line items | Only adult/child tiers in UI; counts on booking row enough    |
