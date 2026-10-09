# Buy & Sell Flow — Context & Change Guide

> Schema and decisions: [ADR 0018](./adr/0018-buy-and-sell.md).
>
> Society, towers, and flats come from [project-setup-flow.md](./project-setup-flow.md). This feature
> does not add columns to those tables.

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **Resident API:** `/v1/marketplace` (org-scoped)
- **Staff API:** `/v1/marketplace/admin` (org-scoped; optional `project_id` filter)
- **Catalog:** `app/data/marketplace_catalog.json`
- **DB schema:** `ats-home-craft-supabase` — `20261006120000_marketplace_enums.sql`, `20261006121000_marketplace_tables.sql`, `20261008140000_marketplace_permissions.sql`

______________________________________________________________________

## 1. What this flow does

A resident lists a household item for **sale** or as a **giveaway**, and browses what others in the organization have listed. Money never moves through the app. Buyers and sellers coordinate offline (phone or in person). There is **no in-app chat**, **no reports**, and **no wanted requests**. Staff moderate posted listings on an organization-scoped admin board (summary, search, filters, drawer, remove), with an optional society (`project_id`) filter.

The prototype flow map, adjusted for this scope:

| Flow-map node      | In scope                                                                            |
| ------------------ | ----------------------------------------------------------------------------------- |
| Buy & Sell home    | Search field, category grid (`/catalog`), recently listed (`/listings?sort=newest`) |
| Search             | Text query only (`q` on list). **No** recent searches or popular chips              |
| Category & filters | Category grid + filter sheet (sort, price, condition, where)                        |
| Saved items        | Bookmarked listings                                                                 |
| Listing detail     | Price, seller, collection                                                           |
| Messages           | **Out of scope** (no threads or messages)                                           |
| Chat with seller   | **Out of scope**                                                                    |
| 1 · Category       | Pick category and subtype                                                           |
| 2 · Post details   | Media, title, price or giveaway, pickup flat, condition                             |
| 3 · Preview        | Preview, post                                                                       |
| Live confirmation  | "You're live"                                                                       |
| My listings        | Live, draft, sold, expired, removed (seller remove only)                            |
| Marked as sold     | Pick buyer from society residents, optional private rating                          |
| Staff listings     | Summary cards, search, status + category, flat table, drawer, remove                |

### Business rules (must enforce)

| Rule                                               | Enforcement                                                                                                                                                                                        |
| -------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Org-scoped resident API**                        | Resident queries filter `organization_id` from auth. Resident URLs never use `project_id`                                                                                                          |
| **Org-scoped staff API**                           | Staff routes are `/v1/marketplace/admin/*` with `ensure_staff_project_access_optional`. `project_id` is an optional query filter                                                                   |
| **Global browse**                                  | GET listing feeds are org-wide. No `unit_id` on read routes; flat numbers follow §6 (seller always sees own flat)                                                                                  |
| **Unit on writes**                                 | `unit_id` in the body on create, save, patch, remove, renew, relist, and mark-sold. Publish uses the listing's stored pickup unit. Active `contact_units`; seller actions need Owner/Tenant/Family |
| **Media immutable**                                | Set only on create. No `/listings/{id}/media` routes                                                                                                                                               |
| **Owner, Tenant, or Family**                       | Role read from `contact_roles`. Guest / Vendor / Staff cannot post                                                                                                                                 |
| **Sale needs a price; giveaway must not have one** | Check constraint + service validation                                                                                                                                                              |
| **At least 2 media items to publish**              | Service count on `marketplace_listings.media` jsonb                                                                                                                                                |
| **Live for 30 days**                               | `expires_at` set at publish. Job flips `live` → `expired`                                                                                                                                          |
| **Flat only inside the listing's society**         | Response builder drops `unit_label` for other projects, and when `show_flat_number` is false                                                                                                       |
| **Phone never returned**                           | Repository select list omits `contacts.phones`                                                                                                                                                     |
| **Mark sold**                                      | `buyer_contact_id` must be an active resident (`contact_units`) in the **same project** as the listing. No thread prerequisite                                                                     |
| **Feedback stays off the public listing**          | Stored in `marketplace_sale_feedback`, never joined into browse or detail                                                                                                                          |
| **Seller can edit a live post**                    | `PATCH` while `draft`, `live`, or `removed`. A live row stays `live` and must remain publish-valid. Category and subtype stay as posted                                                            |
| **Seller can take their own post down**            | `POST .../remove` on a live listing                                                                                                                                                                |
| **Sold hidden from the seller after 1 year**       | My listings filters `sold_at >= now() - interval '1 year'`                                                                                                                                         |
| **Media are paths**                                | Presigned upload, then metadata in `listings.media` jsonb. No blob                                                                                                                                 |
| **Categories from JSON**                           | `GET /marketplace/catalog`. Not Postgres                                                                                                                                                           |

