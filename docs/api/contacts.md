# Contacts API (`/v1/contacts`)

Module: `apps/user_service/app/api/contacts.py`

This is a **production-ready API reference** for the Contacts module. It focuses on **complete, copy/pasteable payloads** and **all supported scenarios**.

## Authentication & headers

- **Authorization**: `Authorization: Bearer <JWT>`
- **Language (optional)**: `lan: en` (affects `message`)
- **Content-Type**: `application/json`

## Standard success / error envelope (all endpoints)

### Success envelope (no `data`)

```json
{
  "status": "success",
  "message": "string",
  "statusCode": 200,
  "code": "2000"
}
```

### Success envelope (with `data`)

```json
{
  "status": "success",
  "message": "string",
  "statusCode": 200,
  "code": "2000",
  "data": {}
}
```

### Paginated list envelope

```json
{
  "status": "success",
  "message": "string",
  "statusCode": 200,
  "code": "2000",
  "data": [],
  "total": 0,
  "page": 1,
  "page_size": 20,
  "total_pages": 0
}
```

### Error envelope

```json
{
  "status": "error",
  "message": "string",
  "statusCode": 422,
  "code": "4004",
  "errors": [
    {
      "field": "string",
      "message": "string"
    }
  ]
}
```

## Endpoints

## `POST /v1/contacts` — Create contact

Creates a contact. Optionally links **one** company (existing or created inline) and can set membership as primary.

### Request body (all fields shown)

```json
{
  "email": "john@example.com",
  "portal_access": false,
  "prefix": "Mr",
  "first_name": "John",
  "middle_name": null,
  "last_name": "Smith",
  "title": "string",
  "date_of_birth": null,
  "profile_photo_url": "https://example.com/photo.png",
  "phones": [
    {
      "phone_number": "5551234567",
      "phone_isd_code": "+1",
      "label": "mobile",
      "is_primary": true
    }
  ],
  "tags": ["string"],
  "social_pages": [
    {
      "id": "optional-string",
      "platform": "linkedin",
      "url": "https://linkedin.com/in/john"
    }
  ],
  "websites": [
    {
      "id": "optional-string",
      "url": "https://example.com",
      "type": "personal",
      "is_primary": true
    }
  ],
  "custom_fields": [
    {
      "any": "json"
    }
  ],
  "additional_data": {
    "any": "json"
  },
  "lead": {
    "stage_id": "UUID",
    "intake_stage": "string",
    "lead_score": "string"
  },
  "company_association": {
    "add_association": {
      "company_id": "COMPANY_UUID",
      "is_primary": false
    },
    "create_and_associate": null
  },
  "addresses": [
    {
      "place_id": "optional-string",
      "address_line1": "1 Main St",
      "address_line2": "Suite 100",
      "city": "New York",
      "state": "NY",
      "postal_code": "10001",
      "country": "United States",
      "latitude": 40.0,
      "longitude": -73.0,
      "address_type": "work",
      "address_data": {
        "any": "json"
      },
      "is_primary": true
    }
  ]
}
```

### Supported scenarios (association)

#### 1) Contact only

Send the same payload but set:

```json
{ "company_association": null }
```

#### 2) Link existing company

```json
{
  "company_association": {
    "add_association": { "company_id": "COMPANY_UUID", "is_primary": true },
    "create_and_associate": null
  }
}
```

#### 3) Create company inline and associate (full company payload)

```json
{
  "company_association": {
    "add_association": null,
    "create_and_associate": {
      "company": {
        "name": "Acme Corp",
        "industry": "Software",
        "profile_photo_url": null,
        "portal_access": false,
        "email": "info@acme.com",
        "phones": [],
        "tags": [],
        "websites": [],
        "billing_preferences": { "method": null, "terms": null },
        "social_pages": [],
        "target_market_segments": [],
        "current_tech_stack": [],
        "preferred_communication_channels": [],
        "industry_specific_terminologies": [],
        "description": null,
        "custom_fields": [],
        "additional_data": {},
        "lead": null,
        "contact_association": null,
        "addresses": []
      },
      "is_primary": true
    }
  }
}
```

