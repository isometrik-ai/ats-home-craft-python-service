# ADR 0005: Invoices and payments — vendor billing lifecycle

|              |                                                              |
| ------------ | ------------------------------------------------------------ |
| **Status**   | Accepted (V1)                                                |
| **Date**     | 2026-09-02                                                   |
| **Flow doc** | [../invoices-payments-flow.md](../invoices-payments-flow.md) |
| **Schema**   | `work_order.vendor_invoices`, `work_order.payments`          |

______________________________________________________________________

## Context

After work completion, vendors submit invoices with line items and PDF attachments. FM reviews,
may request revision, approves, and records payment. Amounts must be precise (INR paise).

**Resident maintenance fee billing** ([fee-flow.md](../../user_service/docs/fee-flow.md)) is a separate domain —
vendor AMC invoices must not reuse `maintenance_fee_invoices`.

______________________________________________________________________

## Decision

### Money storage

All amounts as `*_minor bigint` (paise). Convert to rupees only at API/UI boundary.

### Invoice status machine

```
submitted → revision_requested → resubmitted → approved → paid
                ↓                              ↓
            (vendor resubmit)              rejected
```

Vendor creates via portal (`source=vendor_portal`) or FM via staff API.

FM approve/reject: `PATCH .../invoices/{id}` requires `work_order_management.approve`.

Timeline append: `POST .../invoices/{id}/timeline` (append-only, same pattern as work orders).

### Attachments

`file_paths text[]` — R2 object keys from presigned upload flow ([0002](./0002-assets-and-custom-fields.md) upload endpoint).

### Payments

Separate `payments` table; `work_order_management.pay` on create/update/delete.

Link invoice → payment via `vendor_invoices.payment_id`; set invoice `status=paid` when payment recorded.

### Notifications

`notify_invoice_event` on vendor submit (best-effort gRPC per [ADR 0009](../../user_service/docs/adr/0009-push-notifications-grpc.md)).

______________________________________________________________________

## Consequences

**Positive:** Clear separation from resident billing; revision flow matches prototype; audit on all transitions.

**Negative:** Payment + invoice status update not fully atomic in one endpoint — FM may PATCH invoice after payment create.

**Follow-ups:** Single “mark paid” transaction; PDF generation (`pdf_templates` deferred).

______________________________________________________________________

## Alternatives considered

| Alternative              | Rejected because                         |
| ------------------------ | ---------------------------------------- |
| Reuse fee invoice tables | Different actors, statuses, and GL rules |
| Float money columns      | Rounding errors                          |
| Data-URL file blobs      | Prototype anti-pattern; R2 required      |
