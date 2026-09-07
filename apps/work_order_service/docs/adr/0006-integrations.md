# ADR 0006: Integrations — audit, webhooks, API keys

|              |                                                                     |
| ------------ | ------------------------------------------------------------------- |
| **Status**   | Accepted (V1)                                                       |
| **Date**     | 2026-09-02                                                          |
| **Flow doc** | [../integrations-flow.md](../integrations-flow.md)                  |
| **Schema**   | `trigger_configs`, `audit_events`, `webhook_deliveries`, `api_keys` |

______________________________________________________________________

## Context

FM admins and customer integrations need visibility into mutations (audit log) and outbound notifications
(webhooks). The prototype included public REST API, MCP, and in-memory webhook queue — not production-safe.

V1 must persist deliveries, retry failures, and manage API keys without exposing unauthenticated admin routes.

______________________________________________________________________

## Decision

### Events pipeline

Every entity service calls `EventsService.record_and_dispatch()` after successful write:

1. Insert **`audit_events`** (immutable snapshot + field diff).
1. Match active **`trigger_configs`** (entity + event).
1. Insert **`webhook_deliveries`** (status `pending`).
1. **`webhook_worker`** loop in lifespan POSTs with backoff.

Event sources: `fm`, `vendor_portal`, `scheduler`, `api` (future).

Derived event `status_changed` when diff touches `state` or `status`.

### Staff APIs

| Resource   | Routes                                                           |
| ---------- | ---------------------------------------------------------------- |
| Triggers   | CRUD + `POST .../triggers/{id}/test`                             |
| Audit log  | `GET .../audit-events`                                           |
| Deliveries | `GET .../webhook-deliveries`                                     |
| API keys   | Create/list/revoke — SHA-256 hash stored, raw key once on create |

### Push notifications (parallel)

Not webhooks — `notification_grpc_client` from `app/adapters/notifications.py` on WO create and invoice submit.

### Deferred from prototype

| Feature            | Reason                                  |
| ------------------ | --------------------------------------- |
| `api_call_logs`    | No public API traffic yet               |
| Public REST + MCP  | Ship after `api_keys` middleware exists |
| Kafka outbox       | Webhooks sufficient for V1              |
| `business_profile` | PDF letterhead not required             |

______________________________________________________________________

## Consequences

**Positive:** DB-backed queue survives restart; customers can integrate via webhooks; keys ready for future public API.

**Negative:** Webhook signing/HMAC may need hardening; no dead-letter admin UI in V1.

**Follow-ups:** Public API router behind API key auth; populate `api_call_logs`; MCP docs endpoint.

______________________________________________________________________

## Alternatives considered

| Alternative             | Rejected because                           |
| ----------------------- | ------------------------------------------ |
| In-memory webhook queue | Lost on restart (prototype flaw)           |
| Kafka only              | Heavier ops; DB queue enough for V1 volume |
| Ship public API in V1   | Scope cut; webhooks cover integrations     |
