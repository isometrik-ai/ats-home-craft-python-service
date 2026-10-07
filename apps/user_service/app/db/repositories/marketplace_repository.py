"""SQL for resident buy and sell (ADR 0019)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository

_LISTING_COLUMNS = """
    l.id::text AS id,
    l.organization_id::text AS organization_id,
    l.project_id::text AS project_id,
    l.unit_id::text AS unit_id,
    l.tower_id::text AS tower_id,
    l.seller_contact_id::text AS seller_contact_id,
    l.category,
    l.subtype,
    l.kind::text AS kind,
    l.status::text AS status,
    l.title,
    l.description,
    l.purchase_year,
    l.price_amount,
    l.original_price_amount,
    l.negotiable,
    l.brand,
    l.condition::text AS condition,
    l.product_url,
    l.show_flat_number,
    l.original_bill_available,
    l.rules_accepted_at,
    l.published_at,
    l.expires_at,
    l.renewal_count,
    l.sold_at,
    l.buyer_contact_id::text AS buyer_contact_id,
    l.removed_at,
    l.removal_note,
    l.removed_by_user_id::text AS removed_by_user_id,
    l.created_at,
    l.updated_at,
    p.name AS project_name,
    t.name AS tower_name,
    t.latitude AS tower_latitude,
    t.longitude AS tower_longitude,
    u.code AS unit_code,
    u.unit_label,
    seller.first_name AS seller_first_name,
    seller.last_name AS seller_last_name,
    seller.profile_photo_url AS seller_photo_url,
    role.role_type::text AS seller_role,
    role.started_at AS seller_role_started_at
"""

_LISTING_JOINS = """
FROM marketplace_listings l
JOIN projects p
  ON p.id = l.project_id
 AND p.organization_id = l.organization_id
JOIN units u
  ON u.id = l.unit_id
 AND u.organization_id = l.organization_id
LEFT JOIN towers t
  ON t.id = l.tower_id
 AND t.organization_id = l.organization_id
JOIN contacts seller
  ON seller.id = l.seller_contact_id
 AND seller.organization_id = l.organization_id