______________________________________________________________________

## 2. What this reads from project setup (no new columns)

From [project-setup-flow.md](./project-setup-flow.md) §3 and [resident onboarding](../../../../ats-home-craft-supabase/docs/resident-onboarding-schema.md):

| Need                           | Source                                                                                       |
| ------------------------------ | -------------------------------------------------------------------------------------------- |
| Society name                   | `projects.name` (via listing `project_id` / pickup unit)                                     |
| Tower count in the header      | `count(*)` from `towers` where `project_id` = viewer's project (from `unit_id`)              |
| Nearby societies               | `projects.latitude`, `projects.longitude`, same `organization_id`, `status = active`, ≤ 5 km |
| Tower name and collection pin  | `towers.name`, `towers.latitude`, `towers.longitude`                                         |
| "Closest to me"                | Distance from the viewer's tower coordinates to the listing's `tower_id` coordinates         |
| Pickup flat dropdown           | Active `contact_units` → `units.code` / `units.unit_label` + `projects.name`                 |
| Seller role and "Member since" | Active `contact_roles` on that unit (`started_at` year)                                      |
| Seller display name and photo  | `contacts.first_name`, `last_name`, `profile_photo_url`                                      |

`units.code` is the flat label the prototype writes as "B-1104" / "1104". Prefer `unit_label` when it is set, otherwise `code`.

Do **not** reuse `vehicles`, `facilities`, `notices`, or `pets` for a listing. A cycle for sale is a marketplace row whose category happens to be Vehicles.

______________________________________________________________________

## 3. New tables

**Three tables.** Every table has `organization_id uuid NOT NULL` and is queried with that tenant id. Primary keys are `uuid`. Timestamps are `timestamptz`. Media lives on the listing row as `jsonb`, not a child table.

Listings still store `project_id` (society of the pickup flat). That is data denormalized from `units.project_id`, not an API path segment.

Enums (migration `20261006120000_marketplace_enums.sql`):

```sql
CREATE TYPE public.marketplace_listing_kind AS ENUM ('sale', 'giveaway');
CREATE TYPE public.marketplace_listing_status AS ENUM ('draft', 'live', 'sold', 'expired', 'removed');
CREATE TYPE public.marketplace_item_condition AS ENUM ('lightly_used', 'well_used', 'needs_repair');
CREATE TYPE public.marketplace_sale_rating AS ENUM ('smooth', 'fine', 'had_trouble');
```

### `marketplace_listings`

