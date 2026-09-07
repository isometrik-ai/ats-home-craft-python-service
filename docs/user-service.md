# User Service

> **Path:** `apps/user_service` · **Port:** 5000 · **API prefix:** `/v1/...`

CRM and resident platform — authentication, contacts, project membership, visitor passes, fees, community events, notice board, daily help, and related modules.

______________________________________________________________________

## Full documentation

All flow guides, API notes, and ADRs:

**[apps/user_service/docs/README.md](../apps/user_service/docs/README.md)**

Quick links:

| Topic               | Doc                                                                                |
| ------------------- | ---------------------------------------------------------------------------------- |
| Membership model    | [membership-architecture.md](../apps/user_service/docs/membership-architecture.md) |
| Resident onboarding | [contact-onboarding-flow.md](../apps/user_service/docs/contact-onboarding-flow.md) |
| Project setup       | [project-setup-flow.md](../apps/user_service/docs/project-setup-flow.md)           |
| ADRs                | [adr/README.md](../apps/user_service/docs/adr/README.md)                           |

______________________________________________________________________

## Run & test

```bash
uvicorn apps.user_service.app.main:app --host 0.0.0.0 --port 5000 --reload
ENVIRONMENT=test PYTHONPATH=. pytest apps/user_service/tests -q
```

See [apps/user_service/README.md](../apps/user_service/README.md) for Docker.

______________________________________________________________________

## Related

- [Top-level docs index](./README.md)
- [Work order service](./work-order-service.md) — separate service; shares `common_utils` and custom fields
