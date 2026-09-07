# Work Order Management — Team Overview

|                   |                                                                                                             |
| ----------------- | ----------------------------------------------------------------------------------------------------------- |
| **Audience**      | Engineering team                                                                                            |
| **Purpose**       | High-level summary for planning and alignment                                                               |
| **Status**        | V1 implemented                                                                                              |
| **Date**          | 2026-09-02                                                                                                  |
| **Detailed spec** | [integration-review.md](./integration-review.md)                                                            |
| **ADRs**          | [adr/README.md](./adr/README.md)                                                                            |
| **Flow docs**     | [README.md](./README.md)                                                                                    |
| **Schema**        | [work-order-management-schema.md](../../../../ats-home-craft-supabase/docs/work-order-management-schema.md) |

______________________________________________________________________

## 1. What We're Building

We are integrating a **Work Order Management (WOM)** module into ATS-Home-Craft. This is **facility management** for gated communities — not e-commerce or product orders.

**Core capabilities:**

| Area                    | What it does                                                                               |
| ----------------------- | ------------------------------------------------------------------------------------------ |
| **Assets**              | Register equipment (HVAC, DG sets, fire pumps, gym gear) with categories and custom fields |
| **AMC contracts**       | Maintenance agreements that **auto-generate work orders** on a schedule                    |
| **Work orders**         | Tasks assigned to vendors with a full status lifecycle                                     |
| **Vendor portal**       | Vendors view assigned work, submit invoices, upload documents                              |
| **Invoices & payments** | FM approves vendor invoices and records payments                                           |
| **Integrations**        | Webhooks, public API, audit logs (from prototype)                                          |

**Source:** A Cloud AI–built prototype (`order-management`) with a working React UI and FastAPI backend. We adopt it as a **starting point**, not a drop-in production system.

______________________________________________________________________

## 2. Why Now / Business Fit

Home Craft today covers resident ops (contacts, units, passes, fees, notices) but has **no facility or vendor maintenance workflow**.

| Home Craft today                              | WOM adds                                 |
| --------------------------------------------- | ---------------------------------------- |
| Maintenance **fee billing** (residents)       | Vendor **AMC contracts** and work orders |
| CRM companies/contacts                        | Vendor assignment + invoice approval     |
| R2 file uploads, JWT auth, push notifications | Same infra — applied to FM domain        |

WOM fills a clear product gap for property management teams managing assets and third-party vendors.

______________________________________________________________________

## 3. Architecture Decision

**Add a new service** in the monorepo — same pattern as `user_service`.

```
┌─────────────────────┐                    ┌─────────────────────────┐
│   user_service      │   REST / events    │  work_order_service     │
│   port 5000         │ ◄────────────────► │  port 5001              │
│                     │                    │                         │
│  Orgs, users, CRM   │                    │  Assets, contracts,     │
│  contacts, fees     │                    │  work orders, invoices  │
└──────────┬──────────┘                    └────────────┬────────────┘
           │                                            │
           ▼                                            ▼
    public schema                              work_order schema
    (Supabase Postgres)
```

| Decision                   | Choice                                              | Rationale                                            |
| -------------------------- | --------------------------------------------------- | ---------------------------------------------------- |
| Where code lives           | `apps/work_order_service/`                          | Mirrors `user_service`; independent deploy and scale |
| Structure                  | `api/` → `services/` → `repositories/` → `schemas/` | Team already knows this pattern                      |
| Shared infra               | Reuse `libs/shared_*`                               | JWT, DB pool, logging, config                        |
| Database                   | Same Postgres, separate `work_order` schema         | One Supabase instance; clear ownership               |
| Merge into `user_service`? | **No**                                              | Avoid bloating an already large service              |
| Separate domain package?   | **Not at launch**                                   | Extract later if another project needs it            |

______________________________________________________________________

## 4. What the Prototype Gives Us (Strengths)

Worth keeping — reduces build time significantly:

- **Contract-driven scheduler** — visit frequency (monthly, quarterly) auto-creates work orders ahead of due dates
- **Work order + invoice lifecycle** — state machines, timelines, revision flow
- **Full React admin UI** — assets, contracts, WOs, invoices, payments, settings
- **Vendor portal** — token link, invoice submission, file uploads
- **Webhook + audit pipeline** — mutation tracking and outbound integrations
- **Public API + MCP** — external system and AI agent access
- **Product specs** — documented in prototype `conductor/` folder

______________________________________________________________________

## 5. Gaps — What the Prototype Is Missing

These must be addressed **before production**. We cannot deploy the prototype as-is.

### 5.1 Security (blockers)

