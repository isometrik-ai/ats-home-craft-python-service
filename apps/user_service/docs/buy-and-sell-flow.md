# Buy & Sell Flow — Context & Change Guide

> **Status: Not yet implemented (ADR + flow spec).** No migrations or routes exist yet.
> Schema and decisions: [ADR 0019](./adr/0019-buy-and-sell.md).
>
> Society, towers, and flats come from [project-setup-flow.md](./project-setup-flow.md). This feature
> does not add columns to those tables.

- **Service:** `ats-home-craft-python-service` → `apps/user_service`
- **API prefix:** `/v1/marketplace` for residents and for the committee. No route is nested under `/v1/projects/{project_id}`
- **Catalog:** `app/data/marketplace_catalog.json`
- **DB schema:** `ats-home-craft-supabase` (migrations proposed, not written — see §3)

______________________________________________________________________

## 1. What this flow does

A resident lists a household item for **sale** or as a **giveaway** inside their society, and browses what neighbours (and nearby societies) have listed. Money never moves through the app. There is no in-app chat. The two people meet at the flat on their own.

The prototype's flow map, and the screen that implements each node:

| Flow-map node      | Screen                                                              |
| ------------------ | ------------------------------------------------------------------- |
| Buy & Sell home    | Society header, search, draft strip, category grid, recently listed |
| Search             | Query box only. No recents, no popular chips, no wanted request     |
| Category & filters | Category grid + filter sheet (sort, price, condition, where)        |
| Saved items        | Listings the resident bookmarked                                    |
| Listing detail     | Price, seller, collection, report                                   |
| Service detail     | Same detail when the category is Services                           |
| 1 · Category       | Pick a category, then a type when the category has types            |
| 2 · Post details   | Media, title, price or giveaway, pickup flat, condition             |
| 3 · Preview        | What neighbours will see, rules checkbox, post                      |
| Live confirmation  | "You're live"                                                       |
| My listings        | Live, draft, sold, expired, removed                                 |
| Marked as sold     | Buyer and a private rating                                          |

### Business rules (must enforce)

| Rule                                               | Enforcement                                                                                                                             |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **Unit-scoped seller**                             | Pickup `unit_id` must have an active `contact_units` row for the caller                                                                 |
| **Owner, Tenant, or Family**                       | Role read from `contact_roles`. Guest / Vendor / Staff cannot post                                                                      |
| **Sale needs a price; giveaway must not have one** | Check constraint + service validation                                                                                                   |
| **At least 2 media files to publish**              | Service count on `marketplace_listing_media`                                                                                            |
| **Live for 30 days**                               | `expires_at` set at publish. A list or detail read past that time sets `expired`                                                        |
| **Flat only inside the listing's society**         | Response builder drops `unit_label` for other projects, and when `show_flat_number` is false                                            |
| **Phone never returned**                           | Repository select list omits `contacts.phones`                                                                                          |
| **Report does not tell the seller**                | Insert report only. The seller sees `removal_note` on My listings after the committee takes it down                                     |
| **Feedback stays off the public listing**          | Stored in `marketplace_sale_feedback`, never joined into browse or detail                                                               |
| **Seller can edit a live post**                    | `PATCH` while `draft`, `live`, or `removed`. A live row stays `live` and must remain publish-valid. Category and subtype stay as posted |
| **Seller remove returns the post to draft**        | `POST .../actions` with `action: remove` on a live listing sets `draft`. It leaves the board. Committee takedown is a different status  |
| **Seller can edit that draft and restore it**      | `PATCH` while `draft`, then `POST .../actions` with `action: restore`. Restore requires the same checks as publish and sets `live`      |
| **Sold hidden from the seller after 1 year**       | My listings filters `sold_at >= now() - interval '1 year'`                                                                              |
| **Media are paths**                                | Presigned upload, then metadata row. No blob                                                                                            |
| **Categories from JSON**                           | `GET /marketplace/catalog`. Not Postgres                                                                                                |
| **Routes are not project-scoped**                  | Every path is `/v1/marketplace/...`. The client never sends `project_id`                                                                |

______________________________________________________________________

## 2. What this reads from project setup (no new columns)

From [project-setup-flow.md](./project-setup-flow.md) §3 and [resident onboarding](../../../../ats-home-craft-supabase/docs/resident-onboarding-schema.md):