### Response

201 with the standard success envelope (no `data`).

______________________________________________________________________

## `GET /v1/contacts` — List contacts (DB)

### Query params

- `search` (optional, min 2)
- `status` (optional): `active|inactive|prospect|deleted`
- `page` (default 1)
- `page_size` (default 20, max 100)

### Response payload (`data[]` items include all fields returned)

```json
{
  "status": "success",
  "message": "string",
  "statusCode": 200,
  "code": "2000",
  "data": [
    {
      "id": "UUID",
      "organization_id": "UUID",
      "status": "active",
      "first_name": "string",
      "last_name": "string",
      "title": "string",
      "email": "string",
      "profile_photo_url": "string",
      "phones": [],
      "company_names": ["string"],
      "created_at": "2026-01-01T00:00:00Z",
      "updated_at": "2026-01-01T00:00:00Z"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 20,
  "total_pages": 1
}
```

______________________________________________________________________

## `GET /v1/contacts/search` — Search contacts (Typesense)

### Query params

- `query` (required, min 2)
- `status` (optional): `active|inactive|prospect|deleted`
- `page` (default 1)
- `page_size` (default 20, max 100)

### Response

Returns raw Typesense hits in the list envelope:

```json
{
  "status": "success",
  "message": "string",
  "statusCode": 200,
  "code": "2000",
  "data": [],
  "total": 0,
  "page": 1,
  "page_size": 20,
  "total_pages": 0
}
```

______________________________________________________________________

## `GET /v1/contacts/overview` — Contact overview

Returns overview card counts for the Contacts registry dashboard (Total Contacts, Owners, Tenants, Vendors).

### Query params

- `status` (optional): `active|inactive|prospect|deleted`
  - omitted — **All** tab: counts all non-deleted contacts
  - `active` — **Active** tab
  - `deleted` — **Deleted** tab

### Response payload (`data`)

```json
{
  "status": "success",
  "message": "Contact overview retrieved successfully.",
  "statusCode": 200,
  "code": "2000",
  "data": {
    "total": 26,
    "owners": 16,
    "tenants": 2,
    "vendors": 8
  }
}
```

Notes:

- Counts are org-scoped aggregates (same RBAC as list/search: `contacts_management.view`).
- Typed sub-counts (`owners`, `tenants`, `vendors`) are subsets of `total`; contacts with other or null `contact_type` contribute to `total` only.

______________________________________________________________________

## `GET /v1/contacts/{contact_id}` — Contact details

### Path params

- `contact_id`: UUID string

### Response payload (`data` includes all fields returned)

```json
{
  "status": "success",
  "message": "string",
  "statusCode": 200,
  "code": "2000",
  "data": {
    "id": "UUID",
    "organization_id": "UUID",
    "status": "active",
    "user_id": "UUID",
    "isometrik_user_id": "string",
    "prefix": "string",
    "first_name": "string",
    "middle_name": "string",
    "last_name": "string",
    "title": "string",
    "date_of_birth": "2026-01-01",
    "profile_photo_url": "string",
    "email": "string",
    "phones": [],
    "tags": [],
    "custom_fields": [],
    "additional_data": {},
    "social_pages": [],
    "work_history": [],
    "educational_history": [],
    "skills": [],
    "enrichment_done": false,
    "enrichment_status": null,
    "last_enriched_at": null,
    "companies": [],
    "leads": [],
    "addresses": [],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z"
  }
}
```

______________________________________________________________________

## `PATCH /v1/contacts/{contact_id}` — Update contact

Updates contact fields, nested deltas, addresses delta, and optional company association delta.

### Path params

- `contact_id`: UUID string

### Request body (all fields shown)

