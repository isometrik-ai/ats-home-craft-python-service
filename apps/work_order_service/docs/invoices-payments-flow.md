# Invoices & Payments Flow — Context & Change Guide

> **Status: V1 implemented.**
> Architecture: [ADR 0005 — Invoices and payments](./adr/0005-invoices-and-payments.md)
> Part of [Work Order Service](./README.md).

- **Service:** `apps/work_order_service` (port 5001)
- **Staff API:** `/v1/projects/{project_id}/invoices`, `/payments`
- **Vendor API:** `POST/GET /v1/vendor/invoices` (token-scoped)
- **DB tables:** `work_order.vendor_invoices`, `work_order.payments`

______________________________________________________________________

## 1. What this flow does

After vendor work completes, the vendor submits an **invoice** (via portal or FM on their behalf). FM
**reviews**, may request **revision**, **approves** or **rejects**, then records a **payment** against
approved invoices.

Money is stored as **minor units** (paise) in `*_minor bigint` columns; convert at API/UI boundary.

### Invoice status lifecycle

```
submitted → revision_requested → resubmitted → approved → paid
                ↓                                    ↓
            rejected                              rejected
```

| Status               | Meaning                               |
| -------------------- | ------------------------------------- |
| `submitted`          | Initial vendor submission             |
| `revision_requested` | FM asked vendor to fix breakdown/docs |
| `resubmitted`        | Vendor re-submitted after revision    |
| `approved`           | FM approved for payment               |
| `rejected`           | FM rejected (terminal for payment)    |
| `paid`               | Payment recorded                      |

Enum: `work_order.work_order_invoice_status`.

### Business rules (must enforce)

| Rule                     | Enforcement                                              |
| ------------------------ | -------------------------------------------------------- |
| **Link to WO**           | Every invoice has `work_order_id` + `company_id`         |
| **Minor units**          | `subtotal_minor`, `tax_minor`, `total_minor` as integers |
| **File attachments**     | `file_paths text[]` — R2 keys from presigned upload      |
| **Timeline append-only** | `POST .../invoices/{id}/timeline`                        |
| **Approve permission**   | PATCH invoice requires `work_order_management.approve`   |
| **Pay permission**       | Payment routes require `work_order_management.pay`       |
| **Audit trail**          | All mutations → `EventsService` + optional webhooks      |
| **Payment link**         | `vendor_invoices.payment_id` set when paid               |

### Screen → capability map

| Screen / action             | Capability                                                              |
| --------------------------- | ----------------------------------------------------------------------- |
| Invoice list                | `GET /projects/{project_id}/invoices?status=&work_order_id=`            |
| Invoice detail              | `GET /projects/{project_id}/invoices/{id}`                              |
| FM create invoice           | `POST /projects/{project_id}/invoices`                                  |
| Approve / reject / revision | `PATCH /projects/{project_id}/invoices/{id}` `{ "status": "approved" }` |
| Append timeline             | `POST /projects/{project_id}/invoices/{id}/timeline`                    |
| Payment list                | `GET /projects/{project_id}/payments`                                   |
| Record payment              | `POST /projects/{project_id}/payments`                                  |
| Vendor submit               | `POST /v1/vendor/invoices`                                              |

______________________________________________________________________

## 2. Architecture

```
invoices.py / payments.py
    → InvoicesService / PaymentsService
    → InvoicesRepository / PaymentsRepository
    → EventsService + notify_invoice_event (on vendor submit)
```

### File map

| Concern         | File                                               |
| --------------- | -------------------------------------------------- |
| Invoice API     | `app/api/invoices.py`                              |
| Payment API     | `app/api/payments.py`                              |
| Invoice service | `app/services/invoices_service.py`                 |
| Payment service | `app/services/payments_service.py`                 |
| Repositories    | `invoices_repository.py`, `payments_repository.py` |
| Upload          | `app/api/presigned_url.py`                         |
| Notifications   | `app/adapters/notifications.py`                    |

______________________________________________________________________

## 3. Data model

### `vendor_invoices`

