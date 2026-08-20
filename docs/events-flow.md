# Community Events Flow — Context & Change Guide

> **Status: Implemented.** Admin API, resident booking, manual mark-paid, export, complete-past job,
> waitlist promotion, push notifications, gate QR verification, and reminder cron
> are live in `user_service`. See [community-events-schema.md](../../ats-home-craft-supabase/docs/community-events-schema.md)
> for migrations.
>
> Schema and architecture rationale: [ADR 0014](./adr/0014-community-events.md), [ADR 0011](./adr/0011-project-membership.md) (project access), [community-events-schema.md](../../ats-home-craft-supabase/docs/community-events-schema.md).

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **Admin API prefix:** `/v1/projects/{project_id}/community-events`
- **Resident API prefix:** `/v1/community-events`
- **DB schema:** `ats-home-craft-supabase` (migrations `20260820120000`–`20260820130000`)
- **Venue source:** existing `facilities` from [project-setup-flow.md](./project-setup-flow.md) (`facility_type IN events, sports, recreation, services`)

______________________________________________________________________

## 1. What this flow does

A **community admin** creates **free or paid events** for a **project** (gated community): social
gatherings, workshops, sports meets, cultural programs, AGMs, etc. Residents browse events on mobile,
book tickets (adult and/or child counts), and view their bookings at the gate.

**Phase 1 payment model:** there is **no payment gateway**. For paid events, the resident books with
`payment_status = pending`; the admin **marks the booking paid** after collecting payment offline
(cash, UPI, etc.). Revenue metrics on the admin dashboard reflect **paid** bookings only.

Each event has:

- **Basics** — title, category, multi-day flag, start/end date, optional times, optional total capacity.
- **Ticketing** — free or paid; max tickets per resident; booking closes date/time.
- **Paid pricing** — adult price; child ticket mode (not applicable / free / priced); optional tax (`apply_tax`, default 18%).
- **Venue** — a **facility** from project setup (`facility_type IN events, sports, recreation, services`, active).
- **About & media** — description, cover image, optional gallery.

### Business rules (must enforce)

| Rule                               | Enforcement                                                                                                                 |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **Project-scoped**                 | All queries filter `organization_id` + `project_id`                                                                         |
| **Venue from facilities**          | `facility_id` must reference active `facilities` row with `facility_type IN ('events', 'sports', 'recreation', 'services')` |
| **Draft not visible to residents** | List/detail resident APIs require `publish_status = published`, `record_status = active`                                    |
| **Booking window**                 | `now() < booking_closes_at` and event not ended                                                                             |
| **Capacity in tickets**            | `total_tickets` per booking = adult + child; sum confirmed tickets ≤ `total_capacity`                                       |
| **Waitlist when full**             | If capacity exhausted → `booking_status = waitlisted` (Phase 1: no auto-promote)                                            |
| **Per-resident cap**               | Sum of active tickets for `(event_id, contact_id)` ≤ `max_tickets_per_resident`                                             |
| **Child tickets**                  | `child_tickets > 0` only when `child_ticket_mode != not_applicable`                                                         |
| **Free events**                    | `payment_status = not_applicable`, amounts zero                                                                             |
| **Paid events Phase 1**            | Book → `payment_status = pending`; admin `mark-paid` → `paid`                                                               |
| **Amount immutability**            | Prices stored on booking row at create time                                                                                 |
| **Tax**                            | When `apply_tax`, `tax_minor = round(subtotal × tax_rate / 100)`                                                            |
| **Published edit lock**            | Structural fields locked when published and `tickets_booked > 0` → `409`                                                    |
| **Soft delete**                    | `record_status = deleted`; retain bookings and audit                                                                        |
| **Display codes**                  | `EVT-{n}` events, `BKG-{n}` bookings — monotonic per project                                                                |
| **Staff project access**           | `ensure_staff_project_access(project_id)` on admin routes                                                                   |
| **Resident unit context**          | `unit_id` required on book; active Owner/Tenant on unit                                                                     |

### Screen → capability map

**Admin dashboard (Community → Events)**

