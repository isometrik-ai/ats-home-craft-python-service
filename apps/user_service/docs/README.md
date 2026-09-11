# User Service — Documentation

> **Service:** `apps/user_service` (port **5000**)

All user_service documentation lives in this folder. Top-level index: [docs/README.md](../../../docs/README.md).

______________________________________________________________________

## Start here

| Document                                                                 | Audience           | Purpose                                       |
| ------------------------------------------------------------------------ | ------------------ | --------------------------------------------- |
| [membership-architecture.md](./membership-architecture.md)               | Engineering team   | Org + project membership model                |
| [contact-onboarding-flow.md](./contact-onboarding-flow.md)               | Backend + frontend | Resident onboarding flow                      |
| [project-setup-flow.md](./project-setup-flow.md)                         | Backend + frontend | Admin project setup wizard                    |
| [file-based-transactional-email.md](./file-based-transactional-email.md) | Backend engineers  | File-template emails (`send_templated_email`) |
| [adr/README.md](./adr/README.md)                                         | All engineers      | ADR index                                     |

______________________________________________________________________

## Flow guides (Context & Change Guide)

| Domain              | Guide                                                      | ADR                                                                                                 |
| ------------------- | ---------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Resident onboarding | [contact-onboarding-flow.md](./contact-onboarding-flow.md) | [0001](./adr/0001-resident-onboarding.md), [0002](./adr/0002-resident-onboarding-implementation.md) |
| Project setup       | [project-setup-flow.md](./project-setup-flow.md)           | [0001](./adr/0001-resident-onboarding.md)                                                           |
| Contact roles       | [api/contacts.md](./api/contacts.md)                       | [0010](./adr/0010-contact-roles.md)                                                                 |
| Project membership  | [membership-architecture.md](./membership-architecture.md) | [0011](./adr/0011-project-membership.md)                                                            |
| Visitor passes      | [passes-flow.md](./passes-flow.md)                         | [0003](./adr/0003-visitor-passes.md)                                                                |
| Pass validation     | [passes-validation-flow.md](./passes-validation-flow.md)   | [0004](./adr/0004-pass-validation-gate.md)                                                          |
| Move events         | [move-events-flow.md](./move-events-flow.md)               | [0005](./adr/0005-move-events.md)                                                                   |
| Project fees        | [fee-flow.md](./fee-flow.md)                               | [0006](./adr/0006-project-fee-configuration.md)                                                     |
| Tenant requests     | [tenant-requests-flow.md](./tenant-requests-flow.md)       | [0007](./adr/0007-tenant-requests.md)                                                               |
| Walk-in entries     | [walk-in-flow.md](./walk-in-flow.md)                       | [0008](./adr/0008-walk-in-entries.md)                                                               |
| Push notifications  | [push-notifications-flow.md](./push-notifications-flow.md) | [0009](./adr/0009-push-notifications-grpc.md)                                                       |
| Notice board        | [notice-board-flow.md](./notice-board-flow.md)             | [0012](./adr/0012-notice-board.md)                                                                  |
| Daily help          | [daily-help-flow.md](./daily-help-flow.md)                 | [0013](./adr/0013-daily-help.md)                                                                    |
| Community events    | [events-flow.md](./events-flow.md)                         | [0014](./adr/0014-community-events.md)                                                              |
| Parking allotment   | [parking-allotment-flow.md](./parking-allotment-flow.md)   | —                                                                                                   |
| Household pets      | [pets-flow.md](./pets-flow.md)                             | [0016](./adr/0016-pets.md)                                                                          |

Full ADR index: [adr/README.md](./adr/README.md)

______________________________________________________________________

## External references

| Doc                 | Location                                                                                                                     |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| DB schemas          | [ats-home-craft-supabase/docs/](../../../../ats-home-craft-supabase/docs/)                                                   |
| Work order service  | [work_order_service/docs/](../../work_order_service/docs/README.md) · [top-level index](../../../docs/work-order-service.md) |
| Frontend membership | [frontend-membership-flow.md](./frontend-membership-flow.md)                                                                 |

______________________________________________________________________

## Quick reference

| Item        | Value                                                             |
| ----------- | ----------------------------------------------------------------- |
| Staff API   | `/v1/...` (JWT + org/project permissions)                         |
| Run locally | `uvicorn apps.user_service.app.main:app --port 5000 --reload`     |
| Tests       | `ENVIRONMENT=test PYTHONPATH=. pytest apps/user_service/tests -q` |

See [../README.md](../README.md) for Docker.