| Column                     | Type                           | Notes                                                                  |
| -------------------------- | ------------------------------ | ---------------------------------------------------------------------- |
| `id`                       | uuid PK                        |                                                                        |
| `organization_id`          | uuid NOT NULL                  | Tenant                                                                 |
| `project_id`               | uuid NOT NULL                  | FK `projects`. Society of the pickup flat                              |
| `unit_id`                  | uuid NOT NULL                  | FK `units`. Pickup flat                                                |
| `tower_id`                 | uuid                           | Denormalized from `units.tower_id` for distance sort                   |
| `seller_contact_id`        | uuid NOT NULL                  | FK `contacts`. Set from the caller, not from the body                  |
| `category`                 | text NOT NULL                  | Catalog slug, e.g. `furniture`. Display name comes from the catalog    |
| `subtype`                  | text                           | Catalog subtype slug, e.g. `tables_desks`. Required on create          |
| `kind`                     | marketplace_listing_kind       | `sale` or `giveaway`                                                   |
| `status`                   | marketplace_listing_status     | Default `draft`                                                        |
| `title`                    | text                           | Required to publish. 1–80 chars                                        |
| `description`              | text                           | Required to publish. 1–1000 chars                                      |
| `purchase_year`            | smallint                       | Required to publish. 1980 … current year                               |
| `price_amount`             | numeric(12,2)                  | Required and > 0 for a live sale. Null for giveaway                    |
| `original_price_amount`    | numeric(12,2)                  | Struck-through "new" price. If set on a sale, must be > `price_amount` |
| `negotiable`               | boolean NOT NULL default false | Sale only. Giveaway stores false                                       |
| `brand`                    | text                           | Optional, 1–80                                                         |
| `condition`                | marketplace_item_condition     | Required to publish                                                    |
| `product_url`              | text                           | Optional http(s) link to the new product                               |
| `show_flat_number`         | boolean NOT NULL default false | Same-society residents see the flat when true                          |
| `original_bill_available`  | boolean NOT NULL default false | Detail line "Original bill"                                            |
| `published_at`             | timestamptz                    |                                                                        |
| `expires_at`               | timestamptz                    | `published_at + 30 days`, extended by renew                            |
| `renewal_count`            | integer NOT NULL default 0     |                                                                        |
| `sold_at`                  | timestamptz                    |                                                                        |
| `buyer_contact_id`         | uuid                           | FK `contacts`. Set by mark-sold                                        |
| `removed_at`               | timestamptz                    | Set on seller remove (`POST .../remove`)                               |
| `removal_note`             | text                           | Required when `status = removed` (seller reason)                       |
| `removed_by_user_id`       | uuid                           | FK `auth.users`. Resident session user on remove                       |
| `media`                    | jsonb NOT NULL default `[]`    | Ordered image/video metadata. Max 8                                    |
| `created_at`, `updated_at` | timestamptz                    |                                                                        |

Checks:

- `kind = 'giveaway'` ⇒ `price_amount` is null and `negotiable` is false.
- `kind = 'sale' AND status = 'live'` ⇒ `price_amount > 0`.
- `original_price_amount` is null or greater than `price_amount`.
- `status = 'sold'` ⇒ `buyer_contact_id` and `sold_at` are set.
- `status = 'removed'` ⇒ `removed_by_user_id`, `removal_note`, and `removed_at` are set.
- `media` is a JSON array of 0–8 objects.

Indexes:

- `(organization_id, project_id, status, published_at DESC)` for society feed.
- `(organization_id, seller_contact_id, status, updated_at DESC)` for My listings and the draft strip.
- Partial `(expires_at)` where `status = 'live'` for the expiry job.
- `(organization_id, tower_id)` for "My tower".

Each `media` element:

| Key            | Notes                                                       |
| -------------- | ----------------------------------------------------------- |
| `type`         | `image` or `video`                                          |
| `path`         | Storage path from the presigned upload                      |
| `file_type`    | `image/jpeg`, `image/png`, or `video/mp4` (must match type) |
| `preview_path` | Required for `video` (poster). Optional for `image`         |
| `description`  | Optional caption, ≤ 200 chars                               |
| `order`        | Display order, 1-based                                      |

Service cap: **8** items (mix of photos and videos). Publish requires **≥ 2**. Card cover is the first image path, or the first item's `preview_path`. Media is set only on create.

### `marketplace_saved_items`

| Column            | Type          | Notes                       |
| ----------------- | ------------- | --------------------------- |
| `id`              | uuid PK       |                             |
| `organization_id` | uuid NOT NULL |                             |
| `contact_id`      | uuid NOT NULL | The resident who bookmarked |
| `listing_id`      | uuid NOT NULL |                             |
| `created_at`      | timestamptz   |                             |

Unique `(contact_id, listing_id)`.

### `marketplace_sale_feedback`