| Need                           | Source                                                                                        |
| ------------------------------ | --------------------------------------------------------------------------------------------- |
| Society name                   | `projects.name`                                                                               |
| Tower count in the header      | `count(*)` from `towers` where `project_id` = the unit's project                              |
| Nearby societies               | `projects.latitude`, `projects.longitude`, same `organization_id`, `status = active`, ≤ 5 km  |
| Tower name and collection pin  | `towers.name`, `towers.latitude`, `towers.longitude`                                          |
| "Closest to me"                | Distance from the viewer's tower coordinates to the listing's `tower_id` coordinates          |
| Pickup flat dropdown           | Active `contact_units` → `units.code` / `units.unit_label` + `projects.name`                  |
| Seller role and "Member since" | Active `contact_roles` on that unit (`started_at` year)                                       |
| Seller display name and photo  | `contacts.first_name`, `last_name`, `profile_photo_url`                                       |
| Committee actor                | `project_members` + `marketplace_management.*` ([ADR 0011](./adr/0011-project-membership.md)) |

`units.code` is the flat label the prototype writes as "B-1104" / "1104". Prefer `unit_label` when it is set, otherwise `code`.

The listing row stores `project_id` because the pickup flat belongs to a society. That value is copied from `units.project_id`. It is not a path parameter and not a request field.

Do **not** reuse `vehicles`, `facilities`, `notices`, or `pets` for a listing. A cycle for sale is a marketplace row whose category happens to be Vehicles.

______________________________________________________________________

## 3. New tables

Five tables. Every table has `organization_id uuid NOT NULL` and is queried with that tenant id. Primary keys are `uuid`. Timestamps are `timestamptz`.

Enums (migration `20261006120000_marketplace_enums.sql`):

```sql
CREATE TYPE public.marketplace_listing_kind AS ENUM ('sale', 'giveaway');
CREATE TYPE public.marketplace_listing_status AS ENUM ('draft', 'live', 'sold', 'expired', 'removed');
CREATE TYPE public.marketplace_item_condition AS ENUM ('like_new', 'lightly_used', 'well_used', 'needs_repair');
CREATE TYPE public.marketplace_report_reason AS ENUM (
    'not_allowed', 'business_or_broker', 'sold_but_listed', 'something_else'
);
CREATE TYPE public.marketplace_report_status AS ENUM ('open', 'upheld', 'dismissed');
CREATE TYPE public.marketplace_sale_rating AS ENUM ('smooth', 'fine', 'had_trouble');
```

### `marketplace_listings`

| Column                     | Type                           | Notes                                                                   |
| -------------------------- | ------------------------------ | ----------------------------------------------------------------------- |
| `id`                       | uuid PK                        |                                                                         |
| `organization_id`          | uuid NOT NULL                  | Tenant                                                                  |
| `project_id`               | uuid NOT NULL                  | FK `projects`. Copied from the pickup unit. Not sent by the client      |
| `unit_id`                  | uuid NOT NULL                  | FK `units`. Pickup flat                                                 |
| `tower_id`                 | uuid                           | Denormalized from `units.tower_id` for distance sort                    |
| `seller_contact_id`        | uuid NOT NULL                  | FK `contacts`. Set from the caller, not from the body                   |
| `category`                 | text NOT NULL                  | Catalog display name, e.g. `Furniture`                                  |
| `subtype`                  | text                           | Catalog subtype, e.g. `Tables & desks`. Null when the category has none |
| `kind`                     | marketplace_listing_kind       | `sale` or `giveaway`                                                    |
| `status`                   | marketplace_listing_status     | Default `draft`                                                         |
| `title`                    | text                           | Required to publish. 1–80 chars                                         |
| `description`              | text                           | Required to publish. 1–1000 chars                                       |
| `purchase_year`            | smallint                       | Required to publish. 1980 … current year                                |
| `price_amount`             | numeric(12,2)                  | Required and > 0 for a live sale. Null for giveaway                     |
| `original_price_amount`    | numeric(12,2)                  | Struck-through "new" price. If set on a sale, must be > `price_amount`  |
| `negotiable`               | boolean NOT NULL default false | Sale only. Giveaway stores false                                        |
| `brand`                    | text                           | Optional, 1–80                                                          |
| `condition`                | marketplace_item_condition     | Required to publish                                                     |
| `product_url`              | text                           | Optional http(s) link to the new product                                |
| `show_flat_number`         | boolean NOT NULL default false | Same-society residents see the flat when true                           |
| `original_bill_available`  | boolean NOT NULL default false | Detail line "Original bill"                                             |
| `rules_accepted_at`        | timestamptz                    | Set at publish. Null blocks publish                                     |
| `published_at`             | timestamptz                    |                                                                         |
| `expires_at`               | timestamptz                    | `published_at + 30 days`, extended by renew                             |
| `renewal_count`            | integer NOT NULL default 0     |                                                                         |
| `sold_at`                  | timestamptz                    |                                                                         |
| `buyer_contact_id`         | uuid                           | FK `contacts`. Set by mark-sold                                         |
| `removed_at`               | timestamptz                    |                                                                         |
| `removal_note`             | text                           | Committee sentence shown on the seller's removed card                   |
| `removed_by_user_id`       | uuid                           | Staff `auth.users` id. Set only by committee uphold                     |
| `created_at`, `updated_at` | timestamptz                    |                                                                         |

