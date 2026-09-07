# Assets Flow — Context & Change Guide

> **Status: V1 implemented.**
> Architecture: [ADR 0002 — Assets and custom fields](./adr/0002-assets-and-custom-fields.md)
> Part of [Work Order Service](./README.md).
> Schema: [work-order-management-schema.md](../../../../ats-home-craft-supabase/docs/work-order-management-schema.md).

- **Service:** `apps/work_order_service` (port 5001)
- **Staff API prefix:** `/v1/projects/{project_id}/asset-categories`, `/assets`, `/form-templates`
- **Custom field definitions:** `/v1/custom-fields?entity_type=asset` on **`user_service`** (port 5000)
- **DB tables:** `work_order.asset_categories`, `work_order.assets`, `work_order.form_templates`

______________________________________________________________________

## 1. What this flow does

Facility managers register **equipment and infrastructure** (HVAC, DG sets, fire pumps, gym gear) under a
**project**. Assets belong to a **category** tree, may link to a **facility** (location), and carry
**custom field values** validated against org-wide field definitions.

**Form templates** (Formily JSON schema) define checklists used on work orders — pre-start and completion forms.

### Business rules (must enforce)

| Rule                       | Enforcement                                                                         |
| -------------------------- | ----------------------------------------------------------------------------------- |
| **Tenancy**                | Every row has `organization_id` + `project_id`; all queries scoped                  |
| **Soft delete**            | `record_status = deleted`; never hard-delete operational assets                     |
| **Custom field defs**      | Stored in `public.custom_fields` with `entity_type = asset` — not duplicated in WOM |
| **Custom field values**    | Stored in `assets.custom_fields` jsonb (FieldCell array)                            |
| **Category-scoped fields** | Filter defs where `type_config.asset_category_ids` includes asset's category        |
| **Validate on write**      | `AssetsService` calls `CustomFieldService.validate_for_create` / `merge_for_update` |
| **Facility link**          | Optional `facility_id` → `public.facilities(id)`                                    |
| **Money on assets**        | Purchase cost stored as `purchase_cost_minor` (paise); convert at API boundary      |

### Screen → capability map

**FM admin UI**

| Screen / action          | Capability                                                           |
| ------------------------ | -------------------------------------------------------------------- |
| Category tree            | `GET /projects/{project_id}/asset-categories`                        |
| Create category          | `POST /projects/{project_id}/asset-categories`                       |
| Asset list + filters     | `GET /projects/{project_id}/assets?category_id=&search=&status=`     |
| Asset detail             | `GET /projects/{project_id}/assets/{id}`                             |
| Create asset             | `POST /projects/{project_id}/assets`                                 |
| Update asset             | `PATCH /projects/{project_id}/assets/{id}`                           |
| Delete asset             | `DELETE /projects/{project_id}/assets/{id}`                          |
| Custom field definitions | `GET /v1/custom-fields?entity_type=asset` (user_service)             |
| Form template list       | `GET /projects/{project_id}/form-templates`                          |
| Form builder             | `POST/PATCH /projects/{project_id}/form-templates`                   |
| Upload photos/docs       | Presigned URL → `GET .../upload/presigned-url` then client PUT to R2 |

______________________________________________________________________

## 2. Architecture (layers)

```
HTTP → asset_categories.py / assets.py / form_templates.py
     → AssetCategoriesService / AssetsService / FormTemplatesService
     → *Repository (asyncpg)
     → work_order.asset_categories | assets | form_templates
```

### File map

| Concern                         | File                                                                                                         |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Category API                    | `app/api/asset_categories.py`                                                                                |
| Asset API                       | `app/api/assets.py`                                                                                          |
| Form template API               | `app/api/form_templates.py`                                                                                  |
| Presigned upload                | `app/api/presigned_url.py`                                                                                   |
| Category service                | `app/services/asset_categories_service.py`                                                                   |
| Asset service (+ custom fields) | `app/services/assets_service.py`                                                                             |
| Form template service           | `app/services/form_templates_service.py`                                                                     |
| Repositories                    | `app/db/repositories/asset_categories_repository.py`, `assets_repository.py`, `form_templates_repository.py` |
| Custom field validation         | `apps/user_service/app/services/custom_field_service.py` (imported directly)                                 |
| Entity type enum                | `EntityType.ASSET` in `apps/user_service/app/schemas/enums/project_fields.py`                                |