| Column              | Type                    | Notes                           |
| ------------------- | ----------------------- | ------------------------------- |
| `id`                | uuid PK                 |                                 |
| `organization_id`   | uuid NOT NULL           |                                 |
| `listing_id`        | uuid NOT NULL UNIQUE    | One rating per sold listing     |
| `seller_contact_id` | uuid NOT NULL           |                                 |
| `buyer_contact_id`  | uuid NOT NULL           |                                 |
| `rating`            | marketplace_sale_rating | `smooth`, `fine`, `had_trouble` |
| `created_at`        | timestamptz             |                                 |

Resident browse, detail, and My listings queries must not join this table. Staff list/detail also omit this table.

### Not a table

| Thing                       | Where it lives                                                                  |
| --------------------------- | ------------------------------------------------------------------------------- |
| Categories and subtypes     | `app/data/marketplace_catalog.json`                                             |
| Nearby societies            | Computed from `projects` coordinates, radius `MARKETPLACE_NEARBY_RADIUS_KM = 5` |
| Prohibited-item rules copy  | `app/locales/en.json` → `marketplace.rules`                                     |
| "Earned"                    | Sum of `price_amount` on the seller's `sold` rows in the last 365 days          |
| Draft gap ("add 2 photos…") | Computed from `jsonb_array_length(media)` and null price                        |

______________________________________________________________________

## 4. Architecture (layers)

```
HTTP → API router → Service → Repository (SQL) → Postgres
                      │
                      ├── MarketplaceCatalogService (JSON, read-only)
                      └── presigned upload (existing)
```

### File map (to implement)

| Concern            | File                                                                                                                         |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| Resident routes    | `app/api/marketplace.py`                                                                                                     |
| Staff routes       | `app/api/marketplace_admin.py`                                                                                               |
| Route registration | `app/api/routes.py`                                                                                                          |
| Orchestration      | `app/services/marketplace_service.py`                                                                                        |
| Catalog            | `app/services/marketplace_catalog_service.py`                                                                                |
| Nearby + distance  | `app/services/marketplace_geo.py` (`MARKETPLACE_NEARBY_RADIUS_KM`)                                                           |
| Expiry job         | `app/jobs/expire_marketplace_listings.py`                                                                                    |
| SQL                | `app/db/repositories/marketplace_repository.py`                                                                              |
| Schemas            | `app/schemas/marketplace.py`                                                                                                 |
| Enums              | `app/schemas/enums/marketplace.py`                                                                                           |
| Static data        | `app/data/marketplace_catalog.json`                                                                                          |
| i18n               | `app/locales/en.json` under `marketplace.*` and `notifications.push.marketplace.*`                                           |
| Tests              | `tests/unit/test_marketplace_service.py`, `tests/unit/test_marketplace_catalog_service.py`, `tests/integration/marketplace/` |

______________________________________________________________________

## 5. Screen → API

**Browse reads are org-global.** No `GET` route accepts `unit_id`. Feeds include every live listing in the organization; flat display on cards follows §6 without a viewer unit.

**Writes require `unit_id` in the JSON body** (not query params): create, save/unsave, patch, remove, renew, relist, and mark-sold. Publish has no body; it authorizes the pickup unit already stored on the listing. Pickup changes on patch use optional `pickup_unit_id`. Authorization uses active `contact_units`; posting actions require Owner / Tenant / Family on that unit.

**Pagination** on every listing collection: `page` (default 1), `page_size` (defaults vary). Browse, saved, and my listings use `list_response` (`data`, `total`, `page`, `page_size`, `total_pages`).

### Buy & Sell home (client composition)

There is **no** `GET /marketplace/home`. The landing screen loads:

| Element         | API                                                                                              |
| --------------- | ------------------------------------------------------------------------------------------------ |
| Search field    | Navigates to search UI; submit uses list route with `q` only                                     |
| Draft strip     | `GET /v1/marketplace/me/listings?status=draft&page=1&page_size=1` (or client cache after create) |
| Category grid   | `GET /v1/marketplace/catalog`                                                                    |
| Recently listed | `GET /v1/marketplace/listings?sort=newest&page=&page_size=`                                      |
| Bookmark        | `POST /v1/marketplace/listings/{id}/save` `{ "unit_id", "saved": true \| false }`                |
| **+ Sell**      | Single create (below)                                                                            |