Checks:

- `kind = 'giveaway'` ⇒ `price_amount` is null and `negotiable` is false.
- `kind = 'sale' AND status = 'live'` ⇒ `price_amount > 0`.
- `original_price_amount` is null or greater than `price_amount`.
- `status = 'sold'` ⇒ `buyer_contact_id` and `sold_at` are set.
- `status = 'removed'` ⇒ `removed_by_user_id` and `removal_note` are set. Seller remove does not use this status.

Indexes:

- `(organization_id, project_id, status, published_at DESC)` for the society feed.
- `(organization_id, seller_contact_id, status, updated_at DESC)` for My listings and the draft strip.
- Partial `(expires_at)` where `status = 'live'` for rows past their close time.
- `(organization_id, tower_id)` for "My tower".

### `marketplace_listing_media`

| Column            | Type                           | Notes                                       |
| ----------------- | ------------------------------ | ------------------------------------------- |
| `id`              | uuid PK                        |                                             |
| `organization_id` | uuid NOT NULL                  |                                             |
| `listing_id`      | uuid NOT NULL                  | FK `marketplace_listings` ON DELETE CASCADE |
| `path`            | text NOT NULL                  | Storage path from the presigned upload      |
| `file_type`       | text NOT NULL                  | `jpeg` or `png`                             |
| `size_bytes`      | integer NOT NULL               | ≤ 5_242_880                                 |
| `original_name`   | text                           |                                             |
| `sort_order`      | integer NOT NULL               |                                             |
| `is_cover`        | boolean NOT NULL default false | Partial unique: one cover per listing       |
| `created_at`      | timestamptz                    |                                             |

Service cap: **8** files. Publish requires **≥ 2** and exactly one cover. If the client never flags a cover, the lowest `sort_order` is the cover.

### `marketplace_saved_items`

| Column            | Type          | Notes                       |
| ----------------- | ------------- | --------------------------- |
| `id`              | uuid PK       |                             |
| `organization_id` | uuid NOT NULL |                             |
| `contact_id`      | uuid NOT NULL | The resident who bookmarked |
| `listing_id`      | uuid NOT NULL |                             |
| `created_at`      | timestamptz   |                             |

Unique `(contact_id, listing_id)`.

### `marketplace_reports`

| Column                | Type                      | Notes                                     |
| --------------------- | ------------------------- | ----------------------------------------- |
| `id`                  | uuid PK                   |                                           |
| `organization_id`     | uuid NOT NULL             |                                           |
| `project_id`          | uuid NOT NULL             | Copied from the listing. Not a path param |
| `listing_id`          | uuid NOT NULL             |                                           |
| `reporter_contact_id` | uuid NOT NULL             |                                           |
| `reason`              | marketplace_report_reason |                                           |
| `status`              | marketplace_report_status | Default `open`                            |
| `created_at`          | timestamptz               |                                           |
| `reviewed_at`         | timestamptz               |                                           |
| `reviewed_by_user_id` | uuid                      | Staff user                                |

Partial unique: one `open` report per `(listing_id, reporter_contact_id)`.

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

Resident browse, detail, and My listings queries must not join this table. The committee pattern query counts `had_trouble` per seller and returns sellers with **3 or more**.

### Not a table