| Column                                       | Notes               |
| -------------------------------------------- | ------------------- |
| `work_order_id`, `company_id`                | Required links      |
| `invoice_number`, `invoice_date`             | Vendor reference    |
| `line_items`                                 | jsonb array         |
| `subtotal_minor`, `tax_minor`, `total_minor` | Paise               |
| `currency`                                   | Default `INR`       |
| `status`                                     | Lifecycle enum      |
| `file_paths`                                 | R2 object keys      |
| `timeline`, `revisions`                      | jsonb audit/history |
| `payment_id`                                 | Set when paid       |

### `payments`

| Column                        | Notes                                             |
| ----------------------------- | ------------------------------------------------- |
| `invoice_id`                  | FK to vendor_invoices                             |
| `amount_minor`                | Paise                                             |
| `method`                      | `bank_transfer`, `cheque`, `cash`, `upi`, `other` |
| `status`                      | `pending`, `completed`, `failed`, `voided`        |
| `paid_at`, `reference_number` | Accounting metadata                               |

______________________________________________________________________

## 4. FM flow (step by step)

### 4.1 Review invoice list

```http
GET /v1/projects/{project_id}/invoices?status=submitted
Authorization: Bearer <jwt>
```

### 4.2 Request revision

```http
PATCH /v1/projects/{project_id}/invoices/{id}
Authorization: Bearer <jwt>

{
  "status": "revision_requested",
  "timeline": null
}
```

Prefer append timeline separately:

```http
POST /v1/projects/{project_id}/invoices/{id}/timeline
{ "type": "revision_requested", "note": "Upload GST breakdown" }
```

Then PATCH status. Vendor sees note on portal and re-submits.

### 4.3 Approve invoice

```http
PATCH /v1/projects/{project_id}/invoices/{id}
{ "status": "approved" }
```

Requires `work_order_management.approve`. Triggers `status_changed` webhook if configured.

### 4.4 Record payment

```http
POST /v1/projects/{project_id}/payments
Authorization: Bearer <jwt>

{
  "invoice_id": "<uuid>",
  "amount_minor": 177000,
  "method": "bank_transfer",
  "status": "completed",
  "paid_at": "2026-09-10T14:00:00Z",
  "reference_number": "NEFT-123456"
}
```

Requires `work_order_management.pay`. Follow-up PATCH on invoice:

```http
PATCH /v1/projects/{project_id}/invoices/{id}
{ "status": "paid", "payment_id": "<payment-uuid>" }
```

______________________________________________________________________

## 5. File upload flow

1. FM or vendor requests presigned URL:

```http
GET /v1/projects/{project_id}/upload/presigned-url
  ?file_name=invoice.pdf
  &path={org_id}/{project_id}/invoices/{wo_id}
  &bucket=<r2-bucket>
  &content_type=application/pdf
```

2. Client `PUT` file to returned URL.
1. Include returned key in `file_paths` on invoice create/PATCH.

Same pattern as user_service presigned uploads — reuses Cloudflare R2 credentials from `shared_settings`.

______________________________________________________________________

## 6. Vendor submission flow

See [vendor-portal-flow.md](./vendor-portal-flow.md) §4.3.

On create with `source=vendor_portal`:

- Audit event with `source=vendor_portal`
- Push notification `invoice.submitted` (when notification service enabled)

______________________________________________________________________

## 7. Where to change things

| Change                   | Edit                                                        |
| ------------------------ | ----------------------------------------------------------- |
| Status transition rules  | Add validation in `InvoicesService.update` before repo call |
| Auto-set paid on payment | Compose in `PaymentsService.create` → update linked invoice |
| New payment method       | Supabase enum + `payments_repository.py`                    |
| Currency handling        | API schemas / UI formatters                                 |
| PDF generation           | Deferred (`pdf_templates` table optional)                   |

______________________________________________________________________

## 8. Related flows

- Vendor submit → [vendor-portal-flow.md](./vendor-portal-flow.md)
- Work order link → [work-orders-flow.md](./work-orders-flow.md)
- Webhooks on status change → [integrations-flow.md](./integrations-flow.md)

**Not in scope V1:** Resident maintenance fee billing — see [fee-flow.md](../../user_service/docs/fee-flow.md) (separate domain).