| Screen / action                                                         | Capability                                                                                                                       |
| ----------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Summary cards (Total / Upcoming / RSVPs / Revenue)                      | `GET /projects/{project_id}/community-events/summary`                                                                            |
| Status tabs (All / Draft / Published / Completed / Cancelled / Deleted) | `GET /projects/{project_id}/community-events?publish_status=&record_status=`                                                     |
| Search by name                                                          | `GET ...?search=`                                                                                                                |
| Events table                                                            | List response with aggregates                                                                                                    |
| + New Event drawer                                                      | `POST /projects/{project_id}/community-events`                                                                                   |
| Save as draft                                                           | `POST` with `{ "publish_mode": "draft" }`                                                                                        |
| Create & publish                                                        | `POST` with `{ "publish_mode": "publish" }`                                                                                      |
| View event                                                              | `GET /projects/{project_id}/community-events/{id}`                                                                               |
| Edit (draft or no bookings)                                             | `PATCH /projects/{project_id}/community-events/{id}`                                                                             |
| Publish draft                                                           | `POST /projects/{project_id}/community-events/{id}/publish`                                                                      |
| Mark completed                                                          | `POST /projects/{project_id}/community-events/{id}/complete`                                                                     |
| Cancel event                                                            | `POST /projects/{project_id}/community-events/{id}/cancel`                                                                       |
| Soft delete                                                             | `POST /projects/{project_id}/community-events/{id}/delete`                                                                       |
| Restore                                                                 | `POST /projects/{project_id}/community-events/{id}/restore`                                                                      |
| Event bookings list                                                     | `GET /projects/{project_id}/community-events/{id}/bookings`                                                                      |
| Mark booking paid                                                       | `POST .../bookings/{booking_id}/mark-paid`                                                                                       |
| Mark waived (optional)                                                  | `POST .../bookings/{booking_id}/mark-waived`                                                                                     |
| Facility picker                                                         | `GET /projects/{project_id}/facilities?facility_types=events,sports,recreation,services&status=active` (existing, extend filter) |
| Export                                                                  | `GET /projects/{project_id}/community-events/export`                                                                             |
| Upload cover / gallery                                                  | Presigned URL → paths in create/PATCH body                                                                                       |

**Resident mobile (Events tab)**

| Screen / action               | Capability                                                                 |
| ----------------------------- | -------------------------------------------------------------------------- |
| Events home (Upcoming / Past) | `GET /community-events?project_id=&timeframe=upcoming\|past`               |
| Category chips                | `GET /community-events?project_id=&category=`                              |
| Search                        | `GET /community-events?project_id=&search=`                                |
| My ticket badge count         | `GET /community-events/my-bookings/summary?unit_id=`                       |
| Event card list               | List item: title, category, price label, date, venue, booked/capacity, CTA |
| Event detail                  | `GET /community-events/{id}?unit_id=`                                      |
| Book tickets                  | `POST /community-events/{id}/bookings?unit_id=`                            |
| View my tickets               | `GET /community-events/{id}/my-booking?unit_id=`                           |
| Cancel booking                | `POST /community-events/bookings/{booking_id}/cancel?unit_id=`             |
| All my bookings               | `GET /community-events/my-bookings?unit_id=`                               |

______________________________________________________________________

## 2. Architecture (layers)

Same 3-layer FastAPI pattern as the rest of the service:

```
HTTP → API router → CommunityEventsService (business rules) → CommunityEventsRepository (SQL) → Postgres
         ↓
  ensure_staff_project_access(project_id)          [admin routes]
  extract_onboarding_contact_context()             [resident routes]
```

### File map (to create)

| Concern                     | File                                                             |
| --------------------------- | ---------------------------------------------------------------- |
| Admin API endpoints         | `app/api/community_events.py`                                    |
| Resident API endpoints      | `app/api/community_events_resident.py`                           |
| Route registration          | `app/api/routes.py`                                              |
| Orchestration               | `app/services/community_events_service.py`                       |
| Booking + pricing logic     | `app/services/community_event_booking_service.py`                |
| Persistence                 | `app/db/repositories/community_events_repository.py`             |
| Request/response models     | `app/schemas/community_events.py`                                |
| Enums (mirror Postgres)     | `app/schemas/enums.py`                                           |
| Admin RBAC + project access | Reuse `projects_management.*` or `community_events_management.*` |
| Presigned uploads           | Reuse `app/api/presigned_url.py`                                 |
| Complete past events job    | `app/jobs/complete_past_community_events.py`                     |
| Audit logging               | `@audit_api_call` + `community_event_audit_log` inserts          |
| i18n                        | `app/locales/en.json` (`community_events.*`)                     |
| Push (Phase 2)              | New event published, booking confirmed, event reminder           |