| Thing                       | Where it lives                                                                  |
| --------------------------- | ------------------------------------------------------------------------------- |
| Categories and subtypes     | `app/data/marketplace_catalog.json`                                             |
| Nearby societies            | Computed from `projects` coordinates, radius `MARKETPLACE_NEARBY_RADIUS_KM = 5` |
| Prohibited-item rules copy  | `app/locales/en.json` → `marketplace.rules`                                     |
| "Earned"                    | Sum of `price_amount` on the seller's `sold` rows in the last 365 days          |
| Draft gap ("add 2 photos…") | Computed from media count and null price                                        |

______________________________________________________________________

## 4. Architecture (layers)

```
HTTP → API router → Service → Repository (SQL) → Postgres
                      │
                      ├── MarketplaceCatalogService (JSON, read-only)
                      └── presigned upload (existing)
```

### File map (to implement)

| Concern            | File                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------ |
| Routes             | `app/api/marketplace.py`                                                                   |
| Route registration | `app/api/routes.py`                                                                        |
| Orchestration      | `app/services/marketplace_service.py`                                                      |
| Catalog            | `app/services/marketplace_catalog_service.py`                                              |
| Nearby + distance  | `app/services/marketplace_geo.py` (`MARKETPLACE_NEARBY_RADIUS_KM`)                         |
| SQL                | `app/db/repositories/marketplace_repository.py`                                            |
| Schemas            | `app/schemas/marketplace.py`                                                               |
| Enums              | `app/schemas/enums/marketplace.py`                                                         |
| Static data        | `app/data/marketplace_catalog.json`                                                        |
| i18n               | `app/locales/en.json` under `marketplace.*`                                                |
| Tests              | `tests/unit/test_marketplace_service.py`, `tests/unit/test_marketplace_catalog_service.py` |

Resident and committee handlers live in the same router. Committee handlers require staff project access on the listing's `project_id` after the row is loaded. The URL does not contain `project_id`.

______________________________________________________________________

## 5. Screen → API

Resident routes take `unit_id`. The service loads that unit, rejects the call unless the contact has an active `contact_units` row, and uses the unit's project as "my society". The client does not send `project_id`.

### Buy & Sell home

| Element                                   | API                                                                                 |
| ----------------------------------------- | ----------------------------------------------------------------------------------- |
| "ATS Nobility · 6 towers"                 | `GET /v1/marketplace/home?unit_id=` → `project_name`, `tower_count`                 |
| Search field                              | Navigates to search. Placeholder is client copy                                     |
| Draft strip ("Study table… add 2 photos") | Same home payload → `draft` (`id`, `title`, `missing[]`) or null                    |
| Category grid                             | `GET /v1/marketplace/catalog`                                                       |
| Recently listed + "See all 128"           | Home payload → `recent[]` (this society, `live`, newest, limit 10) and `live_count` |
| Bookmark on a card                        | `PUT /v1/marketplace/listings/{id}/save` `{ "unit_id", "saved": true \| false }`    |
| **+ Sell**                                | Opens step 1. No request until Continue                                             |

`missing[]` values: `media` (fewer than 2), `price` (sale with no price), `title`, `description`, `purchase_year`, `condition`, `pickup`. The prototype sentence is the client joining the first gaps.

Card fields: cover path, `is_new_today` (`published_at` is today in the project timezone — use Asia/Kolkata until projects grow a timezone), price, original price, title, tower, flat (subject to §7), `saved`.

### Search

| Element        | API                                        |
| -------------- | ------------------------------------------ |
| Submit a query | `GET /v1/marketplace/listings?unit_id=&q=` |

The search screen has no recent list, no Clear, no "Popular in your society" chips, and no wanted request. Submitting a query does not write a row.

### Category & filters

| Element                                             | API                                                                                          |
| --------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| "Furniture · 64 listings · your society + 3 nearby" | `GET /v1/marketplace/listings?unit_id=&category=Furniture` → `total`, `nearby_project_count` |
| Sticky chips (Newest, Under ₹5,000)                 | Same list route, repeated query params                                                       |
| Filter sheet                                        | Query params below. "Show 64 listings" is the list `total` for those params                  |

