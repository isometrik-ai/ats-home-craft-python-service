# ADR 0019: Buy & sell — resident classifieds

|                  |                                                                                                                                                                                                                                                                          |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Status**       | Proposed                                                                                                                                                                                                                                                                 |
| **Date**         | 2026-10-06 (revised 2026-10-08)                                                                                                                                                                                                                                          |
| **Authors**      | Home Craft platform team                                                                                                                                                                                                                                                 |
| **Depends on**   | [ADR 0001](./0001-resident-onboarding.md) (contacts + `contact_units`), [ADR 0010](./0010-contact-roles.md), [project setup](../project-setup-flow.md)                                                                                                                   |
| **Related docs** | [buy-and-sell-flow.md](../buy-and-sell-flow.md), [project-setup-flow.md](../project-setup-flow.md), [pets-flow.md](../pets-flow.md) (catalog + media-path pattern)                                                                                                       |
| **Migrations**   | `ats-home-craft-supabase`: `20261006120000_marketplace_enums.sql`, `20261006121000_marketplace_tables.sql`, `20261006122000_marketplace_permissions.sql`, `20261008120000_marketplace_item_condition_drop_like_new.sql`, `20261008130000_marketplace_flow_alignment.sql` |

______________________________________________________________________

## Context

Residents need a place to sell or give away household items and browse what others have listed. Buy & Sell is a resident tab on the app shell.

**Reduced scope (2026-10-08):** No in-app chat, no reports, no wanted requests, no staff marketplace admin API, no search history or suggestions, no view counts, no giveaway sort boost, no expiry reminder push. All HTTP routes live under **`/v1/marketplace`** (organization-scoped). Listings still reference a pickup **project** via `units`, but URLs do not include `project_id`.

### In scope

1. **Browse** — text search on list, category grid, filters, saved items (no dedicated home route).
1. **Buy** — listing detail.
1. **Sell** — category, post details, preview, live confirmation.
1. **Manage** — my listings, mark as sold, renew, remove.

### Out of scope (removed from earlier drafts)

- Messages, chat, threads, messages tables
- Reports and committee uphold/dismiss
- Wanted requests ("Not finding it?") and matching-seller push
- `marketplace_management.*` permissions and `/v1/projects/{project_id}/marketplace/*`
- Recent searches and popular search terms
- `view_count`, `giveaway_boost_until`, `expiry_reminder_sent_at`
- `marketplace_thread_status` and related thread enums

### What already exists

Project setup stores societies, towers, and flats. Buy & sell **reads** those rows and does **not** add columns to them. Photo upload uses presigned URLs; Postgres stores paths only (same as pets and project media).

______________________________________________________________________

## Decision

### 1. Three new tables

| Table                           | Purpose                                                                                                |
| ------------------------------- | ------------------------------------------------------------------------------------------------------ |
| **`marketplace_listings`**      | Draft → live → sold / expired / removed. Sale or giveaway. 30-day window. Media is `jsonb` on this row |
| **`marketplace_saved_items`**   | Bookmarks                                                                                              |
| **`marketplace_sale_feedback`** | Optional seller rating after mark-sold. Not exposed on public reads                                    |

Categories: `app/data/marketplace_catalog.json` (not Postgres). Listings store catalog **slugs**; display names are resolved on read so a rename does not break filters.

Nearby societies: computed from project coordinates (`MARKETPLACE_NEARBY_RADIUS_KM = 5`), not a table.

Column detail: [buy-and-sell-flow.md](../buy-and-sell-flow.md) §3.

### 2. API surface

- **Prefix:** `/v1/marketplace` only.
- **Tenancy:** `organization_id` from auth on every query.
- **Browse:** GET listing routes are **org-wide**; no `unit_id` query param. Flat display follows visibility rules without a viewer unit.
- **Writes:** `unit_id` in the JSON body on create, save, patch, publish, remove, and mark-sold (not on GET).
- **Sell:** `POST /listings` (unpublished), `PATCH /listings/{id}`, `GET /listings/{id}` (preview), **`POST /listings/{id}/publish`**, **`POST /listings/{id}/remove`**. Media is not editable after create. Remove is permanent; the seller creates a new listing to post again.
- **Pagination:** `page` / `page_size` on browse, saved, and my listings.
- **No** project-id path segments and **no** staff marketplace routes in v1.

Residents do not get new RBAC codes. Posting requires Owner, Tenant, or Family on the pickup unit (same as pets).

### 3. Listing lifecycle

```
draft ──publish──► live ──30 days──► expired ──list again──► live
                    │  ▲                  │
                    │  └── renew 30 days ─┘
                    ├── edit (stays live)
                    ├── mark sold ──► sold
                    └── seller remove ──► removed (create a new listing to post again)
```

| Status    | Public board | Seller                                       |
| --------- | ------------ | -------------------------------------------- |
| `draft`   | No           | Edit, publish when valid                     |
| `live`    | Yes          | Edit (stays live), renew, mark sold, remove  |
| `sold`    | No           | My listings ≤ 1 year                         |
| `expired` | No           | List again                                   |
| `removed` | No           | Terminal. Create a new listing to post again |

No committee removal path. Seller remove sets `status = removed`, `removal_note`, and `removed_by_user_id`.

Giveaways display as **Free**; sort uses normal `newest` / price / distance — **no** 24-hour boost column.

### 4. Publish requirements

Unchanged from prior ADR: category, subtype, ≥2 media (image or video), title, description, purchase year, condition, pickup unit, rules acceptance. Sale requires price > 0; giveaway requires null price. Cover is the first image, or the first item's preview.

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
- Reports, moderation, admin marketplace permissions
- Wanted requests ("Not finding it?") and matching-seller push
- View counts, giveaway boost, expiry reminder
- Search recents/terms APIs
- Payments, phone reveal, community-feed share
- Listing pets ([ADR 0016](./0016-pets.md)) or rental property

______________________________________________________________________

## Consequences

**Positive**

- Smaller schema and one router module; faster to ship browse + sell + manage.
- Org-scoped API matches other resident features; no parallel staff marketplace surface.
- Reuses project setup, onboarding, presigned upload, and catalog JSON patterns.

**Negative**

- Prototype screens for Messages, Chat, Report, and search suggestions are not implemented; client must hide or defer them.
- Buyers cannot coordinate in-app; product relies on offline contact.
- No moderation path for bad listings in v1 except seller self-remove.

**Follow-ups**

- Chat and notifications per thread (new ADR if added)
- Reports and committee tools
- Search suggestions if product restores them
- Share to community feed when a resident feed exists
