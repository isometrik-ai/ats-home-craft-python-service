# Walk-in Flow — Context & Change Guide

> **Status: Phase 1 implemented (API + service + migrations + push notifications).** Visitor Logs union remains a follow-up.
>
> This document describes the **Walk-in** feature — security
> marks **one enter** and **one exit** for the visit — in the same style as
> [`passes-flow.md`](./passes-flow.md) and [`tenant-requests-flow.md`](./tenant-requests-flow.md).
>
> Schema and architecture rationale: [ADR 0008](./adr/0008-walk-in-entries.md).

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **Security API prefix:** `/v1/projects/{project_id}/walk-ins`
- **Resident API prefix:** `/v1/walk-ins`
- **DB schema:** `ats-home-craft-supabase` (migrations `20260727120000_*`, `20260727121000_*`)

______________________________________________________________________

## 1. What this flow does

A **security guard** creates a **walk-in visit** when a guest arrives without a pre-created pass.
The visit can target **one or many flats** (e.g. a courier with parcels for 3 units). Security fills
visitor details **once** and lists each flat with **`tower_id` + `unit_id`**.

Each flat is a **visit unit** (`walk_in_visit_units`) that its residents (any contact with an **active**
`contact_units` link on that flat) approve or
reject **independently**. The security list shows **one card per visit** with **`flats_count`**.

### Product rules (confirmed)

| Rule                 | Behaviour                                                                   |
| -------------------- | --------------------------------------------------------------------------- |
| **Gate entry**       | Security verification (create with photos) **+ at least one flat approved** |
| **Partial approval** | Visitor may enter when some flats approved; rejected flats are skipped      |
| **List UI**          | One card per visit; show **number of flats**                                |
| **Exit**             | **One exit** for the whole visit (not per flat)                             |
| **Create API**       | Single `POST /walk-ins` with **`flats[]`** — no separate batch endpoint     |

### Screen → capability map

**Security mobile**

| Screen / action | Capability                                                               |
| --------------- | ------------------------------------------------------------------------ |
| Walk-in list    | `GET /projects/{project_id}/walk-ins` → `flats_count` on row             |
| Create walk-in  | `POST /projects/{project_id}/walk-ins` with `flats[]`                    |
| Pass Details    | `GET /projects/{project_id}/walk-ins/{id}` → `visit_units[]`, `events[]` |
| Mark entered    | `POST /projects/{project_id}/walk-ins/{id}/enter`                        |
| Mark exit       | `POST /projects/{project_id}/walk-ins/{id}/exit`                         |

**Resident mobile**

| Screen / action | Capability                                                                      |
| --------------- | ------------------------------------------------------------------------------- |
| Pending for me  | `GET /walk-ins/visit-units` (all statuses) or `?status=awaiting` (pending only) |
| Visit detail    | `GET /walk-ins/{entry_id}` (entry + my visit unit)                              |
| Approve my flat | `POST /walk-ins/{entry_id}/visit-units/{visit_unit_id}/approve`                 |
| Reject my flat  | `POST /walk-ins/{entry_id}/visit-units/{visit_unit_id}/reject`                  |

______________________________________________________________________

## 2. Data model

### Tables

| Table                     | Purpose                                                            |
| ------------------------- | ------------------------------------------------------------------ |
| **`walk_in_entries`**     | Visit header: visitor info, header status, enter/exit              |
| **`walk_in_visit_units`** | One row per flat: `tower_id`, `unit_id`, visit unit status         |
| **`walk_in_events`**      | Timeline: requested, visit unit approved/rejected, entered, exited |