LEFT JOIN LATERAL (
    SELECT cr.role_type, cr.started_at
    FROM contact_roles cr
    WHERE cr.organization_id = l.organization_id
      AND cr.contact_id = l.seller_contact_id
      AND cr.unit_id = l.unit_id
      AND cr.status = 'active'::contact_role_status
    ORDER BY cr.started_at ASC
    LIMIT 1
) role ON true
"""


class MarketplaceRepository(BaseRepository):
    """Persistence for listings, media, saves, reports, and sale feedback."""

    async def expire_due(self, *, organization_id: str) -> None:
        """Mark live rows past expires_at as expired. Called on read, not by a job."""
        await self.db_connection.execute(
            """
            UPDATE marketplace_listings
               SET status = 'expired'::marketplace_listing_status,
                   updated_at = now()
             WHERE organization_id = $1::uuid
               AND status = 'live'::marketplace_listing_status
               AND expires_at IS NOT NULL
               AND expires_at <= now()
            """,
            organization_id,
        )

    async def get_unit_context(self, *, organization_id: str, unit_id: str) -> dict[str, Any] | None:
        """Load the flat, its society, and its tower coordinates."""
        return await self.db_connection.fetchrow(
            """
            SELECT u.id::text AS id,
                   u.project_id::text AS project_id,
                   u.tower_id::text AS tower_id,
                   u.code AS unit_code,
                   u.unit_label,
                   p.name AS project_name,
                   p.latitude AS project_latitude,
                   p.longitude AS project_longitude,
                   t.name AS tower_name,
                   t.latitude AS tower_latitude,
                   t.longitude AS tower_longitude
              FROM units u
              JOIN projects p
                ON p.id = u.project_id
               AND p.organization_id = u.organization_id
              LEFT JOIN towers t
                ON t.id = u.tower_id
               AND t.organization_id = u.organization_id
             WHERE u.organization_id = $1::uuid
               AND u.id = $2::uuid
            """,
            organization_id,
            unit_id,
        )

    async def get_posting_role(
        self,
        *,
        organization_id: str,
        contact_id: str,
        unit_id: str,
    ) -> dict[str, Any] | None:
        """Active Owner, Tenant, or Family role on the unit."""
        return await self.db_connection.fetchrow(
            """
            SELECT role_type::text AS role_type, started_at
              FROM contact_roles
             WHERE organization_id = $1::uuid
               AND contact_id = $2::uuid
               AND unit_id = $3::uuid
               AND status = 'active'::contact_role_status
               AND role_type IN (
                   'Owner'::contact_role_type,
                   'Tenant'::contact_role_type,
                   'Family'::contact_role_type
               )
             ORDER BY started_at ASC
             LIMIT 1
            """,
            organization_id,
            contact_id,
            unit_id,
        )

    async def get_contact_card(
        self, *, organization_id: str, contact_id: str
    ) -> dict[str, Any] | None:
        """Name and one active flat for a contact, used on the sold response."""
        row = await self.db_connection.fetchrow(
            """
            SELECT c.first_name,
                   c.last_name,
                   u.code AS unit_code,
                   u.unit_label,
                   t.name AS tower_name
              FROM contacts c
              LEFT JOIN contact_units cu
                ON cu.contact_id = c.id
               AND cu.organization_id = c.organization_id
               AND cu.status = 'active'::contact_unit_status
              LEFT JOIN units u
                ON u.id = cu.unit_id
               AND u.organization_id = cu.organization_id
              LEFT JOIN towers t
                ON t.id = u.tower_id
               AND t.organization_id = u.organization_id
             WHERE c.organization_id = $1::uuid
               AND c.id = $2::uuid
             ORDER BY cu.is_primary DESC NULLS LAST
             LIMIT 1
            """,
            organization_id,
            contact_id,
        )
        return dict(row) if row else None

    async def contact_in_organization(self, *, organization_id: str, contact_id: str) -> bool:
        """True when the contact has any active unit in the organization."""
        found = await self.db_connection.fetchval(
            """
            SELECT 1
              FROM contact_units
             WHERE organization_id = $1::uuid
               AND contact_id = $2::uuid
               AND status = 'active'::contact_unit_status
             LIMIT 1
            """,
            organization_id,
            contact_id,
        )
        return found is not None

    async def list_pickup_units(
        self, *, organization_id: str, contact_id: str
    ) -> list[dict[str, Any]]:
        """Active flats the caller may use as a pickup location."""
        rows = await self.db_connection.fetch(
            """
            SELECT u.id::text AS unit_id,
                   u.code AS unit_code,
                   u.unit_label,
                   p.name AS project_name
              FROM contact_units cu
              JOIN units u
                ON u.id = cu.unit_id
               AND u.organization_id = cu.organization_id
              JOIN projects p
                ON p.id = u.project_id
               AND p.organization_id = u.organization_id
              JOIN contact_roles cr
                ON cr.organization_id = cu.organization_id
               AND cr.contact_id = cu.contact_id
               AND cr.unit_id = cu.unit_id
               AND cr.status = 'active'::contact_role_status
               AND cr.role_type IN (
                   'Owner'::contact_role_type,
                   'Tenant'::contact_role_type,
                   'Family'::contact_role_type
               )
             WHERE cu.organization_id = $1::uuid
               AND cu.contact_id = $2::uuid
               AND cu.status = 'active'::contact_unit_status
             ORDER BY p.name, u.code
            """,
            organization_id,
            contact_id,
        )
        return [dict(row) for row in rows]

    async def count_towers(self, *, organization_id: str, project_id: str) -> int:
        """Tower count for the society header."""
        value = await self.db_connection.fetchval(
            """
            SELECT count(*)::int
              FROM towers
             WHERE organization_id = $1::uuid
               AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return int(value or 0)

    async def list_active_project_coords(self, *, organization_id: str) -> list[dict[str, Any]]:
        """Active projects that have a map pin, for the nearby set."""
        rows = await self.db_connection.fetch(
            """
            SELECT id::text AS id, latitude, longitude
              FROM projects
             WHERE organization_id = $1::uuid
               AND status = 'active'::project_status
               AND latitude IS NOT NULL
               AND longitude IS NOT NULL
            """,
            organization_id,
        )
        return [dict(row) for row in rows]

    async def insert_listing(self, **fields: Any) -> dict[str, Any]:
        """Insert a draft and return its id."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO marketplace_listings (
                organization_id, project_id, unit_id, tower_id, seller_contact_id,
                category, subtype, kind, status
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid,
                $6, $7, $8::marketplace_listing_kind, 'draft'::marketplace_listing_status
            )
            RETURNING id::text AS id
            """,
            fields["organization_id"],
            fields["project_id"],
            fields["unit_id"],
            fields.get("tower_id"),
            fields["seller_contact_id"],
            fields["category"],
            fields.get("subtype"),
            fields.get("kind", "sale"),
        )
        return dict(row)

    async def get_listing(self, *, organization_id: str, listing_id: str) -> dict[str, Any] | None:
        """One listing with seller, flat, and tower."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT {_LISTING_COLUMNS}
            {_LISTING_JOINS}
            WHERE l.organization_id = $1::uuid
              AND l.id = $2::uuid
            """,
            organization_id,
            listing_id,
        )
        return dict(row) if row else None

    async def update_listing(
        self,
        *,
        organization_id: str,
        listing_id: str,
        fields: dict[str, Any],
    ) -> None:
        """Update a whitelist of listing columns."""
        allowed = {
            "unit_id",
            "project_id",
            "tower_id",
            "category",
            "subtype",
            "kind",
            "status",
            "title",
            "description",
            "purchase_year",
            "price_amount",
            "original_price_amount",
            "negotiable",
            "brand",
            "condition",
            "product_url",
            "show_flat_number",
            "original_bill_available",
            "rules_accepted_at",
            "published_at",
            "expires_at",
            "renewal_count",
            "sold_at",
            "buyer_contact_id",
            "removed_at",
            "removal_note",
            "removed_by_user_id",
        }
        sets: list[str] = []
        params: list[Any] = [organization_id, listing_id]
        casts = {
            "unit_id": "::uuid",
            "project_id": "::uuid",
            "tower_id": "::uuid",
            "kind": "::marketplace_listing_kind",
            "status": "::marketplace_listing_status",
            "condition": "::marketplace_item_condition",
            "buyer_contact_id": "::uuid",
            "removed_by_user_id": "::uuid",
            "rules_accepted_at": "::timestamptz",
            "published_at": "::timestamptz",
            "expires_at": "::timestamptz",
            "sold_at": "::timestamptz",
            "removed_at": "::timestamptz",
        }
        for key, value in fields.items():
            if key not in allowed:
                continue
            params.append(value)
            cast = casts.get(key, "")
            sets.append(f"{key} = ${len(params)}{cast}")
        if not sets:
            return
        sets.append("updated_at = now()")
        await self.db_connection.execute(
            f"""
            UPDATE marketplace_listings
               SET {", ".join(sets)}
             WHERE organization_id = $1::uuid
               AND id = $2::uuid
            """,
            *params,
        )

    async def latest_draft(
        self, *, organization_id: str, seller_contact_id: str
    ) -> dict[str, Any] | None:
        """Newest draft for the home strip."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT {_LISTING_COLUMNS}
            {_LISTING_JOINS}
            WHERE l.organization_id = $1::uuid
              AND l.seller_contact_id = $2::uuid
              AND l.status = 'draft'::marketplace_listing_status
            ORDER BY l.updated_at DESC
            LIMIT 1
            """,
            organization_id,
            seller_contact_id,
        )
        return dict(row) if row else None

    async def count_live(self, *, organization_id: str, project_id: str) -> int:
        """Live listings in one society."""
        value = await self.db_connection.fetchval(
            """
            SELECT count(*)::int
              FROM marketplace_listings
             WHERE organization_id = $1::uuid
               AND project_id = $2::uuid
               AND status = 'live'::marketplace_listing_status
            """,
            organization_id,
            project_id,
        )
        return int(value or 0)

    async def list_recent(
        self, *, organization_id: str, project_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Newest live listings in one society."""
        rows = await self.db_connection.fetch(
            f"""
            SELECT {_LISTING_COLUMNS}
            {_LISTING_JOINS}
            WHERE l.organization_id = $1::uuid
              AND l.project_id = $2::uuid
              AND l.status = 'live'::marketplace_listing_status
            ORDER BY l.published_at DESC NULLS LAST
            LIMIT $3
            """,
            organization_id,
            project_id,
            limit,
        )
        return [dict(row) for row in rows]

    async def list_listings(
        self,
        *,
        organization_id: str,
        project_ids: list[str],
        tower_id: str | None,
        category: str | None,
        subtype: str | None,
        query: str | None,
        conditions: list[str],
        price_band: str | None,
        sort: str,
        viewer_lat: float | None,
        viewer_lng: float | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Public live feed with filters."""
        if not project_ids and tower_id is None:
            return [], 0
        where = [
            "l.organization_id = $1::uuid",
            "l.status = 'live'::marketplace_listing_status",
        ]
        params: list[Any] = [organization_id]
        if tower_id:
            params.append(tower_id)
            where.append(f"l.tower_id = ${len(params)}::uuid")
        else:
            params.append(project_ids)
            where.append(f"l.project_id = ANY(${len(params)}::uuid[])")
        if category:
            params.append(category)
            where.append(f"l.category = ${len(params)}")
        if subtype:
            params.append(subtype)
            where.append(f"l.subtype = ${len(params)}")
        if query:
            params.append(f"%{query.strip()}%")
            where.append(f"l.title ILIKE ${len(params)}")
        if conditions:
            params.append(conditions)
            where.append(f"l.condition = ANY(${len(params)}::marketplace_item_condition[])")
        if price_band == "free":
            where.append("l.kind = 'giveaway'::marketplace_listing_kind")
        elif price_band == "under_5000":
            where.append("l.kind = 'sale'::marketplace_listing_kind AND l.price_amount < 5000")
        elif price_band == "5000_20000":
            where.append(
                "l.kind = 'sale'::marketplace_listing_kind "
                "AND l.price_amount >= 5000 AND l.price_amount <= 20000"
            )
        elif price_band == "above_20000":
            where.append("l.kind = 'sale'::marketplace_listing_kind AND l.price_amount > 20000")

        where_sql = " AND ".join(where)
        total = await self.db_connection.fetchval(
            f"SELECT count(*)::int FROM marketplace_listings l WHERE {where_sql}",
            *params,
        )
        order = "l.published_at DESC NULLS LAST"
        if sort == "price_asc":
            order = "l.price_amount ASC NULLS FIRST, l.published_at DESC"
        elif sort == "price_desc":
            order = "l.price_amount DESC NULLS LAST, l.published_at DESC"
        elif sort == "closest" and viewer_lat is not None and viewer_lng is not None:
            params.append(viewer_lat)
            lat_idx = len(params)
            params.append(viewer_lng)
            lng_idx = len(params)
            order = f"""
                (t.latitude IS NULL OR t.longitude IS NULL),
                (
                    6371 * acos(least(1::float8, greatest(-1::float8,
                        cos(radians(${lat_idx})) * cos(radians(t.latitude))
                        * cos(radians(t.longitude) - radians(${lng_idx}))
                        + sin(radians(${lat_idx})) * sin(radians(t.latitude))
                    )))
                ) ASC NULLS LAST
            """
        params.append(page_size)
        limit_idx = len(params)
        params.append((page - 1) * page_size)
        offset_idx = len(params)
        rows = await self.db_connection.fetch(
            f"""
            SELECT {_LISTING_COLUMNS}
            {_LISTING_JOINS}
            WHERE {where_sql}
            ORDER BY {order}
            LIMIT ${limit_idx} OFFSET ${offset_idx}
            """,
            *params,
        )
        return [dict(row) for row in rows], int(total or 0)

    async def list_media(self, *, organization_id: str, listing_id: str) -> list[dict[str, Any]]:
        """Photos for one listing, cover first."""
        rows = await self.db_connection.fetch(
            """
            SELECT id::text AS id,
                   listing_id::text AS listing_id,
                   path,
                   file_type,
                   size_bytes,
                   original_name,
                   sort_order,
                   is_cover,
                   created_at
              FROM marketplace_listing_media
             WHERE organization_id = $1::uuid
               AND listing_id = $2::uuid
             ORDER BY is_cover DESC, sort_order ASC, created_at ASC
            """,
            organization_id,
            listing_id,
        )
        return [dict(row) for row in rows]

    async def count_media(self, *, organization_id: str, listing_id: str) -> int:
        """How many files a listing has."""
        value = await self.db_connection.fetchval(
            """
            SELECT count(*)::int
              FROM marketplace_listing_media
             WHERE organization_id = $1::uuid
               AND listing_id = $2::uuid
            """,
            organization_id,
            listing_id,
        )
        return int(value or 0)

    async def insert_media(self, **fields: Any) -> dict[str, Any]:
        """Store media metadata."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO marketplace_listing_media (
                organization_id, listing_id, path, file_type, size_bytes,
                original_name, sort_order, is_cover
            )
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8)
            RETURNING id::text AS id
            """,
            fields["organization_id"],
            fields["listing_id"],
            fields["path"],
            fields["file_type"],
            fields["size_bytes"],
            fields.get("original_name"),
            fields["sort_order"],
            fields["is_cover"],
        )
        return dict(row)

    async def clear_cover(self, *, organization_id: str, listing_id: str) -> None:
        """Drop the current cover flag before setting a new one."""
        await self.db_connection.execute(
            """
            UPDATE marketplace_listing_media
               SET is_cover = false
             WHERE organization_id = $1::uuid
               AND listing_id = $2::uuid
               AND is_cover = true
            """,
            organization_id,
            listing_id,
        )

    async def delete_media(
        self, *, organization_id: str, listing_id: str, media_id: str
    ) -> bool:
        """Delete one media row. True when a row was removed."""
        result = await self.db_connection.execute(
            """
            DELETE FROM marketplace_listing_media
             WHERE organization_id = $1::uuid
               AND listing_id = $2::uuid
               AND id = $3::uuid
            """,
            organization_id,
            listing_id,
            media_id,
        )
        return result.split()[-1] == "1"

    async def ensure_cover(self, *, organization_id: str, listing_id: str) -> None:
        """If nobody flagged a cover, the lowest sort_order becomes the cover."""
        await self.db_connection.execute(
            """
            UPDATE marketplace_listing_media
               SET is_cover = true
             WHERE id = (
                 SELECT id
                   FROM marketplace_listing_media
                  WHERE organization_id = $1::uuid
                    AND listing_id = $2::uuid
                    AND NOT EXISTS (
                        SELECT 1
                          FROM marketplace_listing_media cover
                         WHERE cover.listing_id = $2::uuid
                           AND cover.is_cover = true
                    )
                  ORDER BY sort_order ASC, created_at ASC
                  LIMIT 1
             )
            """,
            organization_id,
            listing_id,
        )

    async def is_saved(
        self, *, organization_id: str, contact_id: str, listing_id: str
    ) -> bool:
        """Whether this resident bookmarked the listing."""
        found = await self.db_connection.fetchval(
            """
            SELECT 1
              FROM marketplace_saved_items
             WHERE organization_id = $1::uuid
               AND contact_id = $2::uuid
               AND listing_id = $3::uuid
            """,
            organization_id,
            contact_id,
            listing_id,
        )
        return found is not None

    async def save_item(
        self, *, organization_id: str, contact_id: str, listing_id: str
    ) -> None:
        """Bookmark. A second save is a no-op."""
        await self.db_connection.execute(
            """
            INSERT INTO marketplace_saved_items (organization_id, contact_id, listing_id)
            VALUES ($1::uuid, $2::uuid, $3::uuid)
            ON CONFLICT (contact_id, listing_id) DO NOTHING
            """,
            organization_id,
            contact_id,
            listing_id,
        )

    async def unsave_item(
        self, *, organization_id: str, contact_id: str, listing_id: str
    ) -> None:
        """Remove a bookmark."""
        await self.db_connection.execute(
            """
            DELETE FROM marketplace_saved_items
             WHERE organization_id = $1::uuid
               AND contact_id = $2::uuid
               AND listing_id = $3::uuid
            """,
            organization_id,
            contact_id,
            listing_id,
        )

    async def list_saved(
        self, *, organization_id: str, contact_id: str
    ) -> list[dict[str, Any]]:
        """Live bookmarks, newest save first."""
        rows = await self.db_connection.fetch(
            f"""
            SELECT {_LISTING_COLUMNS}
            {_LISTING_JOINS}
            JOIN marketplace_saved_items s
              ON s.listing_id = l.id
             AND s.organization_id = l.organization_id
             AND s.contact_id = $2::uuid
            WHERE l.organization_id = $1::uuid
              AND l.status = 'live'::marketplace_listing_status
            ORDER BY s.created_at DESC
            """,
            organization_id,
            contact_id,
        )
        return [dict(row) for row in rows]

    async def list_mine(
        self,
        *,
        organization_id: str,
        seller_contact_id: str,
        status: str,
    ) -> list[dict[str, Any]]:
        """Seller's own listings. Sold rows older than a year are omitted."""
        status_sql = ""
        if status == "live":
            status_sql = "AND l.status = 'live'::marketplace_listing_status"
        elif status == "draft":
            status_sql = "AND l.status = 'draft'::marketplace_listing_status"
        elif status == "sold":
            status_sql = "AND l.status = 'sold'::marketplace_listing_status"
        elif status == "past":
            status_sql = (
                "AND l.status IN ("
                "'expired'::marketplace_listing_status, "
                "'removed'::marketplace_listing_status)"
            )
        rows = await self.db_connection.fetch(
            f"""
            SELECT {_LISTING_COLUMNS}
            {_LISTING_JOINS}
            WHERE l.organization_id = $1::uuid
              AND l.seller_contact_id = $2::uuid
              AND (
                    l.status <> 'sold'::marketplace_listing_status
                    OR l.sold_at >= now() - interval '1 year'
                  )
              {status_sql}
            ORDER BY l.updated_at DESC
            """,
            organization_id,
            seller_contact_id,
        )
        return [dict(row) for row in rows]

    async def earned_amount(self, *, organization_id: str, seller_contact_id: str) -> Any:
        """Sum of sold asking prices in the last 365 days."""
        return await self.db_connection.fetchval(
            """
            SELECT COALESCE(sum(price_amount), 0)
              FROM marketplace_listings
             WHERE organization_id = $1::uuid
               AND seller_contact_id = $2::uuid
               AND status = 'sold'::marketplace_listing_status
               AND sold_at >= now() - interval '1 year'
            """,
            organization_id,
            seller_contact_id,
        )

    async def count_other_live(
        self,
        *,
        organization_id: str,
        seller_contact_id: str,
        project_id: str,
        exclude_listing_id: str,
    ) -> int:
        """Other live posts by the same seller in the same society."""
        value = await self.db_connection.fetchval(
            """
            SELECT count(*)::int
              FROM marketplace_listings
             WHERE organization_id = $1::uuid
               AND seller_contact_id = $2::uuid
               AND project_id = $3::uuid
               AND status = 'live'::marketplace_listing_status
               AND id <> $4::uuid
            """,
            organization_id,
            seller_contact_id,
            project_id,
            exclude_listing_id,
        )
        return int(value or 0)

    async def insert_report(self, **fields: Any) -> dict[str, Any]:
        """Open a report. The partial unique index blocks a second open report."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO marketplace_reports (
                organization_id, project_id, listing_id, reporter_contact_id, reason
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::marketplace_report_reason
            )
            RETURNING id::text AS id
            """,
            fields["organization_id"],
            fields["project_id"],
            fields["listing_id"],
            fields["reporter_contact_id"],
            fields["reason"],
        )
        return dict(row)

    async def get_report(self, *, organization_id: str, report_id: str) -> dict[str, Any] | None:
        """One report."""
        row = await self.db_connection.fetchrow(
            """
            SELECT id::text AS id,
                   organization_id::text AS organization_id,
                   project_id::text AS project_id,
                   listing_id::text AS listing_id,
                   reporter_contact_id::text AS reporter_contact_id,
                   reason::text AS reason,
                   status::text AS status,
                   created_at,
                   reviewed_at,
                   reviewed_by_user_id::text AS reviewed_by_user_id
              FROM marketplace_reports
             WHERE organization_id = $1::uuid
               AND id = $2::uuid
            """,
            organization_id,
            report_id,
        )
        return dict(row) if row else None

    async def list_reports(
        self, *, organization_id: str, status: str | None
    ) -> list[dict[str, Any]]:
        """Reports in the organization. The API drops rows the staff member cannot open."""
        status_sql = ""
        params: list[Any] = [organization_id]
        if status:
            params.append(status)
            status_sql = f"AND status = ${len(params)}::marketplace_report_status"
        rows = await self.db_connection.fetch(
            f"""
            SELECT id::text AS id,
                   organization_id::text AS organization_id,
                   project_id::text AS project_id,
                   listing_id::text AS listing_id,
                   reporter_contact_id::text AS reporter_contact_id,
                   reason::text AS reason,
                   status::text AS status,
                   created_at,
                   reviewed_at,
                   reviewed_by_user_id::text AS reviewed_by_user_id
              FROM marketplace_reports
             WHERE organization_id = $1::uuid
               {status_sql}
             ORDER BY created_at DESC
            """,
            *params,
        )
        return [dict(row) for row in rows]

    async def review_report(
        self,
        *,
        organization_id: str,
        report_id: str,
        status: str,
        reviewed_by_user_id: str,
        reviewed_at: datetime,
    ) -> None:
        """Close a report as upheld or dismissed."""
        await self.db_connection.execute(
            """
            UPDATE marketplace_reports
               SET status = $3::marketplace_report_status,
                   reviewed_at = $4,
                   reviewed_by_user_id = $5::uuid
             WHERE organization_id = $1::uuid
               AND id = $2::uuid
            """,
            organization_id,
            report_id,
            status,
            reviewed_at,
            reviewed_by_user_id,
        )

    async def insert_feedback(self, **fields: Any) -> None:
        """One private rating per sold listing."""
        await self.db_connection.execute(
            """
            INSERT INTO marketplace_sale_feedback (
                organization_id, listing_id, seller_contact_id, buyer_contact_id, rating
            )
            VALUES (
                $1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::marketplace_sale_rating
            )
            """,
            fields["organization_id"],
            fields["listing_id"],
            fields["seller_contact_id"],
            fields["buyer_contact_id"],
            fields["rating"],
        )

    async def feedback_patterns(self, *, organization_id: str) -> list[dict[str, Any]]:
        """Sellers with three or more had_trouble ratings. No individual ratings."""
        rows = await self.db_connection.fetch(
            """
            SELECT seller_contact_id::text AS seller_contact_id,
                   count(*)::int AS had_trouble_count
              FROM marketplace_sale_feedback
             WHERE organization_id = $1::uuid
               AND rating = 'had_trouble'::marketplace_sale_rating
             GROUP BY seller_contact_id
            HAVING count(*) >= 3
            """,
            organization_id,
        )
        return [dict(row) for row in rows]