| Gap                                        | Risk                                          |
| ------------------------------------------ | --------------------------------------------- |
| **No auth on internal API**                | Anyone can read/write all data                |
| **Vendor portal not enforced server-side** | Token is UI-only; API exposes all work orders |
| **Tenant via query param**                 | Trivial to bypass multi-tenancy               |
| **Unprotected API key management**         | Anyone can create/revoke integration keys     |
| **Open CORS + credentials**                | Overly permissive browser access              |
| **Secrets in prototype `.env`**            | HoA credentials exposed in download           |

### 5.2 Engineering / ops

| Gap                             | Impact                                                |
| ------------------------------- | ----------------------------------------------------- |
| **Zero automated tests**        | No safety net for refactors or scheduler logic        |
| **No real DB migrations**       | Schema changes via startup `ALTER TABLE` hack         |
| **No Docker/deployment setup**  | Not deployable in our pipeline                        |
| **File storage as JSON blobs**  | Invoice PDFs stored as data-URLs — not scalable       |
| **In-process scheduler**        | Duplicate work orders if scaled to multiple instances |
| **Webhook queue in memory**     | Lost on restart                                       |
| **No push/email notifications** | FM and vendors not alerted on status changes          |

### 5.3 Alignment with Home Craft

| Prototype           | Home Craft standard                   | Change needed                              |
| ------------------- | ------------------------------------- | ------------------------------------------ |
| `tenant_id` string  | `organization_id` + `project_id` UUID | Add both to every table                    |
| SQLAlchemy ORM      | asyncpg repositories                  | Rewrite data layer                         |
| HoA SDK for vendors | CRM `companies` in `user_service`     | Store `company_id`; fetch vendors from CRM |
| Fee invoices module | Resident billing                      | **Do not reuse** — different domain        |
| No notifications    | gRPC push service                     | Wire on key WO/invoice events              |

______________________________________________________________________

## 6. Required Changes (Prototype → Home Craft)

Summary of work beyond copy-paste:

| #   | Change                                                       | Priority |
| --- | ------------------------------------------------------------ | -------- |
| 1   | Scaffold `apps/work_order_service/` (mirror `user_service`)  | P0       |
| 2   | Supabase migrations — `work_order` schema (~15 tables)       | P0       |
| 3   | Add JWT auth + vendor token middleware                       | P0       |
| 4   | Port business logic to `app/services/` (scheduler, invoices) | P0       |
| 5   | Rewrite repositories with asyncpg                            | P0       |
| 6   | Add unit + integration tests (scheduler, WO lifecycle)       | P0       |
| 7   | Docker + compose entry (port 5001)                           | P0       |
| 8   | R2 presigned URLs for invoice/document uploads               | P1       |
| 9   | Map vendors to CRM `company_id`                              | P1       |
| 10  | Push notifications on status changes                         | P1       |
| 11  | Kafka events for cross-service sync (optional)               | P2       |
| 12  | Fix prototype code bugs (duplicate fields, dead code)        | P1       |
| 13  | Scheduler locking for multi-instance                         | P1       |
| 14  | Point React UI at new backend API                            | P1       |

______________________________________________________________________

## 7. Key Challenges

### 7.1 Technical

| Challenge                     | Mitigation                                                                            |
| ----------------------------- | ------------------------------------------------------------------------------------- |
| **ORM → asyncpg rewrite**     | Port service logic first; repositories second; use prototype as spec                  |
| **Scheduler correctness**     | Unit tests around contract → WO generation; Postgres advisory lock for multi-instance |
| **Two services, one product** | Loose coupling: WOM stores foreign refs (`company_id`); no cross-DB joins             |
| **Vendor portal security**    | Server-side token middleware; scoped routes; cryptographically strong tokens          |

### 7.2 Integration

| Challenge                               | Mitigation                                                    |
| --------------------------------------- | ------------------------------------------------------------- |
| **Vendor master data lives in CRM**     | WOM references `company_id`; vendor list from `user_service`  |
| **Dual API for frontend (5000 + 5001)** | Accept short-term; optional BFF proxy in `user_service` later |
| **Auth consistency**                    | Same Supabase JWT secret; shared `JWTAuthMiddleware`          |

### 7.3 Process / scope

| Challenge                                      | Mitigation                                                              |
| ---------------------------------------------- | ----------------------------------------------------------------------- |
| **Large surface area** (~15 entities, full UI) | Phased delivery — schema → core WO flow → invoices → integrations       |
| **Prototype looks "done"**                     | Team awareness: UI works; backend is MVP with critical gaps             |
| **Custom fields overlap with CRM**             | Defer unification; WOM keeps its own custom fields for assets initially |