`CommunityEventsService` delegates booking create/cancel/mark-paid to
`CommunityEventBookingService` to keep capacity and payment aggregates in one transaction.

______________________________________________________________________

## 3. Data model

### New tables (4)

| Table                           | Purpose                                                                                         |
| ------------------------------- | ----------------------------------------------------------------------------------------------- |
| **`community_events`**          | Event header: schedule, category, venue FK, ticketing, pricing, dual status, denormalized stats |
| **`community_event_media`**     | Gallery image rows (`file_path`, metadata, `sort_order`)                                        |
| **`community_event_bookings`**  | Resident booking: unit, contact, adult/child counts, amounts, payment + booking status          |
| **`community_event_audit_log`** | Append-only audit trail                                                                         |

Full column reference: [community-events-schema.md](../../ats-home-craft-supabase/docs/community-events-schema.md).

### Reused tables

| Table                  | Role                                                                  |
| ---------------------- | --------------------------------------------------------------------- |
| `projects`             | Community scope                                                       |
| `facilities`           | Event venue (`facility_type IN events, sports, recreation, services`) |
| `towers`               | Venue location label (join via facility)                              |
| `units`                | Resident booking context                                              |
| `contacts`             | Booker; admin `created_by` via user linkage                           |
| `contact_units`        | Active residency validation                                           |
| `contact_roles`        | Owner/Tenant eligibility on `unit_id`                                 |
| `organization_members` | Staff admin access gate                                               |

### Status lifecycles

**Publish status** (`publish_status`):

```text
                    ┌─────────┐
                    │  draft  │◄──── restore (new draft copy optional)
                    └────┬────┘
                         │ publish
                         ▼
                    ┌───────────┐
         cancel ──► │ published │ ──► completed (manual or job when past end)
                    └─────┬─────┘
                          │ cancel
                          ▼
                    ┌───────────┐
                    │ cancelled │
                    └───────────┘
```

**Record status** (`record_status`): `active` ↔ `deleted` (soft delete independent of publish status).

**Booking status**:

```text
book (capacity available)  ──► confirmed
book (capacity full)       ──► waitlisted     [Phase 2: promote on cancel]
resident/admin cancel      ──► cancelled
```

**Payment status (paid events only)**:

```text
book ──► pending ──admin mark-paid──► paid
              └──admin mark-waived──► waived   [optional]
```

### Category values (`community_event_category`)

| Key        | UI label |
| ---------- | -------- |
| `social`   | Social   |
| `workshop` | Workshop |
| `sports`   | Sports   |
| `cultural` | Cultural |
| `agm`      | AGM      |

### Admin tab behaviour

| Tab           | Query filter                                           |
| ------------- | ------------------------------------------------------ |
| **All**       | `record_status = active`                               |
| **Draft**     | `publish_status = draft`, `record_status = active`     |
| **Published** | `publish_status = published`, `record_status = active` |
| **Completed** | `publish_status = completed`, `record_status = active` |
| **Cancelled** | `publish_status = cancelled`, `record_status = active` |
| **Deleted**   | `record_status = deleted`                              |

### Summary cards (admin)

| Card                  | Derivation                                                                     |
| --------------------- | ------------------------------------------------------------------------------ |
| **Total Events**      | Count where `record_status = active` (all publish statuses)                    |
| **Upcoming**          | `publish_status = published` AND `end_date >= today` (or `end_at >= now()`)    |
| **Total RSVPs**       | Sum `tickets_booked` across active published/upcoming events                   |
| **Revenue Collected** | Sum `revenue_collected_minor` where paid bookings in **current calendar year** |

______________________________________________________________________

## 4. Admin flow (step by step)

### 4.1 Preconditions

- Staff logged in (JWT → `organization_members`).
- Session org matches project's `organization_id`.
- `ensure_staff_project_access(project_id)` passes.
- Permission: `projects_management.*` or `community_events_management.*` (TBD).
- At least one **event facility** exists in project setup (Step 7 — Facilities with type Events).

### 4.2 List page load

1. Admin opens **Community → Events** for active project.
1. `GET /v1/projects/{project_id}/community-events/summary` → tab counts + summary cards.

**Summary response (example):**

```json
{
  "total_events": 8,
  "upcoming": 5,
  "total_rsvps": 13,
  "revenue_collected_minor": 361000,
  "revenue_currency": "INR",
  "tabs": {
    "all": 8,
    "draft": 0,
    "published": 5,
    "completed": 2,
    "cancelled": 1,
    "deleted": 1
  }
}
```