Card fields: cover path, `is_new_today`, price, original price, title, tower, flat (subject to §6), `saved`.

### Search

| Element        | API                                                |
| -------------- | -------------------------------------------------- |
| Submit a query | `GET /v1/marketplace/listings?q=&page=&page_size=` |

There is **no** suggestions endpoint, **no** recent-search storage, **no** popular chips API, and **no** "Not finding it?" / wanted-request route. The client must not call removed routes (`/search/suggestions`, `/search/recents`, `/wanted-requests`).

### Category & filters

| Element                             | API                                                                                     |
| ----------------------------------- | --------------------------------------------------------------------------------------- |
| Category browse                     | `GET /v1/marketplace/listings?category=furniture&page=&page_size=` → `total` (org-wide) |
| Sticky chips (Newest, Under ₹5,000) | Same list route, repeated query params                                                  |
| Filter sheet                        | Query params below                                                                      |

| Param               | Values                                                   |
| ------------------- | -------------------------------------------------------- |
| `category`          | Catalog slug (`furniture`, `electronics`, …)             |
| `subtype`           | Catalog subtype slug (`tables_desks`, …)                 |
| `q`                 | Search text (title/description ILIKE)                    |
| `sort`              | `newest` (default), `price_asc`, `price_desc`, `closest` |
| `price_band`        | `free`, `under_5000`, `5000_20000`, `above_20000`        |
| `condition`         | Repeatable. `lightly_used`, `well_used`, `needs_repair`  |
| `page`, `page_size` | Default page size 20                                     |

Giveaways sort with the chosen `sort`. Price sort puts giveaways (no price) at the free end: first for `price_asc`, last for `price_desc`.

`price_band=free` is `kind = giveaway`. The rupee bands apply to sales only.

### Saved items

`GET /v1/marketplace/saved?page=&page_size=` returns live bookmarks (org-wide).

### Listing detail and service detail

`GET /v1/marketplace/listings/{id}` (no `unit_id`).

| Detail line              | Source                                                            |
| ------------------------ | ----------------------------------------------------------------- |
| Available                | `status = live`                                                   |
| Listed 2 days ago        | `published_at` only (no view count, no "people asking")           |
| ₹4,500 and struck ₹9,000 | `price_amount`, `original_price_amount`. Giveaway renders as Free |
| Negotiable               | `negotiable`                                                      |
| ₹9,000 new · bought 2025 | `original_price_amount`, `purchase_year`                          |
| Description              | `description`                                                     |
| Condition, brand         | Columns                                                           |
| Age                      | `current_year - purchase_year`                                    |
| Original bill            | `original_bill_available`                                         |
| Seller name, role, etc.  | Contact + role                                                    |
| Collection pin           | Tower coordinates when present                                    |
| Collection label         | Tower + society. Flat under visibility rules                      |
| More from seller         | Other `live` listings by `seller_contact_id` in the same project  |
| Bookmark                 | Save / unsave                                                     |

There is **no** Message / Chat CTA backed by this service and **no** report endpoint.

### Seller actions on their own listing

| Action                  | API                                                                                             | Effect                                                             |
| ----------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Edit listing            | `PATCH /v1/marketplace/listings/{id}`                                                           | Step-2 fields. Status stays `live`. Category/subtype cannot change |
| Mark as sold            | `POST .../mark-sold`                                                                            | See Marked as sold                                                 |
| Share to community feed | Out of scope                                                                                    | Hide in client until a feed exists                                 |
| Remove listing          | `POST /v1/marketplace/listings/{id}/remove` `{ "unit_id", "removal_note" }` → `status: removed` | Permanent. Create a new listing to post again                      |

### Sell flow (3 steps + confirmation)