______________________________________________________________________

## 8. Out of Scope (For Now)

To keep the first release focused:

- Merging WOM into `user_service`
- Unifying asset custom fields with CRM custom fields
- Replacing the Cloud AI React UI with Home Craft admin (can come later)
- Payment gateway integration (prototype uses manual payment recording)
- E-commerce / product order management
- Reusing maintenance **fee invoice** module for vendor billing

______________________________________________________________________

## 9. Proposed Roadmap

| Phase                 | Deliverable                                                  | Est. duration |
| --------------------- | ------------------------------------------------------------ | ------------- |
| **0 — Scaffold**      | Service skeleton, health check, Docker, config               | 2–3 days      |
| **1 — Schema**        | Supabase migrations (assets → contracts → WOs → invoices)    | 3–5 days      |
| **2 — Core services** | Scheduler + WO/invoice lifecycle + unit tests                | 1–2 weeks     |
| **3 — API + auth**    | CRUD routes, JWT, vendor token gating                        | ~1 week       |
| **4 — Hardening**     | R2 files, audit, webhooks, scheduler lock, integration tests | ~1 week       |
| **5 — Integrate**     | CRM vendor refs, push notifications, optional Kafka          | ~1 week       |
| **6 — Frontend**      | Connect existing React UI to new backend                     | parallel      |

**Total backend estimate:** ~5–7 weeks to production-ready core (excluding full admin UI embed).

______________________________________________________________________

## 10. Risks

| Risk                                       | Likelihood       | Impact       | Mitigation                                              |
| ------------------------------------------ | ---------------- | ------------ | ------------------------------------------------------- |
| Deploying prototype without auth fixes     | Medium if rushed | **Critical** | Auth in Phase 3 before any shared env deploy            |
| Scheduler bugs → missed or duplicate WOs   | Medium           | High         | Test coverage + advisory locks                          |
| Scope creep (CRM custom field unification) | Medium           | Medium       | Explicit out-of-scope list                              |
| Frontend/API mismatch after port           | Medium           | Medium       | Keep API shape close to prototype initially             |
| Vendor data unavailable (CRM down)         | Low              | Medium       | Cache company names; WOM works with stored `company_id` |

______________________________________________________________________

## 11. Dependencies on Other Teams / Systems

| Dependency          | Owner                    | Notes                                                |
| ------------------- | ------------------------ | ---------------------------------------------------- |
| Supabase migrations | Backend                  | New `work_order` schema in `ats-home-craft-supabase` |
| JWT / Supabase auth | Platform                 | Same secrets as `user_service`                       |
| CRM companies API   | Backend (`user_service`) | Vendor list + `company_id` references                |
| R2 storage          | Platform                 | Presigned URL pattern already exists                 |
| Push notifications  | Backend + Go service     | gRPC to notification-service                         |
| DevOps / Docker     | Infra                    | New container on port 5001                           |
| Frontend            | FE team                  | Point Cloud AI SPA at `:5001` or plan admin embed    |

______________________________________________________________________

## 12. Success Criteria (V1)

- [ ] FM staff can create assets, contracts, and work orders under a project
- [ ] Scheduler auto-generates work orders from active contracts
- [ ] Vendor can access assigned work via token link and submit invoice
- [ ] FM can approve/reject invoices and record payments
- [ ] All routes require auth; vendor routes are token-scoped
- [ ] Schema managed via Supabase migrations (no startup ALTER hacks)
- [ ] Core scheduler and lifecycle covered by automated tests
- [ ] Service runs in Docker on port 5001 alongside `user_service`

______________________________________________________________________

**Deep-dive reference:** [integration-review.md](./integration-review.md) — API surface, entity list, code bugs, port mapping, and checklists.

______________________________________________________________________

## Appendix — At a Glance

|             | Prototype                | Target (Home Craft)                |
| ----------- | ------------------------ | ---------------------------------- |
| **Service** | Standalone FastAPI :8000 | `work_order_service` :5001         |
| **Auth**    | None (internal API)      | Supabase JWT + vendor tokens       |
| **DB**      | SQLAlchemy / SQLite dev  | asyncpg + Supabase migrations      |
| **Tenancy** | `tenant_id` query param  | `organization_id` + `project_id`   |
| **Vendors** | External HoA SDK         | CRM `companies` via `user_service` |
| **Files**   | Data-URL in JSON         | R2 presigned URLs                  |
| **Tests**   | None                     | Unit + integration (85%+ target)   |
| **Deploy**  | `start.sh`               | Docker Compose                     |

**Verdict:** Strong prototype to build on — **not safe to deploy without the changes above.**
