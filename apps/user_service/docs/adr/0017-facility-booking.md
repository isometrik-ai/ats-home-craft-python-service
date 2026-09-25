# ADR 0017 — Facility booking

## Context

Clubhouse facility reservations need to live in Home Craft without copying the demo FastAPI app. Home Craft already has `facilities` as property-setup inventory, project-scoped RBAC, and a walk-in / community-events vertical-slice pattern.

## Decision

- Extend `facilities` with `is_bookable` and `booking_archetype`. Booking rules sit on a 1:1 `facility_booking_configs` row.
- Support all five Clubhouse archetypes: slot, tee_time, duration, day_range, room.
- Port availability, pricing and lifecycle as pure Python engines (golden-tested against Clubhouse).
- Prevent double-booking with a per-facility advisory lock plus a gist exclusion constraint on active overlapping ranges.
- Staff vs resident routers; the booking ledger, wallet and invoices belong to the individual contact, not the household or unit.

## Consequences

Creating a bookable facility provisions a default config and one unit. Deleting or un-booking a facility is blocked while upcoming active reservations exist.
