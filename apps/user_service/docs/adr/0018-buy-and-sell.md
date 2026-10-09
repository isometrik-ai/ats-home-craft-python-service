# ADR 0018: Buy & sell — resident classifieds

|                  |                                                                                                                                                                                        |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status**       | Proposed                                                                                                                                                                               |
| **Date**         | 2026-10-06 (revised 2026-10-08; staff admin 2026-10-08)                                                                                                                                |
| **Authors**      | Home Craft platform team                                                                                                                                                               |
| **Depends on**   | [ADR 0001](./0001-resident-onboarding.md) (contacts + `contact_units`), [ADR 0009](./0009-contact-roles.md), [project setup](../project-setup-flow.md)                                 |
| **Related docs** | [0018-buy-and-sell-flow.md](../0018-buy-and-sell-flow.md), [project-setup-flow.md](../project-setup-flow.md), [0015-pets-flow.md](../0015-pets-flow.md) (catalog + media-path pattern) |
| **Migrations**   | `ats-home-craft-supabase`: `20261006120000_marketplace_enums.sql`, `20261006121000_marketplace_tables.sql`, `20261008140000_marketplace_permissions.sql`                               |

______________________________________________________________________

## Context

Residents need a place to sell or give away household items and browse what others have listed. Buy & Sell is a resident tab on the app shell.

**Reduced scope (2026-10-08):** No in-app chat, no reports, no wanted requests, no search history or suggestions, no view counts, no giveaway sort boost, no expiry reminder push. Resident HTTP routes live under **`/v1/marketplace`**. Staff routes live under **`/v1/marketplace/admin`**. Both are organization-scoped. Listings still reference a pickup **project** via `units`; staff may pass optional `project_id` to filter one society.

### In scope

1. **Browse** — text search on list, category grid, filters, saved items (no dedicated home route).
1. **Buy** — listing detail.
1. **Sell** — category, post details, preview, live confirmation.
1. **Manage** — my listings, mark as sold, renew, remove.
1. **Staff admin** — project listings dashboard: summary cards, search, status + category filters, flat table, listing drawer, staff remove of a live listing.

### Out of scope (removed from earlier drafts)

- Messages, chat, threads, messages tables
- Reports, Reported tab, and committee uphold/dismiss
- Wanted requests ("Not finding it?") and matching-seller push
- Recent searches and popular search terms
- `view_count`, `giveaway_boost_until`, `expiry_reminder_sent_at`
- `marketplace_thread_status` and related thread enums
- Staff **Removed by committee** as a distinct status or filter (staff remove uses `status = removed`)
- Group by tower, All towers filter, Sort: tower & unit
- Export API
- Marketplace Settings

### What already exists

Project setup stores societies, towers, and flats. Buy & sell **reads** those rows and does **not** add columns to them. Photo upload uses presigned URLs; Postgres stores paths only (same as pets and project media).

______________________________________________________________________

## Decision

### 1. Three listing tables (plus existing RBAC catalog)

| Table                           | Purpose                                                                                                | Resident | Staff admin |
| ------------------------------- | ------------------------------------------------------------------------------------------------------ | -------- | ----------- |
| **`marketplace_listings`**      | Draft → live → sold / expired / removed. Sale or giveaway. 30-day window. Media is `jsonb` on this row | R/W      | R/W remove  |
| **`marketplace_saved_items`**   | Bookmarks                                                                                              | R/W      | unused      |
| **`marketplace_sale_feedback`** | Optional seller rating after mark-sold. Not exposed on public reads or the staff drawer                | W        | unused      |

Staff also **reads** project-setup / onboarding rows (no new columns): `projects`, `towers`, `units`, `contacts`, `contact_roles`, and `organization_members` (remover display name). Staff **writes** RBAC catalog rows via `20261008140000_marketplace_permissions.sql` into `project_permissions` / `project_role_permissions`.

Categories: `app/data/marketplace_catalog.json` (not Postgres). Listings store catalog **slugs**; display names are resolved on read so a rename does not break filters.

Nearby societies: computed from project coordinates (`MARKETPLACE_NEARBY_RADIUS_KM = 5`), not a table.

Column detail: [0018-buy-and-sell-flow.md](../0018-buy-and-sell-flow.md) §3.

### 2. API surface

**Resident** (`/v1/marketplace`, org-scoped):

- **Tenancy:** `organization_id` from auth on every query.
- **Browse:** GET listing routes are **org-wide**; no `unit_id` query param. Flat display follows visibility rules without a viewer unit.
- **Writes:** `unit_id` in the JSON body on create, save, patch, remove, and mark-sold (not on GET). Publish reads the pickup unit already stored on the listing.
- **Sell:** `POST /listings` (unpublished), `PATCH /listings/{id}`, `GET /listings/{id}` (preview), **`POST /listings/{id}/publish`**, **`POST /listings/{id}/remove`**. Media is not editable after create. Remove is permanent; the seller creates a new listing to post again.
- **Pagination:** `page` / `page_size` on browse, saved, and my listings.

Residents do not get new RBAC codes. Posting requires Owner, Tenant, or Family on the pickup unit (same as pets).