3. Default tab **All** → `GET /community-events?publish_status=all&page=1&page_size=20`.

**List item shape (example row — Pottery Workshop):**

```json
{
  "id": "uuid",
  "display_code": "EVT-12",
  "title": "Pottery Workshop for Kids",
  "category": "workshop",
  "start_date": "2026-08-19",
  "end_date": "2026-08-19",
  "is_multi_day": false,
  "event_type": "paid",
  "facility_name": "Activity Room",
  "facility_location_label": "Tower B — Le Marquee, Ground Floor",
  "bookings_count": 2,
  "tickets_booked": 5,
  "total_capacity": 25,
  "ticket_breakdown": { "adult": 2, "child": 3 },
  "paid_bookings_count": 1,
  "revenue_collected_minor": 58900,
  "publish_status": "published",
  "record_status": "active",
  "booking_state": "closed"
}
```

### 4.3 Create event

#### Section — Basics

| Field           | Required | Notes                                                         |
| --------------- | -------- | ------------------------------------------------------------- |
| Event name      | Yes      | `title`                                                       |
| Multi-day event | No       | `is_multi_day`; when false, `end_date` may equal `start_date` |
| Start date      | Yes      |                                                               |
| End date        | Yes      | ≥ start date                                                  |
| Start time      | No       | e.g. `11:00 AM` → stored as `time`                            |
| End time        | No       |                                                               |
| Category        | Yes      | enum dropdown                                                 |
| Total capacity  | No       | Max **tickets**; null = unlimited; waitlist when full         |

#### Section — Ticketing

| Field                        | Required           | Notes               |
| ---------------------------- | ------------------ | ------------------- |
| Event type                   | Yes                | `free` or `paid`    |
| Max tickets per person       | Yes when published | default 4           |
| Booking closes — date + time | Yes when published | `booking_closes_at` |

When **Paid** selected:

| Field             | Required    | Notes                                      |
| ----------------- | ----------- | ------------------------------------------ |
| Adult price (INR) | Yes         | stored as `adult_price_minor`              |
| Child ticket      | Yes         | `not_applicable` \| `free` \| `priced`     |
| Child price       | When priced |                                            |
| Apply tax         | No          | `apply_tax` toggle; `tax_rate` default 18% |

#### Section — Venue

| Field    | Required           | Notes                                                                                       |
| -------- | ------------------ | ------------------------------------------------------------------------------------------- |
| Facility | Yes when published | Searchable dropdown; only `facility_type IN (events, sports, recreation, services)`, active |

#### Section — About & media

| Field            | Required | Notes                 |
| ---------------- | -------- | --------------------- |
| About this event | No       | `description`         |
| Cover image      | No       | presigned upload path |
| Media gallery    | No       | up to 10 images       |

#### Create request (publish — paid event)

```http
POST /v1/projects/{project_id}/community-events
Authorization: Bearer <staff_jwt>
Content-Type: application/json

{
  "title": "Pottery Workshop for Kids",
  "description": "Hands-on pottery session for ages 6–12. Materials included.",
  "category": "workshop",
  "is_multi_day": false,
  "start_date": "2026-08-19",
  "end_date": "2026-08-19",
  "start_time": "16:00:00",
  "end_time": "18:00:00",
  "event_type": "paid",
  "total_capacity": 25,
  "max_tickets_per_resident": 3,
  "booking_closes_at": "2026-08-17T18:00:00+05:30",
  "adult_price_minor": 58900,
  "child_ticket_mode": "free",
  "apply_tax": true,
  "tax_rate": 18.00,
  "facility_id": "facility-uuid",
  "cover_image_path": "org/.../cover.jpg",
  "gallery": [
    {
      "file_path": "org/.../gallery1.jpg",
      "file_name": "gallery1.jpg",
      "mime_type": "image/jpeg",
      "size_bytes": 120000,
      "sort_order": 0
    }
  ],
  "publish_mode": "publish"
}
```

#### Create request (draft — free event)

```http
POST /v1/projects/{project_id}/community-events
{
  "title": "Independence Day Celebration",
  "category": "social",
  "start_date": "2026-08-15",
  "end_date": "2026-08-15",
  "start_time": "08:00:00",
  "end_time": "11:00:00",
  "event_type": "free",
  "publish_mode": "draft"
}
```

