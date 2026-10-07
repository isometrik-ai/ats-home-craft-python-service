# ADR 0019: Buy & sell — resident classifieds inside a society

|                  |                                                                                                                                                                                                 |
| ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Proposed                                                                                                                                                                                        |
| **Date**         | 2026-10-06                                                                                                                                                                                      |
| **Authors**      | Home Craft platform team                                                                                                                                                                        |
| **Depends on**   | [ADR 0001](./0001-resident-onboarding.md) (contacts + `contact_units`), [ADR 0010](./0010-contact-roles.md), [project setup](../project-setup-flow.md) |
| **Related docs** | [buy-and-sell-flow.md](../buy-and-sell-flow.md), [project-setup-flow.md](../project-setup-flow.md), [pets-flow.md](../pets-flow.md) (catalog + photo-path pattern)                             |
| **Migrations**   | Proposed, not written: `20261006120000_marketplace_enums.sql`, `20261006121000_marketplace_tables.sql` (`ats-home-craft-supabase`)                                                              |

______________________________________________________________________

## Context

Residents of a gated community (a **project** in project setup — the prototype calls it a society, "ATS Nobility") need a place to sell or give away household items to neighbours, and to find things other residents have listed. The resident app already has Home, Facilities, Notices, and Profile. Buy & Sell is a new tab on that shell.

The prototype covers the whole loop:

1. **Browse** — home, search, category grid, filters, saved items.
2. **Buy** — listing detail, the same detail for a service, report to the committee.
3. **Sell** — three steps (category, post details, preview), then a live confirmation.
4. **Manage** — my listings (live, draft, sold, expired, removed) and mark as sold.

Messages, chat, search suggestions, and recent searches are on the prototype and are **not** part of this feature.

### What already exists to hang this on

Project setup ([project-setup-flow.md](../project-setup-flow.md) §3) already stores the places and the people. Buy & sell **reads** those rows. It does **not** add columns to them.

| Prototype label                         | Existing row                                                                                                                                          |
| --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| Society ("ATS Nobility")                | `projects.name`. Header tower count is `count(towers)` for that project                                                                              |
| "6 towers", "Tower B", collection pin   | `towers` (`name`, `latitude`, `longitude` from site map)                                                                                             |
| Flat "B-1104" / pickup dropdown         | `units.code` or `units.unit_label`, joined through the seller's active `contact_units`                                                               |
| Nearby societies, "closest to me"       | `projects.latitude` / `longitude` and `towers.latitude` / `longitude`. No nearby-society table exists                                                |
| Seller "Rohan B.", Owner, member since  | `contacts` (name, photo) + active `contact_roles` on that unit (`Owner`, `Tenant`, `Family`) + `contact_roles.started_at`                            |
| Phone stays private                     | `contacts.phones` is never selected by this API                                                                                                      |
| Committee                               | Staff with project access ([ADR 0011](./0011-project-membership.md)), same actor as notice-board moderation                                           |
| Media upload                            | Presigned URL (`POST /v1/presigned-url`). Postgres stores the path only, as on `project_media` and `pets.photo_paths`                                |

There is **no** listings table or bookmark table today. Household `vehicles` is the parking/onboarding registry. A cycle listed under marketplace **Vehicles** is not a row in `vehicles`.

### Product decisions (from the prototype, do not relitigate)

| #   | Decision                                                                                                                                                                                          |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | A listing belongs to one society (`project_id`, copied from the pickup unit) and one pickup flat (`unit_id`). The seller is the contact who posts it. The HTTP API is not nested under a project |
| 2   | Two kinds: **Sale** (price required) and **Giveaway** (price is null, card says Free)                                                                                                            |
| 3   | A live post runs **30 days**, then becomes expired. The seller can renew for another 30 days in one tap, or list an expired post again. There is no reminder before expiry                       |
| 4   | Drafts are real rows. Home shows the latest draft and what is still missing ("add 2 photos and a price")                                                                                         |
| 5   | Publishing requires the rules acknowledgement. Firearms, alcohol, medicines, pets, and rental property are not listed here. Property stays in Homes (out of scope). Pets stay on the pets module |
| 6   | The flat number is visible to **other residents of the same society** only when the seller turns the toggle on. Residents of nearby societies never receive the flat. There is no share-later step |
| 7   | Phone numbers are never returned                                                                                                                                                                  |
| 8   | A report goes to the society committee, not to the seller. The seller is told only if the committee takes the post down, and then they see the committee's note                                 |
| 9   | Marking sold records the buyer and an optional private rating (Smooth / Fine / Had trouble)                                                                                                      |
| 10  | Sold price is visible to the seller in My listings. It is not shown on any public card after the post leaves the board                                                                           |
| 11  | The platform does not take payment, escrow, or delivery. "₹18,000 earned" is the sum of sold asking prices, not money collected                                                                  |
| 12  | Search is a query on the listings list. The API does not store recent searches, popular terms, suggestions, or wanted requests                                    |