| Step              | UI                         | API                                                                                                                                                                                        |
| ----------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1 · Category      | Pick category / subtype    | `GET /v1/marketplace/catalog` (client only)                                                                                                                                                |
| 2 · New post      | Save or Continue           | `POST /v1/marketplace/listings` on first save (unpublished); `PATCH /v1/marketplace/listings/{id}` to update the same listing                                                              |
| 3 · Preview       | Edit details, Post listing | `GET /v1/marketplace/listings/{id}`; `PATCH` if needed; **`POST /v1/marketplace/listings/{id}/publish`** (no body; pickup unit is the one stored on the listing) — **only** way to go live |
| Live confirmation | Post another / My listings | Post another → step 1 then **`POST /listings`** again; My listings → `GET /v1/marketplace/me/listings`                                                                                     |

**Create** (`POST /listings`): `unit_id`, `category`, `subtype`, step-2 fields, and `media[]` in one body. Row is created unpublished (`status = draft`). Media is set only here; no media routes afterward. Go live with **`POST /listings/{id}/publish`**.

Pickup flat: contact onboarding properties (Owner / Tenant / Family)—no marketplace pickup route.

### Edit listing (no media)

`PATCH /v1/marketplace/listings/{id}` — `{ "unit_id", ...fields, "pickup_unit_id"? }` (media unchanged).

### My listings