______________________________________________________________________

## 3. Data model

### Tables

| Table                         | Purpose                                                                                             |
| ----------------------------- | --------------------------------------------------------------------------------------------------- |
| `work_order.asset_categories` | Hierarchical category tree (`parent_id` optional)                                                   |
| `work_order.assets`           | Equipment record; `custom_fields jsonb`, `asset_category_id`, optional `facility_id`, `contract_id` |
| `work_order.form_templates`   | Named Formily schema for WO checklists                                                              |

### Cross-schema references

| Column              | FK target                          |
| ------------------- | ---------------------------------- |
| `organization_id`   | `public.organizations(id)`         |
| `project_id`        | `public.projects(id)`              |
| `facility_id`       | `public.facilities(id)` (optional) |
| `asset_category_id` | `work_order.asset_categories(id)`  |

Custom field **definitions** live in `public.custom_fields` — see
[user_service custom-fields API](../../user_service/docs/api/contacts.md) pattern; use `entity_type=asset`.

Category-scoped fields use convention:

```json
{
  "type_config": {
    "asset_category_ids": ["<uuid>", "..."]
  }
}
```

______________________________________________________________________

## 4. Staff flow (step by step)

### 4.1 Create category

```http
POST /v1/projects/{project_id}/asset-categories
Authorization: Bearer <jwt>
Content-Type: application/json

{
  "name": "HVAC",
  "description": "Heating, ventilation, air conditioning",
  "parent_id": null
}
```

Requires `work_order_management.edit` + staff project access.

### 4.2 Define custom fields (user_service)

```http
POST /v1/custom-fields
Authorization: Bearer <jwt>

{
  "entity_type": "asset",
  "field_name": "Serial Number",
  "field_key": "serial_number",
  "field_type": "text",
  "is_required": true,
  "type_config": { "asset_category_ids": ["<hvac-category-uuid>"] }
}
```

Requires `custom_fields_management.*` (auto-implied when role has `work_order_management.*`).

### 4.3 Create asset

```http
POST /v1/projects/{project_id}/assets
Authorization: Bearer <jwt>

{
  "name": "Chiller Plant — Tower A",
  "asset_category_id": "<uuid>",
  "facility_id": "<uuid>",
  "status": "operational",
  "custom_fields": [
    { "field_id": "<uuid>", "value": "SN-12345" }
  ],
  "purchase_cost_minor": 250000000,
  "currency": "INR"
}
```

`AssetsService.create`:

1. Validates custom fields via `CustomFieldService.validate_for_create(..., EntityType.ASSET)`.
1. Inserts row scoped to org + project.

### 4.4 Upload asset document

```http
GET /v1/projects/{project_id}/upload/presigned-url
  ?file_name=warranty.pdf
  &path={org_id}/{project_id}/assets/{asset_id}
  &bucket=<r2-bucket>
  &content_type=application/pdf
Authorization: Bearer <jwt>
```

Client PUTs file to returned URL, then stores path in asset PATCH body (`file_paths` or photos array per UI convention).

______________________________________________________________________

## 5. Where to change things

| Change                   | Edit                                                                      |
| ------------------------ | ------------------------------------------------------------------------- |
| New asset filter         | `assets_repository.py` list query + `assets.py` query params              |
| New asset column         | Supabase migration → repository INSERT/UPDATE → API accepts field in body |
| Custom field rules       | `CustomFieldService` in user_service (shared)                             |
| Category tree logic      | `asset_categories_service.py`                                             |
| Form template validation | `form_templates_service.py`                                               |
| RBAC permission          | `libs/shared_utils/common_query.py` + route `permission_codes=`           |

______________________________________________________________________

## 6. Related flows

- Assets linked on **contracts** → [contracts-scheduler-flow.md](./contracts-scheduler-flow.md)
- Form templates assigned on **work orders** → [work-orders-flow.md](./work-orders-flow.md)
- Custom fields shared pattern → [contact-onboarding-flow.md](../../user_service/docs/contact-onboarding-flow.md) FieldCell model