Full DDL: [ADR 0008 § Schema](./adr/0008-walk-in-entries.md#schema-proposed).

### Header status (`walk_in_entries.status`)

| Status      | When                                                       |
| ----------- | ---------------------------------------------------------- |
| `awaiting`  | Created; no visit unit approved yet                        |
| `approved`  | ≥1 visit unit approved; ready for security to mark entered |
| `entered`   | Security marked visitor inside                             |
| `exited`    | Security marked visitor left (terminal)                    |
| `cancelled` | All visit units rejected with none awaiting or approved    |

### Visit unit status (`walk_in_visit_units.status`)

| Status     | When                        |
| ---------- | --------------------------- |
| `awaiting` | Resident has not acted      |
| `approved` | Resident approved this flat |
| `rejected` | Resident rejected this flat |

```text
security POST (flats[])
       │
       ▼
   awaiting ── any visit unit approved ──► approved ── enter ──► entered ── exit ──► exited
       │
       └── all visit units rejected ──► cancelled (cannot enter)
```

______________________________________________________________________

## 3. Security flow

### 3.1 Create walk-in (single or multi-flat)

```http
POST /v1/projects/{project_id}/walk-ins
{
  "visitor_first_name": "Sushil",
  "visitor_last_name": "Jha",
  "visitor_phone_isd_code": "+91",
  "visitor_phone_number": "9876543210",
  "visitor_photo_paths": ["org/.../visitor.jpg"],
  "vehicle_photo_paths": ["org/.../bike.jpg"],
  "notes": "Swish Delivery — 3 parcels",
  "gate_id": "uuid-optional",
  "flats": [
    { "tower_id": "tower-a-uuid", "unit_id": "unit-2102-uuid" },
    { "tower_id": "tower-b-uuid", "unit_id": "unit-1204-uuid" },
    { "tower_id": "tower-a-uuid", "unit_id": "unit-2106-uuid" }
  ]
}
```

Service:

1. Validates `len(flats) >= 1`.
1. For each flat: `unit_id` belongs to `project_id` and `units.tower_id = tower_id`.
1. Inserts `walk_in_entries` (`status = awaiting`, `flats_count = len(flats)`).
1. Inserts one `walk_in_visit_units` row per flat (`status = awaiting`).
1. Appends entry-level event `requested`.
1. *(Follow-up)* Notify residents per visit unit.

Single-flat visits use the same API with `flats` length 1.

### 3.2 List

```http
GET /v1/projects/{project_id}/walk-ins?status=awaiting&date=2026-07-27
```

Example row:

```json
{
  "id": "entry-uuid",
  "visitor_first_name": "Sushil",
  "visitor_last_name": "Jha",
  "status": "approved",
  "flats_count": 3,
  "approved_flats_count": 2,
  "primary_unit_label": "A-2102",
  "requested_at": "2026-07-27T09:00:00Z"
}
```

Client groups by date (Today / Yesterday). **`flats_count`** drives the "3 flats" label on the card.

### 3.3 Enter / exit (whole visit)

```http
POST /v1/projects/{project_id}/walk-ins/{entry_id}/enter
POST /v1/projects/{project_id}/walk-ins/{entry_id}/exit
```

**Enter** — allowed when:

- Header status is `awaiting` or `approved`, and
- **`approved_flats_count >= 1`**

Sets header `entered`, `entered_at`; event `entered`.

**Exit** — allowed when header is `entered`. Sets `exited`, `exited_at`; event `exited`.

### 3.4 Detail

```http
GET /v1/projects/{project_id}/walk-ins/{entry_id}
```

Returns visitor card, header status, **`visit_units[]`** (tower name, unit label, status), **`events[]`**, `milestones[]`.

______________________________________________________________________

## 4. Resident flow

### 4.1 List visit units for my flats

```http
GET /v1/walk-ins/visit-units
GET /v1/walk-ins/visit-units?status=awaiting
```

Returns visit units where the contact has an active `contact_units` link to `visit_unit.unit_id`.
Without `status`, **all** visit unit statuses are returned. Use `?status=awaiting` for the pending inbox only.
Each item includes parent entry summary (visitor name, photos, notes).

### 4.2 Approve / reject my flat only

```http
POST /v1/walk-ins/{entry_id}/visit-units/{visit_unit_id}/approve

POST /v1/walk-ins/{entry_id}/visit-units/{visit_unit_id}/reject
{ "rejection_reason": "Not expecting delivery" }
```

1. Assert visit unit belongs to entry and is `awaiting`.
1. Assert contact has active link to `visit_unit.unit_id`.
1. Update visit unit → `approved` or `rejected`.
1. Append `visit_unit_approved` or `visit_unit_rejected` event (with `tower_id`, `unit_id` in payload).
1. Recompute header: if any visit unit approved → header `approved`; increment `approved_flats_count`.

Other flats' decisions do not block this visit unit's action.

______________________________________________________________________

## 5. Multi-flat delivery example

Courier visits **A-2102**, **B-1204**, **A-2106**:

| Step | Actor           | Result                                                                      |
| ---- | --------------- | --------------------------------------------------------------------------- |
| 1    | Security        | Creates one entry, 3 visit units, all `awaiting`. List shows **"3 flats"**. |
| 2    | A-2102 resident | Approves visit unit for A-2102                                              |
| 3    | B-1204 resident | Rejects visit unit for B-1204                                               |
| 4    | A-2106 resident | Approves visit unit for A-2106                                              |
| 5    | Header          | `approved` (`approved_flats_count = 2`)                                     |
| 6    | Security        | `POST .../enter` — allowed (≥1 approved)                                    |
| 7    | Courier         | Delivers to A-2102 and A-2106 only; skips B-1204                            |
| 8    | Security        | `POST .../exit` — one exit for whole visit                                  |

______________________________________________________________________

## 6. Error keys

| Key                                        | When                                      |
| ------------------------------------------ | ----------------------------------------- |
| `walk_in.errors.flats_required`            | Create with empty `flats[]`               |
| `walk_in.errors.unit_tower_mismatch`       | `unit_id` not under `tower_id`            |
| `walk_in.errors.unit_not_in_project`       | Unit not in project                       |
| `walk_in.errors.photos_required`           | No visitor photos                         |
| `walk_in.errors.no_approved_visit_units`   | Enter when zero visit units approved      |
| `walk_in.errors.visit_unit_not_awaiting`   | Approve/reject on non-awaiting visit unit |
| `walk_in.errors.unit_not_accessible`       | Resident not linked to visit unit's flat  |
| `walk_in.errors.invalid_status_transition` | Enter/exit from wrong header status       |

______________________________________________________________________

## 7. DB changes summary

| Migration                           | Contents                                                                                  |
| ----------------------------------- | ----------------------------------------------------------------------------------------- |
| `20260727120000_walk_in_enums.sql`  | `walk_in_status`, `walk_in_visit_unit_status`, `walk_in_event_type`, `walk_in_actor_type` |
| `20260727121000_walk_in_tables.sql` | `walk_in_entries`, `walk_in_visit_units`, `walk_in_events`                                |

See [ADR 0008](./adr/0008-walk-in-entries.md) for full column lists.

______________________________________________________________________

## Related

- [ADR 0008 — Walk-in entries](./adr/0008-walk-in-entries.md)
- [passes-flow.md](./passes-flow.md) — resident QR passes (no approval)
- [tenant-requests-flow.md](./tenant-requests-flow.md) — similar approval + timeline pattern