All fields are optional; only provided fields are applied. The payload below shows **every field** and the **full nested shapes**.

```json
{
  "status": "active",
  "prefix": "string",
  "first_name": "string",
  "middle_name": "string",
  "last_name": "string",
  "title": "string",
  "date_of_birth": "2026-01-01",
  "profile_photo_url": "string",
  "phones": {
    "add": [{ "phone_number": "5551234567", "phone_isd_code": "+1", "label": "mobile", "is_primary": true }],
    "update": [{ "id": "PHONE_ID", "phone_number": "5551234567", "phone_isd_code": "+1", "label": "mobile", "is_primary": true }],
    "remove": ["PHONE_ID"]
  },
  "tags": ["string"],
  "social_pages": {
    "add": [{ "platform": "linkedin", "url": "https://linkedin.com/in/x" }],
    "update": [{ "id": "SOCIAL_ID", "platform": "linkedin", "url": "https://linkedin.com/in/x" }],
    "remove": ["SOCIAL_ID"]
  },
  "custom_fields": [{ "any": "json" }],
  "additional_data": { "any": "json" },
  "description": "string",
  "work_history": {
    "add": [{ "job_title": "string", "company": "string", "start_date": "Jan 2023", "end_date": null, "current": true }],
    "update": [{ "id": "WORK_ID", "job_title": "string", "company": "string", "start_date": "Jan 2023", "end_date": null, "current": true }],
    "remove": ["WORK_ID"]
  },
  "educational_history": {
    "add": [{ "university": "string", "degree": "string", "field_of_study": "string", "start_date": "Sep 2018", "end_date": "May 2022" }],
    "update": [{ "id": "EDU_ID", "university": "string", "degree": "string", "field_of_study": "string", "start_date": "Sep 2018", "end_date": "May 2022" }],
    "remove": ["EDU_ID"]
  },
  "skills": ["string"],
  "addresses": {
    "add": [
      {
        "place_id": "optional-string",
        "address_line1": "1 Main St",
        "address_line2": "Suite 100",
        "city": "New York",
        "state": "NY",
        "postal_code": "10001",
        "country": "United States",
        "latitude": 40.0,
        "longitude": -73.0,
        "address_type": "work",
        "address_data": {},
        "is_primary": true
      }
    ],
    "update": [
      {
        "id": "ADDRESS_ID",
        "place_id": "optional-string",
        "address_line1": "1 Main St",
        "address_line2": "Suite 100",
        "city": "New York",
        "state": "NY",
        "postal_code": "10001",
        "country": "United States",
        "latitude": 40.0,
        "longitude": -73.0,
        "address_type": "work",
        "address_data": {},
        "is_primary": true
      }
    ],
    "remove": ["ADDRESS_ID"]
  },
  "company_association": {
    "remove_associations": ["COMPANY_UUID"],
    "add_associations": [{ "company_id": "COMPANY_UUID", "is_primary": false }],
    "update_associations": [{ "company_id": "COMPANY_UUID", "is_primary": true }],
    "create_and_associate": { "name": "New Co LLC", "is_primary": true }
  }
}
```

### Supported scenarios (company association delta)

Use any combination of:

- unlink companies: `company_association.remove_associations[]`
- link existing companies: `company_association.add_associations[]`
- toggle primary without unlinking: `company_association.update_associations[]`
- create one company (by name) and link: `company_association.create_and_associate`

### Response

200 with the standard success envelope (no `data`).

______________________________________________________________________

## `POST /v1/contacts/{contact_id}/enrich` — Trigger contact enrichment

Queues enrichment for the contact using latest persisted data.

### Path params

- `contact_id`: UUID string

### Response

200 with the standard success envelope (no `data`).

______________________________________________________________________

## `DELETE /v1/contacts/{contact_id}` — Soft delete contact

### Path params

- `contact_id`: UUID string

### Response

200 with the standard success envelope (no `data`).