| Param               | Values                                                              |
| ------------------- | ------------------------------------------------------------------- |
| `category`          | Catalog name                                                        |
| `subtype`           | Catalog subtype                                                     |
| `q`                 | Search text                                                         |
| `sort`              | `newest` (default), `price_asc`, `price_desc`, `closest`            |
| `price_band`        | `free`, `under_5000`, `5000_20000`, `above_20000`                   |
| `condition`         | Repeatable. `like_new`, `lightly_used`, `well_used`, `needs_repair` |
| `where`             | Omitted = society + nearby. `my_tower`, `my_society`, `nearby`      |
| `page`, `page_size` | Default page size 20                                                |

Price sort puts giveaways (no price) at the free end: first for `price_asc`, last for `price_desc`. Giveaways are not pinned to the top of a category for a timed window.

`price_band=free` is `kind = giveaway`. The rupee bands apply to sales only.

### Saved items

`GET /v1/marketplace/saved?unit_id=` returns live listings this contact saved, newest save first. A saved listing that is no longer `live` drops off this list (the bookmark row can stay).

### Listing detail and service detail

`GET /v1/marketplace/listings/{id}?unit_id=`

Opening the detail does not record a view.

| Detail line                     | Source                                                                 |
| ------------------------------- | ---------------------------------------------------------------------- |
| Available                       | `status = live`                                                        |
| Listed 2 days ago               | `published_at`                                                         |
| ₹4,500 and struck ₹9,000        | `price_amount`, `original_price_amount`. Giveaway renders as Free      |
| Negotiable                      | `negotiable`                                                           |
| ₹9,000 new · bought 2025        | `original_price_amount`, `purchase_year`                               |
| Description                     | `description`                                                          |
| Condition, brand                | Columns                                                                |
| Age                             | `current_year - purchase_year` (0 → "Less than a year")                |
| Original bill                   | `original_bill_available` → Available / Not available                  |
| Seller name, role, member since | Contact + role. Public name is first name + last initial ("Rohan B.")  |
| Collection pin                  | Tower `latitude` / `longitude`. No pin if the tower has no coordinates |
| Collection label                | Tower + society. Flat only under the visibility rules                  |
| More from Rohan · See all 3     | Other `live` listings by `seller_contact_id` in the same society       |
| Bookmark                        | Save / unsave                                                          |
| Report                          | `POST /v1/marketplace/listings/{id}/reports`                           |

Service detail is this response when `category = "Services"`. No second route.

The detail screen has no Chat button and no message thread.

### Seller actions on their own listing

The overflow on a **live** listing the caller owns (prototype: "Dining set, 6 seater · Live · 4 people asking"):

| Action                  | API                                                                              | Effect                                                                                                                                                                                                            |
| ----------------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Edit listing            | `PATCH /v1/marketplace/listings/{id}`                                            | Same step-2 fields as a draft. Status stays `live`. Category and subtype are rejected if changed. The row must still pass the publish checks (media, price, condition, and the rest)                              |
| Mark as sold            | `POST .../mark-sold`                                                             | See Marked as sold                                                                                                                                                                                                |
| Share to community feed | No route in this feature                                                         | The prototype shows the row. There is no resident community feed, and notices are staff-published ([ADR 0012](./adr/0012-notice-board.md)). Do not insert a notice. The client hides this row until a feed exists |
| Remove listing          | `POST /v1/marketplace/listings/{id}/actions` `{ "unit_id", "action": "remove" }` | Seller only, `live` only. Sets `draft`. The post leaves the board. `published_at` and `expires_at` stay, so the draft can be restored. `removed_*` stays null                                                     |

"4 people asking" is prototype copy. This API does not return an asking count.

A buyer looking at the same listing does not get this menu. They get Report.

### Sell step 1 — category

| Element                   | API                                                                              |
| ------------------------- | -------------------------------------------------------------------------------- |
| Category grid             | `GET /v1/marketplace/catalog`                                                    |
| "Furniture — pick a type" | Rendered only when `subtypes.length > 0`                                         |
| Rules card                | i18n `marketplace.rules.prohibited`. Not a request                               |
| Continue                  | `POST /v1/marketplace/listings` with `unit_id`, `category`, `subtype?` → `draft` |

Furniture subtypes in the catalog: Tables & desks, Sofas & seating, Beds & mattresses, Storage, Outdoor. Other categories ship with an empty subtype list until product adds chips.

### Sell step 2 — post details

`PATCH /v1/marketplace/listings/{id}` while status is `draft`, `live`, or `removed`.