Drafts may omit facility, capacity, and booking deadline until ready.

#### Service transaction (create)

1. `ensure_staff_project_access(project_id)`.
1. Validate dates, pricing rules, facility eligibility.
1. If `publish_mode = publish`: require facility, `booking_closes_at`, max tickets per resident.
1. Allocate `sequence_number` + `display_code` (`EVT-{n}`) in transaction.
1. Insert `community_events` with `publish_status = draft` or `published`.
1. Replace `community_event_media` rows.
1. Insert audit log `created` / `published`.
1. Return full event detail.

### 4.4 Bookings management & mark paid

Admin opens event detail → **Bookings** tab:

```http
GET /v1/projects/{project_id}/community-events/{event_id}/bookings?payment_status=&booking_status=
```

**Mark paid (Phase 1 — no gateway):**

```http
POST /v1/projects/{project_id}/community-events/{event_id}/bookings/{booking_id}/mark-paid
{
  "payment_notes": "Collected cash at clubhouse"
}
```

Service transaction:

1. Verify event is paid type; booking `payment_status = pending`.
1. Set `payment_status = paid`, `paid_at = now()`, `paid_by_user_id`.
1. Increment event `paid_bookings_count` and `revenue_collected_minor`.
1. Audit log `marked_paid`.

Admin list **Paid** column = `paid_bookings_count / bookings_count` (non-cancelled).
**Revenue** column = `revenue_collected_minor` for the event.

### 4.5 Cancel / complete / delete

| Action       | Endpoint            | Effect                                          |
| ------------ | ------------------- | ----------------------------------------------- |
| Cancel event | `POST .../cancel`   | `publish_status = cancelled`; bookings retained |
| Complete     | `POST .../complete` | `publish_status = completed`                    |
| Soft delete  | `POST .../delete`   | `record_status = deleted`, `deleted_at` set     |
| Restore      | `POST .../restore`  | `record_status = active`                        |

Nightly job (optional Phase 1b): auto-complete published events where `end_at < now()`.

______________________________________________________________________

## 5. Resident flow (step by step)

### 5.1 Preconditions

- Resident logged in via onboarding contact context.
- Client passes `unit_id` for an active unit in the project.
- Contact has active Owner or Tenant role on the unit ([ADR 0010](./adr/0010-contact-roles.md)).

### 5.2 Events home

```http
GET /v1/community-events?project_id={project_id}&unit_id={unit_id}&timeframe=upcoming&category=social&search=
```

**List item (example — Independence Day):**

```json
{
  "id": "uuid",
  "title": "Independence Day Celebration",
  "category": "social",
  "price_label": "Free",
  "start_date": "2026-08-15",
  "start_time": "08:00:00",
  "end_time": "11:00:00",
  "is_multi_day": false,
  "facility_name": "Central Lawn",
  "location_label": "Open Grounds, Ground Floor",
  "tickets_booked": 184,
  "total_capacity": 250,
  "booking_state": "closed",
  "my_tickets_count": 0,
  "cta": "closed"
}
```

**`booking_state` (derived):**

| Value    | Condition                                                   |
| -------- | ----------------------------------------------------------- |
| `open`   | Published, before `booking_closes_at`, capacity available   |
| `closed` | Past deadline, full, cancelled, completed, or not published |
| `booked` | Resident has active tickets (`my_tickets_count > 0`)        |

**`cta`:** `book` | `closed` | `view_tickets` (when already booked).

### 5.3 Event detail

```http
GET /v1/community-events/{event_id}?unit_id={unit_id}
```

Response sections matching mobile UI:

- Header: category, title, date/time, location, booked/capacity bar.
- Banner: “You have N tickets” when `my_tickets_count > 0`.
- About, Schedule, Venue (from facility join), Tickets (pricing, tax, max per resident, booking closes, status).
- Event photos (cover + gallery).
- Bottom bar: price label + CTA.

### 5.4 Book tickets

```http
POST /v1/community-events/{event_id}/bookings?unit_id={unit_id}
{
  "adult_tickets": 2,
  "child_tickets": 1
}
```

Service transaction:

1. Load event; validate booking eligibility (§1 rules).
1. Compute amounts from event pricing + tax; store on booking row.
1. If capacity available → `booking_status = confirmed`; else → `waitlisted`.
1. If confirmed: increment `tickets_booked`, `bookings_count` on event.
1. Set `payment_status = pending` (paid) or `not_applicable` (free).
1. Allocate `BKG-{n}` display code.
1. Return booking + ticket summary for “View tickets” screen.