`GET /v1/marketplace/me/listings?status=all|live|draft|sold|past&page=&page_size=` (seller's posts org-wide; no `unit_id`)

| Card    | Fields                                    | Action                     |
| ------- | ----------------------------------------- | -------------------------- |
| Live    | Days left                                 | —                          |
| Draft   | Gap sentence                              | Opens step 2 (`PATCH`)     |
| Sold    | Buyer name, tower, flat, `sold_at`, price | None                       |
| Expired | "Ran for 30 days"                         | —                          |
| Removed | Seller removed                            | None. Create a new listing |

### Marked as sold

`POST /v1/marketplace/listings/{id}/mark-sold`

```json
{
  "unit_id": "<seller unit>",
  "buyer_contact_id": "<active resident in listing project>",
  "rating": "smooth"
}
```

`rating` is optional. Buyer picker: `GET /v1/marketplace/listings/{id}/buyer-candidates?unit_id=` — active `contact_units` in the listing's project excluding the seller.

Response: item title, buyer public name, buyer flat, `price_amount`.

______________________________________________________________________

## 6. Flat visibility (response builder)

Apply in `visible_flat(listing, viewer)` on every card, detail, and preview.

| Viewer                                               | Result                          |
| ---------------------------------------------------- | ------------------------------- |
| Seller                                               | Always the flat                 |
| Active resident of the listing's project, toggle on  | Flat                            |
| Active resident of the listing's project, toggle off | Tower + society only            |
| Resident of another project                          | Tower + society. Never the flat |

There is no share-flat action without chat.

______________________________________________________________________

## 7. Jobs and notifications

Daily job `expire_marketplace_listings`:

1. `status = live AND expires_at <= now()` → `expired`.

No wanted-request push and no expiry reminder push.

______________________________________________________________________

## 8. Catalog file

Source of truth: `app/data/marketplace_catalog.json`. Every category has subtypes. Create and publish require both `category` and `subtype` slugs.

______________________________________________________________________

## 9. Out of scope

- In-app **messages**, **chat**, **threads**
- **Reports**, Reported tab, and committee uphold/dismiss
- **Recent search**, **popular chips**, `marketplace_search_recents`, `marketplace_search_terms`
- **View counts** and "people asking"
- **Giveaway 24-hour boost** (`giveaway_boost_until`)
- **Expiry reminder** push (3 days before)
- Payments, phone reveal, in-app negotiation
- Share to community feed
- Staff **Removed by committee** filter (staff remove uses `removed`)
- Group by tower, All towers, Sort: tower & unit
- Export API and Settings

______________________________________________________________________

## 10. How to make common changes

| I want to…                | Change here                                             |
| ------------------------- | ------------------------------------------------------- |
| Add a category            | `app/data/marketplace_catalog.json`                     |
| Change 30-day live window | `marketplace_service.py` and the expiry job             |
| Change the nearby radius  | `MARKETPLACE_NEARBY_RADIUS_KM` in `marketplace_geo.py`  |
| Change who may post       | `contact_roles` check in `marketplace_service.py`       |
| Change flat visibility    | `visible_flat` only                                     |
| Change copy               | `app/locales/en.json` under `marketplace.*`             |
| Change staff filters      | `AdminMarketplaceListQuery` + `list_admin_listings` SQL |
| Change staff header cards | `get_admin_summary` in the repository                   |

______________________________________________________________________

## 11. Staff admin (organization-scoped)

Same layering as companies: JWT → `ensure_staff_project_access_optional` → `MarketplaceService` → `MarketplaceRepository`. `project_id` is never in the path.

**Permissions** (`20261008140000_marketplace_permissions.sql`):

| Code                          | Use                            | Default roles                     |
| ----------------------------- | ------------------------------ | --------------------------------- |
| `marketplace_management.view` | Summary, catalog, list, detail | community_admin, security, viewer |
| `marketplace_management.edit` | Remove a live listing          | community_admin                   |

### Tables used

| Table                       | Role for staff                                                                |
| --------------------------- | ----------------------------------------------------------------------------- |
| `marketplace_listings`      | Source of truth. List, summary, drawer, and `POST .../remove` update this row |
| `projects`                  | Society of the pickup unit (already on `listings.project_id`)                 |
| `towers`                    | Tower name on the card (search + display). Not a filter and not a sort        |
| `units`                     | Pickup unit label / code                                                      |
| `contacts`                  | Seller name and photo; remover name when the actor is a resident              |
| `contact_roles`             | Seller role on the unit (drawer)                                              |
| `organization_members`      | Remover display name when staff took the listing down                         |
| `project_permissions`       | Catalog rows for `marketplace_management.*`                                   |
| `project_role_permissions`  | Grants on default project roles                                               |
| `marketplace_saved_items`   | **Unused** on staff routes                                                    |
| `marketplace_sale_feedback` | **Unused** on staff routes                                                    |

No `marketplace_reports` table. There is no Reported tab.

### Screen → API

Prefix: `/v1/marketplace/admin`

| Screen element                                         | API                                                                                      |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------- |
| Header cards Active / Sold / Past / Removed or deleted | `GET /summary?project_id=` → `active_count`, `sold_count`, `past_count`, `removed_count` |
| Category filter                                        | `GET /catalog` then `GET /listings?category=`                                            |
| Search (item, resident, unit, tower)                   | `GET /listings?q=`                                                                       |
| Society filter                                         | Optional `project_id` on summary, list, detail, and remove                               |
| Status filter                                          | `GET /listings?status=all\|live\|sold\|past\|removed`                                    |
| Listings table                                         | `GET /listings?page=&page_size=` — flat, org-wide, `published_at` desc                   |
| Row click / drawer                                     | `GET /listings/{listing_id}`                                                             |
| Remove (live only)                                     | `POST /listings/{listing_id}/remove` `{ "removal_note" }`                                |

**Not implemented (hide in client):** Group by tower, All towers, Sort: tower & unit, Export, Settings, Reported tab, Restore, view counts, conversations.

Status mapping for the mock:

| Mock label           | Query `status` | Postgres `marketplace_listing_status` |
| -------------------- | -------------- | ------------------------------------- |
| All statuses         | `all`          | `live`, `sold`, `expired`, `removed`  |
| Active               | `live`         | `live`                                |
| Sold                 | `sold`         | `sold`                                |
| Past — expired       | `past`         | `expired`                             |
| Deleted by resident  | `removed`      | `removed` (seller **or** staff)       |
| Removed by committee | **omitted**    | no separate value                     |

Staff `POST .../remove` writes the same `removed` status as the seller. The listing leaves the Active card and appears under Removed. Removal is permanent (no restore).

List card fields: cover, title, category names, price (or Free), resident public name, unit label, tower name, posted time, days left (live), status, `can_remove`.

Drawer extra fields: media, description, price/condition/brand/age, collection pin, seller role, `listings_from_unit_total` / `listings_from_unit_active`, `removed_before_count`, `removal_note`, `history[]` derived from `published_at` / `sold_at` / `expires_at` / `removed_at` (no event table). Staff always see the pickup flat.