______________________________________________________________________

## Decision

### 1. Five new tables. No changes to project-setup tables

| Table                             | Purpose                                                                                                      |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **`marketplace_listings`**        | One item: draft through sold / expired / removed. Sale or giveaway. Pickup unit, price, condition, 30-day window |
| **`marketplace_listing_media`**   | Ordered media metadata (path, file type, size). One cover per listing. Same metadata-only rule as `project_media` |
| **`marketplace_saved_items`**     | Bookmark. One row per resident per listing                                                                  |
| **`marketplace_reports`**         | Resident report. Committee upholds or dismisses. Uphold removes the listing                                 |
| **`marketplace_sale_feedback`**   | Seller's private rating of the buyer. One per sold listing. Hidden from the buyer and from the public feed  |

Categories and subtypes are **not** tables. They live in `app/data/marketplace_catalog.json`, same pattern as `pet_catalog.json`.

Nearby societies are **not** a table. A society is nearby when both projects have coordinates and the haversine distance between `projects.latitude/longitude` is within **5 km**, same `organization_id`, both `status = active`. The radius is the constant `MARKETPLACE_NEARBY_RADIUS_KM` in one module.

Column-level detail, checks, and indexes: [buy-and-sell-flow.md](../buy-and-sell-flow.md) §3.

### 2. Who may post, and from which flat

Any contact with an **active `contact_units`** row on the pickup unit may create and manage listings for that unit. That is the same gate as pets and vehicles: Owner, Tenant, and Family. Guest, Vendor, and Staff cannot post.

The public seller line shows that contact's active `contact_roles.role_type` on the unit ("Owner"), the tower, and — only under decision 6 — the flat. "Member since 2023" is the year of `contact_roles.started_at` for that role. The verified mark means the seller still has an active residency on the unit. There is no separate KYC flag to add.

The pickup dropdown is the caller's active units, labelled `{unit} , {project.name}` (prototype: "B-1104, ATS Nobility").

### 3. Listing lifecycle

```
draft ──publish──► live ──30 days──► expired ──list again──► live
  ▲                 │
  │                 ├── edit (stays live)
  │                 ├── renew 30 days
  │                 ├── mark sold ──► sold
  └── seller remove ┘
        then edit, then restore ──► live

live ──committee uphold──► removed ──edit──► draft ──publish──► live
```

A seller remove lands on `draft`, not on `removed`. From that draft the seller edits with `PATCH`, then `POST .../actions` with `action: restore` returns the same row to `live`. Publish, remove, restore, renew, and relist share that one route. Save and unsave share `PUT .../save`. Uphold and dismiss share `POST .../reports/{id}/review`.

| Status | On the public board | Seller can |
| ------ | ------------------- | ---------- |
| `draft` never published | No | Edit, delete the draft, publish when valid |
| `draft` after the seller removed a live post | No | Edit, then restore. `published_at` is already set |
| `live` | Yes | Edit step-2 fields (stays `live`), renew, mark sold, or remove |
| `sold` | No | Read it in My listings for one year |
| `expired` | No | List it again (same row, new 30-day window) |
| `removed` by the committee | No | Edit and resubmit, which returns it to `draft` |

`expires_at` on publish is `published_at + 30 days`. Renew sets `expires_at = greatest(expires_at, now()) + 30 days`, so remaining days are kept. List-again on an expired row sets `published_at = now()` and `expires_at = now() + 30 days`.

The seller's overflow on their own live listing is Edit listing, Mark as sold, Share to community feed, and Remove listing.

Edit listing is `PATCH` while `live`. Category and subtype stay as posted. The row must still satisfy the publish checks, so a live post cannot be saved half-empty. The same form edits a draft, including a draft the seller just removed. A draft edit can be partial. Preview "Edit details" before the first publish is that form too.