**Response (paid, pending payment):**

```json
{
  "booking_id": "uuid",
  "display_code": "BKG-501",
  "adult_tickets": 2,
  "child_tickets": 1,
  "total_tickets": 3,
  "total_amount_minor": 208900,
  "currency": "INR",
  "payment_status": "pending",
  "booking_status": "confirmed",
  "payment_instruction": "Pay at the clubhouse. Your booking is confirmed pending payment."
}
```

Phase 1 copy: resident is **not blocked** from booking when payment pending — gate/admin verifies
paid status via admin mark-paid after offline collection.

### 5.5 View / cancel my booking

```http
GET /v1/community-events/{event_id}/my-booking?unit_id={unit_id}
POST /v1/community-events/bookings/{booking_id}/cancel?unit_id={unit_id}
```

Cancel decrements event aggregates when booking was `confirmed`; audit log written.

______________________________________________________________________

## 6. Pricing examples

**Free event:** all amounts 0; `payment_status = not_applicable`.

**Paid — Pottery Workshop:** adult ₹589, child free, tax included 18%:

```text
2 adults × ₹589 = ₹1178
1 child  × ₹0   = ₹0
subtotal = ₹1178
tax 18%  = ₹212.04 → ₹212 (round per business rule)
total    = ₹1390
```

Store minor units (paise): `total_amount_minor = 139000`.

**Paid — Swim Meet:** adult ₹150, child ₹75, no tax:

```text
1 adult + 1 child = ₹225
```

______________________________________________________________________

## 7. Facility picker (reuse project setup)

Admin **New Event → Venue → Facility** uses existing facilities API filtered for event venues:

```http
GET /v1/projects/{project_id}/facilities?facility_types=events,sports,recreation,services&status=active
```

Only facilities where:

- `facility_type IN ('events', 'sports', 'recreation', 'services')` (project setup Step 7)
- `status = 'active'` and `active = true`

Resident detail **Venue** block joins:

| UI field     | Source                                        |
| ------------ | --------------------------------------------- |
| Facility     | `facilities.name`                             |
| Location     | tower name + `floor_level` + `wing` formatted |
| Type         | `facility_type` · `facility_subtype`          |
| Access notes | `facilities.location_notes`                   |

______________________________________________________________________

## 8. Phase plan

| Phase  | Scope                                                                                              |
| ------ | -------------------------------------------------------------------------------------------------- |
| **1**  | Migrations, admin CRUD + publish, resident list/detail/book, admin bookings + mark-paid, audit log |
| **1b** | Export CSV, auto-complete past events job, mark-waived                                             |
| **2**  | Push notifications, waitlist auto-promotion, gate QR on booking                                    |
| **3**  | Link to Notices (category Event), analytics dashboard                                              |

______________________________________________________________________

## 9. Error codes (proposed)

| Code                        | HTTP | When                                                      |
| --------------------------- | ---- | --------------------------------------------------------- |
| `event_not_found`           | 404  | Invalid id                                                |
| `event_not_editable`        | 409  | Published with bookings; structural PATCH                 |
| `event_not_publishable`     | 422  | Missing facility, booking deadline, etc.                  |
| `booking_closed`            | 422  | Past `booking_closes_at` or event ended                   |
| `capacity_exceeded`         | 422  | Would exceed cap (if waitlist disabled)                   |
| `ticket_limit_exceeded`     | 422  | Over `max_tickets_per_resident`                           |
| `child_tickets_not_allowed` | 422  | Child count when mode = not_applicable                    |
| `facility_not_eligible`     | 422  | Facility type not in events, sports, recreation, services |
| `booking_not_pending`       | 409  | mark-paid on non-pending row                              |
| `invalid_unit_context`      | 403  | Resident unit not in project                              |

______________________________________________________________________

## 10. Related docs

- [ADR 0014 — Community events](./adr/0014-community-events.md)
- [community-events-schema.md](../../ats-home-craft-supabase/docs/community-events-schema.md)
- [project-setup-flow.md](./project-setup-flow.md) — facilities source
- [notice-board-flow.md](./notice-board-flow.md) — similar community admin + resident pattern
- [push-notifications-flow.md](./push-notifications-flow.md) — Phase 2 notifications