On `live`, the patch is a full edit of the post (the overflow "Edit listing") and the listing stays `live`. On a seller-removed draft, the patch is an edit of that draft and the row stays `draft` until restore. On `removed`, the patch is the committee "Edit and resubmit" path and moves the row back to `draft` until publish. `sold` and `expired` reject PATCH; expired uses list-again.

| Field              | Body                                                                                                     |
| ------------------ | -------------------------------------------------------------------------------------------------------- |
| Media              | `POST /v1/marketplace/listings/{id}/media` after presigned upload. Delete: `DELETE .../media/{media_id}` |
| Cover              | `is_cover: true` on one file                                                                             |
| Title, description | Text                                                                                                     |
| Purchase year      | Integer                                                                                                  |
| Listing type       | `kind`: `sale` \| `giveaway`                                                                             |
| Price              | `price_amount`. Omitted when giveaway. Switching to giveaway clears price and negotiable                 |
| Negotiable         | Boolean. The prototype shows it under price on a sale                                                    |
| Original price     | `original_price_amount`. Helper copy is i18n, not stored                                                 |
| Pickup location    | `unit_id` chosen from `GET /v1/marketplace/pickup-units?unit_id=` (the caller's active units)            |
| Brand              | Optional                                                                                                 |
| Condition          | `like_new`, `lightly_used`, `well_used`, `needs_repair`                                                  |
| Show flat number   | `show_flat_number`                                                                                       |
| Original bill      | `original_bill_available`                                                                                |
| Product URL        | Optional                                                                                                 |
| 30-day sentence    | i18n. "Closes 30 days after it goes live." Not stored                                                    |
| Save draft         | The PATCH itself. Drafts are partial                                                                     |
| Continue           | Client goes to step 3. Server does not publish yet                                                       |

Giveaway helper is i18n: the card shows Free. There is no timed boost.

### Sell step 3 — preview, then live

| Element                            | API                                                                                                       |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------- |
| "This is what neighbours will see" | `GET /v1/marketplace/listings/{id}?unit_id=` as the seller                                                |
| Edit details                       | Back to step 2. PATCH                                                                                     |
| Rules checkbox                     | Required on the client before Post                                                                        |
| Post listing                       | `POST /v1/marketplace/listings/{id}/actions` `{ "unit_id", "action": "publish", "rules_accepted": true }` |
| You're live                        | Publish response: `published_at`, `expires_at`, `project_name`                                            |
| Post another                       | Step 1 again                                                                                              |
| My listings                        | `GET /v1/marketplace/me/listings?unit_id=`                                                                |

Publish returns **422** with `missing[]` when the draft is not valid (see ADR §4).

### My listings

`GET /v1/marketplace/me/listings?unit_id=&status=all|live|draft|sold|past&page=1&page_size=20`

`past` is `expired` and `removed`. Header: `live_count`, `earned_amount` (sold asking prices in the last year).

| Card                     | Fields                                                               | Action                                                               |
| ------------------------ | -------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Live                     | Days left                                                            | `POST .../{id}/actions` `{ "action": "renew" }`                      |
| Draft, never published   | Gap sentence                                                         | Opens step 2                                                         |
| Draft, seller removed it | The saved post. `published_at` is already set                        | Edit (PATCH), then `POST .../{id}/actions` `{ "action": "restore" }` |
| Sold                     | Buyer public name, tower, flat, `sold_at`. Price shown to the seller | None                                                                 |
| Expired                  | "Ran for 30 days"                                                    | `POST .../{id}/actions` `{ "action": "relist" }`                     |
| Removed by committee     | `removal_note`                                                       | Edit (PATCH, returns to `draft`) then publish                        |

Sold cards older than 365 days are omitted.

`POST /v1/marketplace/listings/{id}/actions` with `action: restore` is the seller only, and only when `status` is `draft` and `published_at` is already set. The row must pass the same checks as publish. Status becomes `live`. If `expires_at` is still in the future, it is kept. If it has passed, `published_at` is set to now and `expires_at` to 30 days later. A draft that was never published uses `action: publish` (and `rules_accepted: true`), not restore.

### Marked as sold

`POST /v1/marketplace/listings/{id}/mark-sold`

```json
{
  "unit_id": "<seller unit>",
  "buyer_contact_id": "<active resident>",
  "rating": "smooth"
}
```

`buyer_contact_id` must be a contact with an active `contact_units` row in the same organization. The seller cannot mark the listing sold to themselves.

`rating` is optional: `smooth`, `fine`, `had_trouble`. Copy on the sheet ("only the committee sees this, and only if there's a pattern") matches the storage rule in §3.

Response (seller only, this call): item title, buyer public name, buyer flat, `price_amount`.

______________________________________________________________________

## 6. Committee API

Same prefix as the resident API. Not on the resident flow map. Required so "Removed after a resident reported it" can happen.

The handler loads the report, then checks staff access on that report's `project_id`. The client does not pass `project_id`.

| Action               | Route                                                                                               | Permission                        |
| -------------------- | --------------------------------------------------------------------------------------------------- | --------------------------------- |
| Open reports         | `GET /v1/marketplace/reports?status=open`                                                           | `marketplace_management.view`     |
| Take down or dismiss | `POST /v1/marketplace/reports/{id}/review` `{ "decision": "uphold" \| "dismiss", "removal_note"? }` | `marketplace_management.moderate` |
| Trouble pattern      | `GET /v1/marketplace/feedback-patterns`                                                             | `marketplace_management.view`     |

`decision: uphold` requires `removal_note`, sets the listing `removed`, and stores that note. The seller sees it on My listings. `decision: dismiss` leaves the listing live.

______________________________________________________________________

## 7. Flat visibility (response builder)

Apply in one function, `visible_flat(listing, viewer)`, used by every card, detail, and preview.

| Viewer                                               | Result                          |
| ---------------------------------------------------- | ------------------------------- |
| Seller                                               | Always the flat                 |
| Active resident of the listing's project, toggle on  | Flat                            |
| Active resident of the listing's project, toggle off | Tower + society                 |
| Resident of another project                          | Tower + society. Never the flat |

There is no later step that reveals the flat. The toggle is the only switch.

______________________________________________________________________

## 8. Catalog file

```json
{
  "categories": [
    {
      "id": "furniture",
      "name": "Furniture",
      "icon": "sofa",
      "subtypes": [
        { "id": "tables_desks", "name": "Tables & desks" },
        { "id": "sofas_seating", "name": "Sofas & seating" },
        { "id": "beds_mattresses", "name": "Beds & mattresses" },
        { "id": "storage", "name": "Storage" },
        { "id": "outdoor", "name": "Outdoor" }
      ]
    },
    { "id": "electronics", "name": "Electronics", "icon": "tv", "subtypes": [] },
    { "id": "home_decor", "name": "Home decor", "icon": "lamp", "subtypes": [] },
    { "id": "appliances", "name": "Appliances", "icon": "fridge", "subtypes": [] },
    { "id": "kids_toys", "name": "Kids & toys", "icon": "toy", "subtypes": [] },
    { "id": "vehicles", "name": "Vehicles", "icon": "car", "subtypes": [] },
    { "id": "services", "name": "Services", "icon": "services", "subtypes": [] },
    { "id": "others", "name": "Others", "icon": "box", "subtypes": [] }
  ]
}
```

The API stores `name`, not `id`, same as pets. Ids exist for the picker.

______________________________________________________________________

## 9. Out of scope

- Taking payment, escrow, or delivery.
- Homes / rental property.
- Pets as listings.
- In-app messages, chat threads, and sharing a flat inside a thread.
- Search recents, popular-search chips, suggestions, and wanted requests.
- View counts.
- Background jobs and push notifications.
- A timed giveaway boost.
- Routes under `/v1/projects/{project_id}/marketplace`.
- Share to community feed. The listing overflow shows it; notices are not a resident feed, so v1 has no route and no table for it.
- Revealing `contacts.phones`.
- A configurable nearby radius in admin settings (the constant is 5 km until product changes it).

______________________________________________________________________

## 10. How to make common changes

| I want to…                         | Change here                                                    |
| ---------------------------------- | -------------------------------------------------------------- |
| Add a category or a furniture type | `app/data/marketplace_catalog.json`                            |
| Change the 30-day live window      | `marketplace_service.py`                                       |
| Change the nearby radius           | `MARKETPLACE_NEARBY_RADIUS_KM` in `marketplace_geo.py`         |
| Change who may post                | The `contact_roles` check in `marketplace_service.py`          |
| Change flat visibility             | `visible_flat` only                                            |
| Add a report reason                | Postgres enum + `marketplace_report_reason` + the report sheet |
| Change a sentence                  | `app/locales/en.json` under `marketplace.*`                    |