Remove listing is the seller, `live` only. It sets `draft` and leaves the board. `published_at` and `expires_at` stay. It does not set `removed_at`, `removal_note`, or `removed_by_user_id`.

Restore is the seller, `draft` only, and only when `published_at` is already set. The row must pass the publish checks, because the seller may have edited it. Status becomes `live`. A future `expires_at` is kept. A past `expires_at` is replaced with a new 30-day window.

Committee uphold is the only writer of `removed`. It sets `removed_by_user_id` and a `removal_note`.

Share to community feed has no table and no route. [ADR 0012](./0012-notice-board.md) notices are staff-published. The client does not show that row until a resident feed exists.

### 4. What "publish" requires

| Field                         | Sale                         | Giveaway        |
| ----------------------------- | ---------------------------- | --------------- |
| Category                      | Required                     | Required        |
| Subtype                       | Required when the catalog category has subtypes | Same |
| Media                         | At least 2, exactly one cover | Same           |
| Title, description            | Required                     | Required        |
| Purchase year                 | Required, 1980 through the current year | Same |
| Price                         | `numeric(12,2)` > 0          | Must be null    |
| Original (struck-through) price | Optional; if set, must be **greater than** price | Null |
| Negotiable                    | Optional, default false      | Forced false    |
| Pickup unit                   | Required, caller must have active `contact_units` | Same |
| Condition                     | Required                     | Required        |
| Brand, product URL, original bill | Optional                | Optional        |
| Show flat number              | Optional, default false      | Same            |
| Rules accepted                | Required at publish          | Required        |

Save draft accepts a partial row. The home strip computes the gap; it is not stored.

Condition values: `like_new`, `lightly_used`, `well_used`, `needs_repair`. The filter sheet in the prototype shows the first three. The API accepts all four so "Needs repair" is not unfilterable.

`original_bill_available` is stored because listing detail shows "Original bill: Available". The attached post form is cropped before that control; the field still belongs on step 2 as a yes/no. Age ("1 year") is `current year − purchase_year`, not a column. The prototype detail line "bought Jan 2025" includes a month the form does not collect. The API stores the year and the client renders "bought {year}".

### 5. Who sees a listing, and how much of the flat

Default browse scope is the resident's society **plus** nearby societies. Home → Recently listed is the **current society only** (the prototype cards are Tower A and Tower B of ATS Nobility). Category and search use the wider scope. The client passes `unit_id`. The service reads the society from that unit.

| Where chip        | Rule                                                                 |
| ----------------- | -------------------------------------------------------------------- |
| *(default)*       | Listing `project_id` = the unit's project, or a project inside the 5 km radius |
| My tower          | Same `tower_id` as the viewer's unit                                 |
| My society        | Same `project_id` as the viewer's unit                               |
| Nearby societies  | Other projects inside the radius, excluding the viewer's project     |

Flat number (`units.code` / `unit_label`):

| Viewer                                      | `show_flat_number` | Structured flat |
| ------------------------------------------- | ------------------ | --------------- |
| Same society                                | true               | Shown on card, detail, seller line, preview |
| Same society                                | false              | Hidden. Tower + society only                |
| Nearby society                              | either             | Hidden                                          |
| Seller looking at their own preview / My listings | either        | Shown, so they can confirm what they posted    |

"Closest to me" sorts by distance between the viewer's tower coordinates and the listing's tower coordinates. A tower with no coordinates sorts last. Societies with no coordinates are absent from the nearby set and still appear in their own residents' feeds.

Price bands are fixed chips, not a slider: Free (giveaway), under ₹5,000, ₹5,000–₹20,000, above ₹20,000. Giveaways sort with the free end of a price sort. They are not boosted to the top of a category.

### 6. Sold and reports

**Mark sold** (`buyer_contact_id` required):

1. Buyer must have an active `contact_units` row in the same organization, and must not be the seller.
2. Listing → `sold`, `sold_at`, `buyer_contact_id`.
3. Optional rating is inserted into `marketplace_sale_feedback`. The mark-sold response may echo it once to the seller. No later read returns it to the buyer or on a public listing.

Sold rows stay on the seller's My listings for **365 days** after `sold_at`, then drop off that list. The row stays in Postgres.

**Report** reasons: `not_allowed`, `business_or_broker`, `sold_but_listed`, `something_else`. One open report per reporter per listing. The seller is not notified of the report itself.

