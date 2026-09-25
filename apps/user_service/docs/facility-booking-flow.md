# Facility booking

Residents book community facilities (courts, halls, rooms, tee times) that staff have marked bookable. The existing `facilities` row stays the inventory record; booking rules live on `facility_booking_configs`.

## Staff

1. Mark a facility bookable on create/update (`POST/PATCH /v1/projects/{project_id}/facilities`) with `is_bookable` and optional `booking_archetype`. Parking and utility facilities cannot be bookable.
1. Configure the workspace at `/v1/projects/{project_id}/facility-bookings/facilities/{facility_id}` (hours, pricing, policies, units, schedules, blocks, closures).
1. Operate reservations: approve, check-in, complete, no-show. Staff without configure permission can operate only assigned facilities.

## Resident

Under `/v1/projects/{project_id}/resident/facility-bookings`:

- Browse bookable facilities and availability
- Quote / validate / create a reservation
- List mine, cancel, reschedule

Phase 3 adds contact-scoped wallets and booking invoices. These are separate from maintenance-fee invoices.

## Ledger

Entries live on `facility_booking_ledger_entries` and belong to the host **contact**. Types match Clubhouse: charge, deposit, balance, adjustment, cancellation_fee, no_show_forfeit, manual_charge, refund (payment reserved for Phase 3).

| Event                  | Ledger                                                             |
| ---------------------- | ------------------------------------------------------------------ |
| Confirmed create       | `charge` for quote total                                           |
| Approve                | `deposit` when the quote has a deposit                             |
| Check-in               | `balance` for `due_later`                                          |
| Resident cancel        | `refund` of net paid, then `cancellation_fee` from the policy tier |
| Staff cancel           | `refund` of net paid, no fee                                       |
| Reschedule (confirmed) | `adjustment` or `refund` for the difference vs net paid            |
| No-show                | `refund` of net paid, then `no_show_forfeit`                       |
| Staff manual charge    | `manual_charge`                                                    |

Staff: `GET /facility-bookings/ledger`, `/ledger/unbilled`, `/contacts/{id}/ledger`, `/reservations/{id}/ledger`, `POST /charges`.
Resident: `GET /resident/facility-bookings/ledger` and `/reservations/{id}/ledger`.

## Wallets and invoices

Payment settings live on `project_booking_settings` (`invoice_frequency`, wallet/cash/online toggles, default credit limit). Each contact has one `facility_booking_wallets` row per project.

- Staff generate invoices from unbilled positive charges (`POST /invoices/generate`) and collect via wallet, cash or online.
- Residents can view their invoices, pay by wallet or online, view their wallet, and top up by cash/online.
- Paying by wallet refuses the payment if the resulting balance would drop below `-credit_limit`.
- The public Clubhouse invoice-by-id pay link is not ported; every payment requires a signed-in staff or resident session.

## Notifications

Booking events go through `PushNotificationDispatcher` (`NOTIFICATION_TYPE_SYSTEM` / `facility_booking`) so they appear in the existing in-app feed. Copy lives under `notifications.push.facility_booking.*`.

## Permissions

- `facility_booking_management.view`
- `facility_booking_management.configure`
- `facility_booking_management.approve`
- `facility_booking_management.operate`
- `facility_booking_management.billing` (manual charges and unbilled list)
