# Companies API (`/v1/companies`)

Module: `apps/user_service/app/api/companies.py`

This is a **production-ready API reference** for the Companies module. It focuses on **complete, copy/pasteable payloads** and **all supported scenarios**.

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

## `POST /v1/companies` — Create company

Creates a company. Optionally links **one** contact (existing or created inline) and can set it as primary.

### Request body (all fields shown)

```json
{
  "name": "Acme Corp",
  "industry": "Software",
  "profile_photo_url": "https://example.com/logo.png",
  "portal_access": false,
  "email": "info@acme.com",
  "phones": [
    {
      "id": "optional-string",
      "phone_number": "5551234567",
      "phone_isd_code": "+1",
      "label": "work",
      "is_primary": true
    }
  ],
  "tags": ["string"],
  "websites": [
    {
      "id": "optional-string",
      "url": "https://acme.com",
      "type": "main",
      "is_primary": true
    }
  ],
  "billing_preferences": {
    "method": "string",
    "terms": "string"
  },
  "social_pages": [
    {
      "id": "optional-string",
      "platform": "linkedin",
      "url": "https://linkedin.com/company/acme"
    }
  ],
  "target_market_segments": ["string"],
  "current_tech_stack": ["string"],
  "preferred_communication_channels": ["string"],
  "industry_specific_terminologies": ["string"],
  "description": "string",
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
  "contact_association": {
    "add_association": {
      "contact_id": "CONTACT_UUID",
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

#### 1) Company only

Send the same payload as above but set:

```json
{ "contact_association": null }
```

#### 2) Link existing contact

```json
{
  "contact_association": {
    "add_association": { "contact_id": "CONTACT_UUID", "is_primary": true },
    "create_and_associate": null
  }
}
```

#### 3) Create contact inline and associate

```json
{
  "contact_association": {
    "add_association": null,
    "create_and_associate": {
      "contact": {
        "email": "jane@acme.com",
        "portal_access": false,
        "prefix": "Ms",
        "first_name": "Jane",
        "middle_name": null,
        "last_name": "Doe",
        "title": "GC",
        "date_of_birth": null,
        "profile_photo_url": null,
        "phones": [
          { "phone_number": "5551234567", "phone_isd_code": "+1", "label": "mobile", "is_primary": true }
        ],
        "tags": [],
        "social_pages": [],
        "websites": [],
        "custom_fields": [],
        "additional_data": {},
        "lead": null,
        "company_association": null,
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

## `GET /v1/companies` — List companies (DB)

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
      "name": "Acme Corp",
      "industry": "Software",
      "profile_photo_url": "string",
      "email": "string",
      "phones": [
        {
          "id": "string",
          "phone_number": "string",
          "phone_isd_code": "string",
          "label": "string",
          "is_primary": true
        }
      ],
      "contacts": [
        {
          "id": "UUID",
          "first_name": "string",
          "last_name": "string",
          "title": "string",
          "email": "string",
          "phones": [],
          "is_primary": false
        }
      ],
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

## `GET /v1/companies/search` — Search companies (Typesense)

### Query params

- `query` (required, min 2)
- `status` (optional): `active|inactive|prospect|deleted`
- `page` (default 1)
- `page_size` (default 20, max 100)

### Response

Same envelope and item shape as `GET /v1/companies`.

______________________________________________________________________

## `GET /v1/companies/{company_id}` — Company details

### Path params

- `company_id`: UUID string

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
    "name": "Acme Corp",
    "industry": "Software",
    "profile_photo_url": "string",
    "portal_access": false,
    "email": "string",
    "phones": [],
    "primary_contact_id": "UUID",
    "contacts": [
      {
        "id": "UUID",
        "first_name": "string",
        "last_name": "string",
        "title": "string",
        "email": "string",
        "phones": [],
        "is_primary": true
      }
    ],
    "tags": [],
    "websites": [],
    "billing_preferences": {},
    "social_pages": [],
    "custom_fields": [],
    "additional_data": {},
    "target_market_segments": [],
    "current_tech_stack": [],
    "preferred_communication_channels": [],
    "industry_specific_terminologies": [],
    "description": null,
    "enrichment_done": false,
    "enrichment_status": null,
    "enrichment_request_id": null,
    "last_enriched_at": null,
    "addresses": [],
    "leads": [],
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z"
  }
}
```

______________________________________________________________________

## `PATCH /v1/companies/{company_id}` — Update company

Updates company fields, nested list deltas, addresses delta, and optional contact association delta.

### Path params

- `company_id`: UUID string

### Request body (all fields shown)

All fields are optional; only provided fields are applied. The payload below shows **every field** and the **full nested shapes**.

```json
{
  "status": "active",
  "name": "Acme Corp",
  "industry": "Software",
  "profile_photo_url": "https://example.com/logo.png",
  "portal_access": false,
  "email": "info@acme.com",
  "phones": [
    {
      "id": "optional-string",
      "phone_number": "5551234567",
      "phone_isd_code": "+1",
      "label": "work",
      "is_primary": true
    }
  ],
  "tags": ["string"],
  "websites": {
    "add": [{ "url": "https://acme.com", "type": "main", "is_primary": true }],
    "update": [{ "id": "WEBSITE_ID", "url": "https://acme.com", "type": "main", "is_primary": true }],
    "remove": ["WEBSITE_ID"]
  },
  "billing_preferences": { "method": "string", "terms": "string" },
  "social_pages": {
    "add": [{ "platform": "linkedin", "url": "https://linkedin.com/company/acme" }],
    "update": [{ "id": "SOCIAL_ID", "platform": "linkedin", "url": "https://linkedin.com/company/acme" }],
    "remove": ["SOCIAL_ID"]
  },
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
  "target_market_segments": ["string"],
  "current_tech_stack": ["string"],
  "preferred_communication_channels": ["string"],
  "industry_specific_terminologies": ["string"],
  "description": "string",
  "custom_fields": [{ "any": "json" }],
  "additional_data": { "any": "json" },
  "sales_intelligence": { "any": "json" },
  "linked_pages": {
    "add": [{ "page_name": "string", "page_url": "https://example.com" }],
    "update": [{ "id": "LINKED_PAGE_ID", "page_name": "string", "page_url": "https://example.com" }],
    "remove": ["LINKED_PAGE_ID"]
  },
  "products": {
    "add": [{ "name": "string", "url": "https://example.com", "description": "string" }],
    "update": [{ "id": "PRODUCT_ID", "name": "string", "url": "https://example.com", "description": "string" }],
    "remove": ["PRODUCT_ID"]
  },
  "key_people": {
    "add": [{ "name": "string", "title": "string", "linkedin": "https://linkedin.com/in/x" }],
    "update": [{ "id": "KEY_PERSON_ID", "name": "string", "title": "string", "linkedin": "https://linkedin.com/in/x" }],
    "remove": ["KEY_PERSON_ID"]
  },
  "contact_association": {
    "remove_associations": ["CONTACT_UUID"],
    "add_associations": [{ "contact_id": "CONTACT_UUID", "is_primary": false }],
    "update_associations": [{ "contact_id": "CONTACT_UUID", "is_primary": true }],
    "create_and_associate": {
      "contact": {
        "email": "new@acme.com",
        "portal_access": false,
        "prefix": null,
        "first_name": "New",
        "middle_name": null,
        "last_name": "Person",
        "title": null,
        "date_of_birth": null,
        "profile_photo_url": null,
        "phones": [],
        "tags": [],
        "social_pages": [],
        "websites": [],
        "custom_fields": [],
        "additional_data": {},
        "lead": null,
        "company_association": null,
        "addresses": []
      },
      "is_primary": true
    }
  }
}
```

### Supported scenarios (contact association delta)

Use any combination of:

- unlink contacts: `contact_association.remove_associations[]`
- link existing contacts: `contact_association.add_associations[]`
- toggle primary without unlinking: `contact_association.update_associations[]`
- create one contact and link: `contact_association.create_and_associate`

### Response

200 with the standard success envelope (no `data`).

______________________________________________________________________

## `DELETE /v1/companies/{company_id}` — Soft delete company

### Path params

- `company_id`: UUID string

### Response

200 with the standard success envelope (no `data`).
