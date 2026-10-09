# User Service — Documentation

> **Service:** `apps/user_service` (port **5000**)

All user_service documentation lives in this folder. Top-level index: [docs/README.md](../../../docs/README.md).

______________________________________________________________________

## Start here

| Document                                                                 | Audience           | Purpose                                       |
| ------------------------------------------------------------------------ | ------------------ | --------------------------------------------- |
| [0010-membership-architecture.md](./0010-membership-architecture.md)     | Engineering team   | Org + project membership model                |
| [0001-contact-onboarding-flow.md](./0001-contact-onboarding-flow.md)     | Backend + frontend | Resident onboarding flow                      |
| [project-setup-flow.md](./project-setup-flow.md)                         | Backend + frontend | Admin project setup wizard                    |
| [file-based-transactional-email.md](./file-based-transactional-email.md) | Backend engineers  | File-template emails (`send_templated_email`) |
| [adr/README.md](./adr/README.md)                                         | All engineers      | ADR index                                     |

______________________________________________________________________

## Flow guides (Context & Change Guide)

Listed in ADR order (`0001`–`0018`). Flow filenames use the same prefix as their primary ADR where applicable.

| Domain              | Guide                                                                | ADR                                                                                                 |
| ------------------- | -------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Resident onboarding | [0001-contact-onboarding-flow.md](./0001-contact-onboarding-flow.md) | [0001](./adr/0001-resident-onboarding.md), [0002](./adr/0002-resident-onboarding-implementation.md) |
| Project setup       | [project-setup-flow.md](./project-setup-flow.md)                     | [0001](./adr/0001-resident-onboarding.md)                                                           |
| Visitor passes      | [0003-passes-flow.md](./0003-passes-flow.md)                         | [0003](./adr/0003-visitor-passes.md)                                                                |
| Pass validation     | [0004-passes-validation-flow.md](./0004-passes-validation-flow.md)   | [0004](./adr/0004-pass-validation-gate.md)                                                          |
| Move events         | [0005-move-events-flow.md](./0005-move-events-flow.md)               | [0005](./adr/0005-move-events.md)                                                                   |
| Tenant requests     | [0006-tenant-requests-flow.md](./0006-tenant-requests-flow.md)       | [0006](./adr/0006-tenant-requests.md)                                                               |
| Walk-in entries     | [0007-walk-in-flow.md](./0007-walk-in-flow.md)                       | [0007](./adr/0007-walk-in-entries.md)                                                               |
| Push notifications  | [0008-push-notifications-flow.md](./0008-push-notifications-flow.md) | [0008](./adr/0008-push-notifications-grpc.md)                                                       |
| Contact roles       | [api/contacts.md](./api/contacts.md)                                 | [0009](./adr/0009-contact-roles.md)                                                                 |
| Project membership  | [0010-membership-architecture.md](./0010-membership-architecture.md) | [0010](./adr/0010-project-membership.md)                                                            |
| Notice board        | [0011-notice-board-flow.md](./0011-notice-board-flow.md)             | [0011](./adr/0011-notice-board.md)                                                                  |
| Daily help          | [0012-daily-help-flow.md](./0012-daily-help-flow.md)                 | [0012](./adr/0012-daily-help.md)                                                                    |
| Community events    | [0013-events-flow.md](./0013-events-flow.md)                         | [0013](./adr/0013-community-events.md)                                                              |
| Project-level RBAC  | [0010-membership-architecture.md](./0010-membership-architecture.md) | [0014](./adr/0014-project-level-rbac.md)                                                            |
| Parking allotment   | [parking-allotment-flow.md](./parking-allotment-flow.md)             | —                                                                                                   |
| Household pets      | [0015-pets-flow.md](./0015-pets-flow.md)                             | [0015](./adr/0015-pets.md)                                                                          |
| Facility booking    | [0016-facility-booking-flow.md](./0016-facility-booking-flow.md)     | [0016](./adr/0016-facility-booking.md)                                                              |
| Fee configuration   | [0017-fee-configuration-flow.md](./0017-fee-configuration-flow.md)   | [0017](./adr/0017-fee-configuration.md)                                                             |
| Buy & sell          | [0018-buy-and-sell-flow.md](./0018-buy-and-sell-flow.md)             | [0018](./adr/0018-buy-and-sell.md)                                                                  |

Full ADR index: [adr/README.md](./adr/README.md)

______________________________________________________________________

## External references

| Doc                 | Location                                                                   |
| ------------------- | -------------------------------------------------------------------------- |
| DB schemas          | [ats-home-craft-supabase/docs/](../../../../ats-home-craft-supabase/docs/) |
| Frontend membership | [frontend-membership-flow.md](./frontend-membership-flow.md)               |

______________________________________________________________________

## Quick reference

| Item        | Value                                                             |
| ----------- | ----------------------------------------------------------------- |
| Staff API   | `/v1/...` (JWT + org/project permissions)                         |
| Run locally | `uvicorn apps.user_service.app.main:app --port 5000 --reload`     |
| Tests       | `ENVIRONMENT=test PYTHONPATH=. pytest apps/user_service/tests -q` |

See [../README.md](../README.md) for Docker.