**Staff** (`/v1/marketplace/admin`, org-scoped like companies; optional `project_id` query):

- **Auth:** `ensure_staff_project_access_optional` with `marketplace_management.view` (reads) or `marketplace_management.edit` (remove). When `project_id` is omitted, HQ `projects_management.view` satisfies the ceiling. When it is set, assigned project staff need the marketplace project-role grant.
- **List:** search (`q` on title, resident name, unit, tower), `status` (`all` / `live` / `sold` / `past` / `removed`), `category` catalog slug, optional `project_id`. Flat table newest-first. **No** group-by-tower, all-towers, or tower-and-unit sort.
- **Summary:** `active_count` (`live`), `sold_count`, `past_count` (`expired`), `removed_count`. Drafts are omitted. Same optional `project_id`.
- **Detail / remove:** `/listings/{id}` — org + listing id. Optional `project_id` further scopes the row.
- **Remove:** `POST .../listings/{id}/remove` `{ "removal_note" }` on a **live** row → `status = removed`. Permanent; no restore.
- **Not built:** export, settings, Reported tab, view counts, conversations.

### 3. Listing lifecycle

```
draft ──publish──► live ──30 days──► expired ──list again──► live
                    │  ▲                  │
                    │  └── renew 30 days ─┘
                    ├── edit (stays live)
                    ├── mark sold ──► sold
                    ├── seller remove ──► removed (create a new listing to post again)
                    └── staff remove ──► removed (same status; no committee enum)
```

| Status    | Public board | Seller                                       |
| --------- | ------------ | -------------------------------------------- |
| `draft`   | No           | Edit, publish when valid                     |
| `live`    | Yes          | Edit (stays live), renew, mark sold, remove  |
| `sold`    | No           | My listings ≤ 1 year                         |
| `expired` | No           | List again                                   |
| `removed` | No           | Terminal. Create a new listing to post again |

No separate committee-removal status. Seller **or** staff remove sets `status = removed`, `removal_note`, and `removed_by_user_id`. The staff status filter has no "Removed by committee" option; those rows appear under **Removed**.

Giveaways display as **Free**; sort uses normal `newest` / price / distance — **no** 24-hour boost column.

### 4. Publish requirements

Unchanged from prior ADR: category, subtype, ≥2 media (image or video), title, description, purchase year, condition, pickup unit. Sale requires price > 0; giveaway requires null price. Cover is the first image, or the first item's preview.

### 5. Browse and flat visibility

Default list scope: **organization-wide** live listings (`GET /listings` with no `unit_id`). Optional filters: category, text `q`, sort, price band, condition.

Flat number: seller always sees their flat on their listings; other viewers only see flat when same-society and `show_flat_number` is true (no viewer `unit_id` on GET). **No** chat-based share-flat.

### 6. Mark sold

1. Seller passes `buyer_contact_id`.
1. Buyer must have active `contact_units` in the listing's `project_id` (not the seller).
1. Listing → `sold` with `sold_at`.
1. Optional `rating` → `marketplace_sale_feedback`.

No threads to close. Buyer list comes from `GET .../buyer-candidates`, not chat history.

### 7. Search

Search is **`GET /listings?q=`** only. Do not persist queries. Search UI must not show recent or popular sections backed by the API.

### 8. Media

Stored on **`marketplace_listings.media`** (`jsonb` array). No child table and no `/listings/{id}/media` routes. Max 8 items, ≥2 to publish. Images: `image/jpeg`, `image/png`. Videos: `video/mp4`. Each item has `type`, `path`, `file_type`, optional `description`, and `order`. `preview_path` is required for video, optional for image. Set only on create.

### 9. Jobs and push

Daily job expires live listings past `expires_at`. **No** 3-day reminder job or column.

**No** wanted-request push, chat, report, or sold-to-other notifications.

### 10. Out of scope

- Chat, threads, messages
- Reports, Reported tab, committee uphold/dismiss
- Wanted requests ("Not finding it?") and matching-seller push
- View counts, giveaway boost, expiry reminder
- Search recents/terms APIs
- Payments, phone reveal, community-feed share
- Listing pets ([ADR 0015](./0015-pets.md)) or rental property
- Staff export, settings, group-by-tower / all-towers / sort tower & unit
- Distinct `removed_by_committee` status

______________________________________________________________________

## Consequences

**Positive**

- Smaller listing schema; resident browse + sell + manage plus a project-scoped staff board.
- Staff reuses pets-admin auth (`ensure_staff_project_access`) and the same listing row (no committee table).
- Reuses project setup, onboarding, presigned upload, and catalog JSON patterns.

**Negative**

- Prototype screens for Messages, Chat, Report, Settings, Export, and tower grouping are not implemented; client must hide them.
- Buyers cannot coordinate in-app; product relies on offline contact.
- Staff take-down is indistinguishable from seller remove in the status filter (`removed`).

**Follow-ups**

- Chat and notifications per thread (new ADR if added)
- Reports and a Reported tab if product restores them
- Search suggestions if product restores them
- Share to community feed when a resident feed exists
