# ADR 0002: Assets and custom fields — CRM reuse

|              |                                                                                 |
| ------------ | ------------------------------------------------------------------------------- |
| **Status**   | Accepted (V1)                                                                   |
| **Date**     | 2026-09-02                                                                      |
| **Flow doc** | [../assets-flow.md](../assets-flow.md)                                          |
| **Schema**   | `work_order.asset_categories`, `work_order.assets`, `work_order.form_templates` |

______________________________________________________________________

## Context

FM teams register equipment with categories, optional facility location, purchase/warranty metadata,
and **dynamic fields** (serial number, capacity, etc.). The prototype duplicated a `custom_fields` table
inside its app. Home Craft already has org-wide custom field definitions in `public.custom_fields`
(`user_service`).

Category-scoped fields (e.g. HVAC-only) must work without a new schema column on `custom_fields`.

______________________________________________________________________

## Decision

### Tables in `work_order` schema

| Table              | Role                                                |
| ------------------ | --------------------------------------------------- |
| `asset_categories` | Hierarchical taxonomy (`parent_id`)                 |
| `assets`           | Equipment row; `custom_fields jsonb` for **values** |
| `form_templates`   | Formily JSON for WO checklists                      |

### Reuse CRM custom field definitions

| Layer               | Location                                                                        |
| ------------------- | ------------------------------------------------------------------------------- |
| Definitions         | `public.custom_fields` with `entity_type = asset` (`EntityType.ASSET`)          |
| Values              | `work_order.assets.custom_fields` (FieldCell array)                             |
| Category filter     | `type_config.asset_category_ids` on definition                                  |
| CRUD API            | `user_service` `/v1/custom-fields`                                              |
| Validation on write | `AssetsService` → `CustomFieldService.validate_for_create` / `merge_for_update` |

Direct import of `CustomFieldService` (shared DB connection in monorepo).

### Form templates

Stored in WOM schema — tied to work orders, not CRM entities.

### File uploads

R2 presigned URLs via `GET .../upload/presigned-url`; paths stored on asset/invoice rows — not blobs in Postgres.

### Money

`purchase_cost_minor bigint` (paise) on assets.

______________________________________________________________________

## Consequences

**Positive:** Single source of truth for field definitions; FM admin UI can reuse custom-fields screens.

**Negative:** `work_order_service` depends on user_service Python module; split deploy would need HTTP validate API.

**Follow-ups:** Filter field list by `asset_category_ids` in FM asset form UI.

______________________________________________________________________

## Alternatives considered

| Alternative                      | Rejected because                           |
| -------------------------------- | ------------------------------------------ |
| `work_order.custom_fields` table | Drift from CRM definitions                 |
| HTTP-only validation in V1       | Monorepo shares DB; direct import simpler  |
| Snapshot defs on asset row       | Definitions change; validate at write time |
