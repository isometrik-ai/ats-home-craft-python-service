"""Staff buy and sell rules (organization-scoped, ADR 0018)."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.marketplace_repository import (
    MarketplaceRepository,
)
from apps.user_service.app.schemas.marketplace import (
    AdminMarketplaceListQuery,
    AdminMarketplaceReportListQuery,
)
from apps.user_service.app.services.marketplace_catalog_service import (
    MarketplaceCatalogService,
)
from apps.user_service.app.services.marketplace_service import (
    _as_utc,
    _float_or_none,
    _money,
    _now,
    _year,
    cover_path,
    flat_label,
    listing_media,
    public_name,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class MarketplaceAdminService:
    """Summary, listings, reports, and moderation for staff."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = MarketplaceRepository(db_connection)

    def _org(self) -> str:
        """Organization id from the staff session."""
        org_id = self.user_context.organization_id
        if not org_id:
            raise ValidationException(
                message_key="auth.errors.session_not_found",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )
        return str(org_id)

    async def get_admin_summary(self, *, project_id: str | None = None) -> dict[str, int]:
        """Active, sold, past, and removed counts for the staff header."""
        await self.repo.expire_due(organization_id=self._org())
        return await self.repo.get_admin_summary(
            organization_id=self._org(),
            project_id=project_id,
        )

    async def list_listings_admin(
        self,
        *,
        query: AdminMarketplaceListQuery,
    ) -> dict[str, Any]:
        """Flat staff list: search, status, and category. Optional society filter."""
        await self.repo.expire_due(organization_id=self._org())
        category_slug = MarketplaceCatalogService.parse_filter(query.category, None)[0]
        rows, total = await self.repo.list_admin_listings(
            organization_id=self._org(),
            query=query,
            category=category_slug,
        )
        return {"items": [self._admin_card(row) for row in rows], "total": total}

    async def list_reports_admin(
        self,
        *,
        query: AdminMarketplaceReportListQuery,
    ) -> dict[str, Any]:
        """Staff Reported tab: one row per report with human-readable reason text."""
        rows, total = await self.repo.list_admin_reports(
            organization_id=self._org(),
            query=query,
        )
        return {"items": [self._admin_report_row(row) for row in rows], "total": total}

    async def get_listing_admin(
        self, *, listing_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Staff drawer for one posted listing in the organization."""
        await self.repo.expire_due(organization_id=self._org())
        listing = await self.repo.get_admin_listing(
            organization_id=self._org(),
            listing_id=listing_id,
            project_id=project_id,
        )
        if not listing:
            raise NotFoundException(
                message_key="marketplace.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        stats = await self.repo.count_unit_listing_stats(
            organization_id=self._org(),
            unit_id=listing["unit_id"],
            exclude_listing_id=listing_id,
        )
        return self._admin_detail(listing, stats)

    async def remove_listing_admin(
        self,
        *,
        listing_id: str,
        removal_note: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Draft: hard-delete. Live: status removed (same as seller take-down)."""
        note = removal_note.strip()
        if not note:
            raise ValidationException(
                message_key="marketplace.errors.removal_note_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        actor_user_id = self.user_context.user_id
        if not actor_user_id:
            raise ValidationException(
                message_key="auth.errors.session_not_found",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )
        org_id = self._org()
        listing = await self.repo.get_admin_listing(
            organization_id=org_id,
            listing_id=listing_id,
            project_id=project_id,
        )
        if not listing:
            raise NotFoundException(
                message_key="marketplace.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if listing["status"] == "draft":
            deleted = await self.repo.delete_draft_listing_admin(
                organization_id=org_id,
                listing_id=listing_id,
                project_id=project_id,
            )
            if not deleted:
                raise NotFoundException(
                    message_key="marketplace.errors.not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
            return {
                "id": listing_id,
                "status": "deleted",
                "project_id": listing.get("project_id"),
                "title": listing.get("title"),
            }
        if listing["status"] != "live":
            raise ValidationException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        updated = await self.repo.remove_live_listing_admin(
            organization_id=org_id,
            listing_id=listing_id,
            removal_note=note,
            removed_by_user_id=actor_user_id,
            removed_at=_now(),
            project_id=project_id,
        )
        if not updated:
            raise ValidationException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return await self.get_listing_admin(listing_id=listing_id, project_id=project_id)

    def _admin_report_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a joined report row to the staff table shape."""
        reason_slug = str(row.get("reason") or "")
        reason = MarketplaceCatalogService.report_reason_label(reason_slug)
        if not reason:
            reason = reason_slug.replace("_", " ").strip().title() or reason_slug
        category_name, _ = MarketplaceCatalogService.labels(str(row["category"]), None)
        listing_unit = flat_label(row.get("listing_unit_label"), row.get("listing_unit_code"))
        if category_name and listing_unit:
            item_subtitle = f"{category_name} - {listing_unit}"
        else:
            item_subtitle = category_name or listing_unit
        media = listing_media(row)
        return {
            "id": row["id"],
            "listing_id": row["listing_id"],
            "project_id": row["project_id"],
            "title": row.get("title"),
            "item_subtitle": item_subtitle,
            "cover_path": cover_path(media),
            "reason": reason,
            "reporter_name": public_name(
                row.get("reporter_first_name"), row.get("reporter_last_name")
            ),
            "reporter_unit_label": flat_label(
                row.get("reporter_unit_label"), row.get("reporter_unit_code")
            ),
            "reported_at": row.get("created_at"),
            "listing_report_count": int(row.get("listing_report_count") or 1),
            "status": row["status"],
        }

    def _admin_card(self, row: dict[str, Any]) -> dict[str, Any]:
        """One row on the staff listings table."""
        media = listing_media(row)
        category_name, subtype_name = MarketplaceCatalogService.labels(
            str(row["category"]), row.get("subtype")
        )
        days_left = None
        if row["status"] == "live" and row.get("expires_at"):
            remaining = _as_utc(row["expires_at"]) - _now()
            days_left = max(0, remaining.days)
        return {
            "id": row["id"],
            "title": row.get("title"),
            "status": row["status"],
            "kind": row["kind"],
            "price_amount": _money(row.get("price_amount")),
            "category": row["category"],
            "category_name": category_name,
            "subtype": row.get("subtype"),
            "subtype_name": subtype_name,
            "cover_path": cover_path(media),
            "resident_name": public_name(row.get("seller_first_name"), row.get("seller_last_name")),
            "seller_photo_url": row.get("seller_photo_url"),
            "project_id": row.get("project_id"),
            "project_name": row.get("project_name"),
            "unit_label": flat_label(row.get("unit_label"), row.get("unit_code")),
            "tower_name": row.get("tower_name"),
            "published_at": row.get("published_at"),
            "expires_at": row.get("expires_at"),
            "days_left": days_left,
            "sold_at": row.get("sold_at"),
            "removed_at": row.get("removed_at"),
            "can_remove": row["status"] in ("live", "draft"),
        }

    def _admin_detail(self, row: dict[str, Any], stats: dict[str, int]) -> dict[str, Any]:
        """Staff drawer payload, including unit stats and derived history."""
        card = self._admin_card(row)
        year = row.get("purchase_year")
        age_years = None
        if year is not None:
            age_years = max(0, _now().year - int(year))
        card.update(
            {
                "description": row.get("description"),
                "purchase_year": year,
                "age_years": age_years,
                "negotiable": row.get("negotiable"),
                "brand": row.get("brand"),
                "condition": row.get("condition"),
                "product_url": row.get("product_url"),
                "original_price_amount": _money(row.get("original_price_amount")),
                "original_bill_available": row.get("original_bill_available"),
                "show_flat_number": row.get("show_flat_number"),
                "seller_role": row.get("seller_role"),
                "member_since_year": _year(row.get("seller_role_started_at")),
                "collection_latitude": _float_or_none(row.get("tower_latitude")),
                "collection_longitude": _float_or_none(row.get("tower_longitude")),
                "media": listing_media(row),
                "removal_note": row.get("removal_note"),
                "listings_from_unit_total": stats["listings_from_unit_total"],
                "listings_from_unit_active": stats["listings_from_unit_active"],
                "removed_before_count": stats["removed_before_count"],
                "history": self._admin_history(row),
            }
        )
        return card

    @staticmethod
    def _removed_by_name(row: dict[str, Any]) -> str | None:
        """Staff member name, otherwise the resident who took the listing down."""
        staff = public_name(
            row.get("removed_by_staff_first_name"),
            row.get("removed_by_staff_last_name"),
        )
        if staff:
            return staff
        contact = public_name(
            row.get("removed_by_contact_first_name"),
            row.get("removed_by_contact_last_name"),
        )
        return contact or None

    def _admin_history(self, row: dict[str, Any]) -> list[dict[str, Any]]:
        """Lifecycle events derived from listing timestamps. No event table."""
        seller = public_name(row.get("seller_first_name"), row.get("seller_last_name"))
        events: list[dict[str, Any]] = []
        posted_at = row.get("published_at") or row.get("created_at")
        if posted_at:
            events.append({"event": "posted", "at": posted_at, "actor_name": seller})
        if row.get("published_at"):
            events.append({"event": "live", "at": row["published_at"]})
        if row.get("sold_at"):
            events.append({"event": "sold", "at": row["sold_at"]})
        if row["status"] == "expired" and row.get("expires_at"):
            events.append({"event": "expired", "at": row["expires_at"]})
        if row.get("removed_at"):
            events.append(
                {
                    "event": "removed",
                    "at": row["removed_at"],
                    "actor_name": self._removed_by_name(row) or seller,
                    "note": row.get("removal_note"),
                }
            )
        return events