Committee `uphold` sets the listing to `removed` and copies a `removal_note` the seller sees ("didn't show the item"). `dismiss` leaves the listing live.

Feedback is for the committee, and only as a pattern: the admin read groups by seller and returns a row when that seller has **3 or more** `had_trouble` ratings. Individual ratings are not a resident-facing feed.

### 7. Search

`GET /v1/marketplace/listings?q=` filters live listings. It does not write a recent-search row, a popular-term row, a suggestion list, or a wanted request.

### 8. Catalog, not lookup tables

`app/data/marketplace_catalog.json`:

| Category     | Subtypes in the prototype                                                                 |
| ------------ | ----------------------------------------------------------------------------------------- |
| Furniture    | Tables & desks, Sofas & seating, Beds & mattresses, Storage, Outdoor                     |
| Electronics  | *(none drawn — category has an empty subtype list until product adds chips)*             |
| Home decor   | *(none drawn)*                                                                            |
| Appliances   | *(none drawn)*                                                                            |
| Kids & toys  | *(none drawn)*                                                                            |
| Vehicles     | *(none drawn)*                                                                            |
| Services     | *(none drawn)*                                                                            |
| Others       | *(none drawn)*                                                                            |

Step 1 shows "{Category} — pick a type" only when that category has one or more subtypes. Furniture does. The others continue after the category tap. Adding a chip is a JSON edit, not a migration.

**Service detail** in the flow map is the same listing detail when `category = Services`. The prototype set does not show a second form. Services use the same post fields.

The rules sentence (firearms, alcohol, medicines, pets, rental property) is i18n copy, not a blocklist classifier.

### 9. Permissions

One prefix: `/v1/marketplace`. Residents do not get a new permission code. Routes resolve `extract_onboarding_contact_context()` and require an active `contact_units` row for the `unit_id` they pass.

Committee handlers use the same prefix. After the report or listing is loaded, they call staff project access on that row's `project_id`:

| Action                                      | Code                            |
| ------------------------------------------- | ------------------------------- |
| List reports, read feedback patterns        | `marketplace_management.view`   |
| Uphold (take down) or dismiss a report      | `marketplace_management.moderate` |

Seed both on `community_admin`. Seed view on `viewer`. These codes do not exist yet; the migration that adds the tables also seeds them.

### 10. Money and media

Prices are `numeric(12, 2)` rupees, decimal strings on the API, same as fee configuration. The client draws ₹. There is no currency column (the product is INR in this prototype).

Media: client uploads via the existing presigned URL, then sends `path`, `file_type`, `size_bytes` to `marketplace_listing_media`. `file_type` is `jpeg` or `png`. Max **8** files, 5 MB each. At least **2** to publish. First file becomes cover if the client does not mark one. Paths are never a blob.

### 11. Out of scope

- Payments, offers that change the stored price, shipping, gate passes for pickup
- In-app messages and chat
- Search recents, popular terms, suggestions, and wanted requests
- View counts
- Background jobs and push notifications
- A giveaway boost window
- A project id in the URL (`/v1/projects/{project_id}/marketplace`)
- Share to community feed (prototype row only; no resident feed to write into)
- A Homes / rental-property module (the rules card points sellers there)
- Listing pets (use [ADR 0016](./0016-pets.md))
- Automated prohibited-item detection
- A Postgres table of nearby societies
- Reusing `vehicles` for items listed under the Vehicles category

______________________________________________________________________

## Consequences

**Positive**

- Society, tower, flat, seller identity, and the map pin all reuse tables that project setup and onboarding already maintain.
- Drafts, expiry, reports, and sold history are rows, so My listings does not reconstruct state from logs.
- Flat visibility is a query rule, not a second copy of the unit label.
- Category chips can grow without a migration.
- Residents and committee share one route prefix, so the client never picks a project id to call the API.

**Negative**

- Buyers and sellers have no in-app thread. The listing shows who is selling and where to collect; arranging the handover is outside the app.
- Nearby depends on project and tower coordinates from site map. A project that skipped coordinates has no nearby set.
- A live edit changes what buyers already see. Category stays fixed so the post does not jump rails.
- Share to community feed is drawn on the overflow and intentionally unimplemented.
- Mark sold trusts a `buyer_contact_id` the seller sends. There is no conversation history to confirm that person asked about the item.

**Follow-ups (not this ADR's implementation)**

- Admin UI beyond uphold / dismiss / feedback pattern
