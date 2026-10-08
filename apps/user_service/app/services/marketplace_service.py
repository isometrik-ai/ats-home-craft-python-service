"""Buy and sell business rules (ADR 0019)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.marketplace_repository import (
    MarketplaceRepository,
)
from apps.user_service.app.schemas.marketplace import (
    CreateListingRequest,
    ListingMediaInput,
    MarkSoldRequest,
    PublishListingRequest,
    UpdateListingRequest,
)
from apps.user_service.app.services.marketplace_catalog_service import (
    MarketplaceCatalogService,
)
from apps.user_service.app.services.marketplace_geo import (
    MARKETPLACE_NEARBY_RADIUS_KM,
    haversine_km,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

LIVE_WINDOW = timedelta(days=30)
SOLD_HISTORY = timedelta(days=365)
MAX_MEDIA = 8
MIN_PUBLISH_MEDIA = 2
PURCHASE_YEAR_MIN = 1980
_KOLKATA = ZoneInfo("Asia/Kolkata")
_EDITABLE = frozenset({"draft", "live"})
_MEDIA_MIMES = {
    "image": frozenset({"image/jpeg", "image/png"}),
    "video": frozenset({"video/mp4"}),
}


def public_name(first_name: str | None, last_name: str | None) -> str:
    """First name plus last initial, as on the listing card."""
    first = (first_name or "").strip()
    last = (last_name or "").strip()
    if last:
        return f"{first} {last[0]}.".strip()
    return first


def flat_label(unit_label: str | None, unit_code: str | None) -> str | None:
    """Prefer the display label, otherwise the unit code."""
    label = (unit_label or "").strip()
    if label:
        return label
    code = (unit_code or "").strip()
    return code or None


def visible_flat(
    *,
    listing: dict[str, Any],
    viewer_contact_id: str,
    viewer_project_id: str | None,
) -> str | None:
    """The only place a flat number is shown or hidden."""
    label = flat_label(listing.get("unit_label"), listing.get("unit_code"))
    if str(listing.get("seller_contact_id")) == str(viewer_contact_id):
        return label
    if viewer_project_id is None:
        return None
    same_society = str(listing.get("project_id")) == str(viewer_project_id)
    if same_society and listing.get("show_flat_number"):
        return label
    return None


def listing_media(listing: dict[str, Any]) -> list[dict[str, Any]]:
    """Ordered media documents from the listing jsonb column."""
    items = listing.get("media") or []
    if not isinstance(items, list):
        return []
    return sorted(items, key=lambda item: int(item.get("order") or 0))


def cover_path(media: list[dict[str, Any]]) -> str | None:
    """Card image: first still, otherwise the first item's preview or path."""
    for item in media:
        if item.get("type") == "image" and item.get("path"):
            return item["path"]
    if not media:
        return None
    return media[0].get("preview_path") or media[0].get("path")


def publish_gaps(listing: dict[str, Any], media_count: int) -> list[str]:
    """Fields still required before a draft can go live."""
    gaps: list[str] = []
    if media_count < MIN_PUBLISH_MEDIA:
        gaps.append("media")
    if not (listing.get("title") or "").strip():
        gaps.append("title")
    if not (listing.get("description") or "").strip():
        gaps.append("description")
    if listing.get("purchase_year") is None:
        gaps.append("purchase_year")
    if listing.get("kind", "sale") == "sale" and _money(listing.get("price_amount")) is None:
        gaps.append("price")
    if not listing.get("condition"):
        gaps.append("condition")
    if not listing.get("unit_id"):
        gaps.append("pickup")
    if MarketplaceCatalogService.category_requires_subtype(str(listing.get("category") or "")):
        if not (listing.get("subtype") or "").strip():
            gaps.append("subtype")
    return gaps


def _money(value: Any) -> str | None:
    """Decimal string for the API, or None."""
    if value is None:
        return None
    return format(Decimal(str(value)), "f")


def _now() -> datetime:
    """Current UTC time."""
    return datetime.now(UTC)


class MarketplaceService:  # pylint: disable=too-many-public-methods
    """Listings, saves, and sale feedback."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = MarketplaceRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)

    def _org(self) -> str:
        """Organization on the signed-in session."""
        org_id = self.user_context.organization_id
        if not org_id:
            raise ValidationException(
                message_key="auth.errors.session_not_found",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )
        return org_id

    async def _require_unit(self, *, contact_id: str, unit_id: str) -> dict[str, Any]:
        """Active unit for this contact, or raise."""
        org_id = self._org()
        has_unit = await self.contact_units_repo.contact_has_active_unit(
            organization_id=org_id,
            contact_id=contact_id,
            unit_id=unit_id,
        )
        if not has_unit:
            raise ValidationException(
                message_key="contact_onboarding.errors.unit_not_assigned",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        unit = await self.repo.get_unit_context(organization_id=org_id, unit_id=unit_id)
        if not unit:
            raise NotFoundException(
                message_key="contact_onboarding.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return dict(unit)

    async def _require_poster(self, *, contact_id: str, unit_id: str) -> dict[str, Any]:
        """Unit the contact may post from. Owner, tenant, or family."""
        unit = await self._require_unit(contact_id=contact_id, unit_id=unit_id)
        role = await self.repo.get_posting_role(
            organization_id=self._org(),
            contact_id=contact_id,
            unit_id=unit_id,
        )
        if not role:
            raise ValidationException(
                message_key="marketplace.errors.cannot_post",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return unit

    async def _listing_for_seller(self, *, contact_id: str, listing_id: str) -> dict[str, Any]:
        """Listing owned by this seller, or 404."""
        listing = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        if not listing or str(listing["seller_contact_id"]) != str(contact_id):
            raise NotFoundException(
                message_key="marketplace.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return listing

    async def get_catalog(self) -> dict[str, Any]:
        """Static categories."""
        return MarketplaceCatalogService.get_catalog()

    async def create_listing(
        self, *, contact_id: str, body: CreateListingRequest
    ) -> dict[str, Any]:
        """Create a listing. Status is unpublished until publish."""
        unit = await self._require_poster(contact_id=contact_id, unit_id=body.unit_id)
        category, subtype = MarketplaceCatalogService.resolve(body.category, body.subtype)
        created = await self.repo.insert_listing(
            organization_id=self._org(),
            project_id=unit["project_id"],
            unit_id=unit["id"],
            tower_id=unit.get("tower_id"),
            seller_contact_id=contact_id,
            category=category,
            subtype=subtype,
            kind=body.kind.value,
        )
        listing_id = created["id"]
        fields = self._create_fields(body, unit)
        fields["media"] = self._media_documents(body.media)
        if fields:
            await self.repo.update_listing(
                organization_id=self._org(), listing_id=listing_id, fields=fields
            )
        listing = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        assert listing
        return await self._detail(
            listing, viewer_contact_id=contact_id, viewer_project_id=str(unit["project_id"])
        )

    def _create_fields(self, body: CreateListingRequest, unit: dict[str, Any]) -> dict[str, Any]:
        """Listing columns sent on create (excluding category handled at insert)."""
        fields: dict[str, Any] = {
            "unit_id": unit["id"],
            "project_id": unit["project_id"],
            "tower_id": unit.get("tower_id"),
            "kind": body.kind.value,
        }
        if body.title is not None:
            fields["title"] = body.title
        if body.description is not None:
            fields["description"] = body.description
        if body.purchase_year is not None:
            self._check_year(body.purchase_year)
            fields["purchase_year"] = body.purchase_year
        if body.condition is not None:
            fields["condition"] = body.condition.value
        if body.brand is not None:
            fields["brand"] = body.brand
        if body.product_url is not None:
            self._check_url(body.product_url)
            fields["product_url"] = body.product_url
        fields["show_flat_number"] = body.show_flat_number
        fields["original_bill_available"] = body.original_bill_available
        if body.kind.value == "giveaway":
            fields["price_amount"] = None
            fields["original_price_amount"] = None
            fields["negotiable"] = False
        else:
            if body.price_amount is not None:
                fields["price_amount"] = body.price_amount
            if body.original_price_amount is not None:
                fields["original_price_amount"] = body.original_price_amount
            fields["negotiable"] = body.negotiable
        return fields

    @staticmethod
    def _media_documents(media: list[ListingMediaInput]) -> list[dict[str, Any]]:
        """Normalize create-time media into the listing jsonb array."""
        if len(media) > MAX_MEDIA:
            raise ValidationException(
                message_key="marketplace.errors.too_many_files",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if not media:
            return []
        documents: list[dict[str, Any]] = []
        for item in media:
            kind = item.type.value
            file_type = item.file_type.strip().lower()
            if file_type not in _MEDIA_MIMES[kind]:
                raise ValidationException(
                    message_key="marketplace.errors.invalid_file_type",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            description = (item.description or "").strip() or None
            preview_path = (item.preview_path or "").strip() or None
            documents.append(
                {
                    "type": kind,
                    "path": item.path.strip(),
                    "file_type": file_type,
                    "preview_path": preview_path,
                    "description": description,
                    "order": item.order,
                }
            )
        return documents

    async def update_listing(
        self,
        *,
        contact_id: str,
        listing_id: str,
        body: UpdateListingRequest,
    ) -> dict[str, Any]:
        """Edit a draft or live listing. Status is not patched; use publish/remove/mark-sold."""
        await self._require_poster(contact_id=contact_id, unit_id=body.unit_id)
        listing = await self._listing_for_seller(contact_id=contact_id, listing_id=listing_id)
        if listing["status"] not in _EDITABLE:
            raise ValidationException(
                message_key="marketplace.errors.not_editable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if body.pickup_unit_id:
            unit = await self._require_poster(contact_id=contact_id, unit_id=body.pickup_unit_id)
        else:
            unit = await self.repo.get_unit_context(
                organization_id=self._org(), unit_id=listing["unit_id"]
            )
            assert unit
        fields = self._patch_fields(listing, body, unit)
        fields.pop("status", None)
        merged = {**listing, **fields}
        if listing["status"] == "live":
            gaps = publish_gaps(merged, len(listing_media(merged)))
            self._raise_price_rules(merged)
            if gaps:
                raise ValidationException(
                    message_key="marketplace.errors.live_incomplete",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"missing": gaps},
                )
        await self.repo.update_listing(
            organization_id=self._org(), listing_id=listing_id, fields=fields
        )
        updated = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        assert updated
        return await self._detail(
            updated,
            viewer_contact_id=contact_id,
            viewer_project_id=str(unit["project_id"]),
        )

    def _patch_fields(
        self,
        listing: dict[str, Any],
        body: UpdateListingRequest,
        unit: dict[str, Any],
    ) -> dict[str, Any]:
        """Columns changed by a partial edit."""
        fields: dict[str, Any] = {}
        if body.pickup_unit_id:
            fields["unit_id"] = unit["id"]
            fields["project_id"] = unit["project_id"]
            fields["tower_id"] = unit.get("tower_id")
        provided = body.model_fields_set
        self._copy_text_fields(fields, body, provided)
        self._apply_kind_and_price(fields, listing, body, provided)
        return fields

    def _copy_text_fields(
        self,
        fields: dict[str, Any],
        body: UpdateListingRequest,
        provided: set[str],
    ) -> None:
        """Copy text, year, condition, and toggles that were sent."""
        for name in ("title", "description", "brand"):
            if name in provided:
                fields[name] = getattr(body, name)
        if "purchase_year" in provided:
            self._check_year(body.purchase_year)
            fields["purchase_year"] = body.purchase_year
        if "condition" in provided and body.condition is not None:
            fields["condition"] = body.condition.value
        if "product_url" in provided:
            self._check_url(body.product_url)
            fields["product_url"] = body.product_url
        for name in ("show_flat_number", "original_bill_available"):
            if name in provided:
                fields[name] = bool(getattr(body, name))

    def _apply_kind_and_price(
        self,
        fields: dict[str, Any],
        listing: dict[str, Any],
        body: UpdateListingRequest,
        provided: set[str],
    ) -> None:
        """Set sale price fields. A giveaway clears price instead."""
        if "kind" in provided and body.kind is not None:
            fields["kind"] = body.kind.value
            if body.kind.value == "giveaway":
                fields["price_amount"] = None
                fields["original_price_amount"] = None
                fields["negotiable"] = False
                return
        if fields.get("kind", listing["kind"]) == "giveaway":
            return
        if "price_amount" in provided:
            fields["price_amount"] = body.price_amount
        if "original_price_amount" in provided:
            fields["original_price_amount"] = body.original_price_amount
        if "negotiable" in provided:
            fields["negotiable"] = bool(body.negotiable)

    async def publish(
        self, *, contact_id: str, listing_id: str, body: PublishListingRequest
    ) -> dict[str, Any]:
        """First post, or republish after the committee sent it back to draft."""
        unit = await self._require_poster(contact_id=contact_id, unit_id=body.unit_id)
        listing = await self._listing_for_seller(contact_id=contact_id, listing_id=listing_id)
        if listing["status"] != "draft":
            raise ValidationException(
                message_key="marketplace.errors.not_editable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if not body.rules_accepted:
            raise ValidationException(
                message_key="marketplace.errors.rules_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self._assert_publishable(listing)
        now = _now()
        await self.repo.update_listing(
            organization_id=self._org(),
            listing_id=listing_id,
            fields={
                "status": "live",
                "rules_accepted_at": now,
                "published_at": now,
                "expires_at": now + LIVE_WINDOW,
                "removed_at": None,
                "removal_note": None,
                "removed_by_user_id": None,
            },
        )
        updated = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        assert updated
        return {
            "id": listing_id,
            "published_at": updated["published_at"],
            "expires_at": updated["expires_at"],
            "project_name": unit["project_name"],
        }

    async def remove_listing(
        self,
        *,
        contact_id: str,
        listing_id: str,
        unit_id: str,
        removal_note: str,
    ) -> dict[str, Any]:
        """Soft-delete a live post (status removed, hidden from browse)."""
        await self._require_poster(contact_id=contact_id, unit_id=unit_id)
        listing = await self._listing_for_seller(contact_id=contact_id, listing_id=listing_id)
        if listing["status"] != "live":
            raise ValidationException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
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
        now = _now()
        await self.repo.update_listing(
            organization_id=self._org(),
            listing_id=listing_id,
            fields={
                "status": "removed",
                "removed_at": now,
                "removal_note": note,
                "removed_by_user_id": actor_user_id,
            },
        )
        updated = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        assert updated
        return {
            "id": listing_id,
            "status": updated["status"],
            "removed_at": updated.get("removed_at"),
            "removal_note": updated.get("removal_note"),
        }

    async def renew(self, *, contact_id: str, listing_id: str, unit_id: str) -> dict[str, Any]:
        """Add 30 days, keeping any time still left."""
        await self._require_poster(contact_id=contact_id, unit_id=unit_id)
        listing = await self._listing_for_seller(contact_id=contact_id, listing_id=listing_id)
        if listing["status"] != "live":
            raise ValidationException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        now = _now()
        current = _as_utc(listing["expires_at"]) if listing.get("expires_at") else now
        expires_at = max(current, now) + LIVE_WINDOW
        await self.repo.update_listing(
            organization_id=self._org(),
            listing_id=listing_id,
            fields={"expires_at": expires_at, "renewal_count": int(listing["renewal_count"]) + 1},
        )
        return {"id": listing_id, "expires_at": expires_at}

    async def relist(self, *, contact_id: str, listing_id: str, unit_id: str) -> dict[str, Any]:
        """Start a new 30-day window for an expired post."""
        await self._require_poster(contact_id=contact_id, unit_id=unit_id)
        listing = await self._listing_for_seller(contact_id=contact_id, listing_id=listing_id)
        if listing["status"] != "expired":
            raise ValidationException(
                message_key="marketplace.errors.not_expired",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self._assert_publishable(listing)
        now = _now()
        await self.repo.update_listing(
            organization_id=self._org(),
            listing_id=listing_id,
            fields={"status": "live", "published_at": now, "expires_at": now + LIVE_WINDOW},
        )
        return {"id": listing_id, "published_at": now, "expires_at": now + LIVE_WINDOW}

    async def mark_sold(
        self, *, contact_id: str, listing_id: str, body: MarkSoldRequest
    ) -> dict[str, Any]:
        """Record the buyer. The rating stays off the public listing."""
        await self._require_poster(contact_id=contact_id, unit_id=body.unit_id)
        listing = await self._listing_for_seller(contact_id=contact_id, listing_id=listing_id)
        if listing["status"] != "live":
            raise ValidationException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if str(body.buyer_contact_id) == str(contact_id):
            raise ValidationException(
                message_key="marketplace.errors.buyer_is_seller",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        buyer_ok = await self.repo.contact_in_organization(
            organization_id=self._org(), contact_id=body.buyer_contact_id
        )
        if not buyer_ok:
            raise ValidationException(
                message_key="marketplace.errors.buyer_not_resident",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        now = _now()
        await self.repo.update_listing(
            organization_id=self._org(),
            listing_id=listing_id,
            fields={"status": "sold", "sold_at": now, "buyer_contact_id": body.buyer_contact_id},
        )
        if body.rating is not None:
            await self.repo.insert_feedback(
                organization_id=self._org(),
                listing_id=listing_id,
                seller_contact_id=contact_id,
                buyer_contact_id=body.buyer_contact_id,
                rating=body.rating.value,
            )
        buyer = await self.repo.get_contact_card(
            organization_id=self._org(), contact_id=body.buyer_contact_id
        )
        return {
            "id": listing_id,
            "title": listing.get("title"),
            "buyer_name": public_name(
                (buyer or {}).get("first_name"), (buyer or {}).get("last_name")
            ),
            "buyer_flat": flat_label(
                (buyer or {}).get("unit_label"), (buyer or {}).get("unit_code")
            ),
            "price_amount": _money(listing.get("price_amount")),
        }

    async def save(self, *, contact_id: str, listing_id: str, unit_id: str) -> None:
        """Bookmark a live listing."""
        await self._require_unit(contact_id=contact_id, unit_id=unit_id)
        await self.repo.expire_due(organization_id=self._org())
        listing = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        if not listing or listing["status"] != "live":
            raise NotFoundException(
                message_key="marketplace.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        await self.repo.save_item(
            organization_id=self._org(), contact_id=contact_id, listing_id=listing_id
        )

    async def unsave(self, *, contact_id: str, listing_id: str, unit_id: str) -> None:
        """Remove a bookmark."""
        await self._require_unit(contact_id=contact_id, unit_id=unit_id)
        await self.repo.unsave_item(
            organization_id=self._org(), contact_id=contact_id, listing_id=listing_id
        )

    async def list_saved(
        self,
        *,
        contact_id: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Live bookmarks."""
        await self.repo.expire_due(organization_id=self._org())
        rows, total = await self.repo.list_saved(
            organization_id=self._org(),
            contact_id=contact_id,
            page=page,
            page_size=page_size,
        )
        items = [
            await self._card(row, viewer_contact_id=contact_id, viewer_project_id=None)
            for row in rows
        ]
        return {"items": items, "total": total}

    async def get_listing(self, *, contact_id: str, listing_id: str) -> dict[str, Any]:
        """Listing detail. Opening it can expire a post that has run out of days."""
        await self.repo.expire_due(organization_id=self._org())
        listing = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        if not listing:
            raise NotFoundException(
                message_key="marketplace.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        seller = str(listing["seller_contact_id"]) == str(contact_id)
        if not seller and listing["status"] != "live":
            raise NotFoundException(
                message_key="marketplace.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return await self._detail(listing, viewer_contact_id=contact_id, viewer_project_id=None)

    async def list_listings(
        self,
        *,
        contact_id: str,
        category: str | None,
        subtype: str | None,
        query: str | None,
        sort: str,
        price_band: str | None,
        conditions: list[str],
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Browse live listings org-wide."""
        await self.repo.expire_due(organization_id=self._org())
        category_id, subtype_id = MarketplaceCatalogService.parse_filter(category, subtype)
        rows, total = await self.repo.list_listings(
            organization_id=self._org(),
            org_wide=True,
            project_ids=[],
            tower_id=None,
            category=category_id,
            subtype=subtype_id,
            query=query,
            conditions=conditions,
            price_band=price_band,
            sort=sort,
            viewer_lat=None,
            viewer_lng=None,
            page=page,
            page_size=page_size,
        )
        items = [
            await self._card(row, viewer_contact_id=contact_id, viewer_project_id=None)
            for row in rows
        ]
        return {"items": items, "total": total, "nearby_project_count": 0}

    async def my_listings(
        self,
        *,
        contact_id: str,
        status: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """The seller's own posts across the organization."""
        await self.repo.expire_due(organization_id=self._org())
        rows, total = await self.repo.list_mine(
            organization_id=self._org(),
            seller_contact_id=contact_id,
            status=status,
            page=page,
            page_size=page_size,
        )
        items = [self._mine_card(row, len(listing_media(row))) for row in rows]
        earned = await self.repo.earned_amount(
            organization_id=self._org(), seller_contact_id=contact_id
        )
        return {
            "earned_amount": _money(earned),
            "items": items,
            "total": total,
        }

    async def _assert_publishable(self, listing: dict[str, Any]) -> None:
        """Raise when a listing is not ready to go live."""
        media = listing_media(listing)
        gaps = publish_gaps(listing, len(media))
        self._raise_price_rules(listing)
        year = listing.get("purchase_year")
        if year is not None:
            self._check_year(int(year))
        self._check_url(listing.get("product_url"))
        if gaps:
            raise ValidationException(
                message_key="marketplace.errors.incomplete",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"missing": gaps},
            )

    def _raise_price_rules(self, listing: dict[str, Any]) -> None:
        """Sale needs a positive price. Giveaway must not have one."""
        kind = listing.get("kind")
        price = listing.get("price_amount")
        original = listing.get("original_price_amount")
        if kind == "giveaway" and (price is not None or listing.get("negotiable")):
            raise ValidationException(
                message_key="marketplace.errors.giveaway_price",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if kind == "sale" and price is not None and Decimal(str(price)) <= 0:
            raise ValidationException(
                message_key="marketplace.errors.price_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        asking = None if price is None else Decimal(str(price))
        listed = None if original is None else Decimal(str(original))
        if asking is not None and listed is not None and listed <= asking:
            raise ValidationException(
                message_key="marketplace.errors.original_price",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def _visible_listing(self, *, listing_id: str, unit: dict[str, Any]) -> dict[str, Any]:
        """Listing the viewer may open: own society or a nearby one."""
        listing = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        if not listing:
            raise NotFoundException(
                message_key="marketplace.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        nearby = await self._nearby_project_ids(unit)
        allowed = {unit["project_id"], *nearby}
        if listing["project_id"] not in allowed and listing["status"] == "live":
            raise NotFoundException(
                message_key="marketplace.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return listing

    async def _nearby_project_ids(self, unit: dict[str, Any]) -> list[str]:
        """Other societies within the nearby radius."""
        origin_lat = unit.get("project_latitude")
        origin_lng = unit.get("project_longitude")
        if origin_lat is None or origin_lng is None:
            return []
        projects = await self.repo.list_active_project_coords(organization_id=self._org())
        nearby: list[str] = []
        for project in projects:
            if project["id"] == unit["project_id"]:
                continue
            distance = haversine_km(
                origin_lat, origin_lng, project["latitude"], project["longitude"]
            )
            if distance is not None and distance <= MARKETPLACE_NEARBY_RADIUS_KM:
                nearby.append(project["id"])
        return nearby

    async def _card(
        self, row: dict[str, Any], *, viewer_contact_id: str, viewer_project_id: str | None
    ) -> dict[str, Any]:
        """Card fields shared by the home strip, browse, and saved list."""
        media = listing_media(row)
        saved = await self.repo.is_saved(
            organization_id=self._org(),
            contact_id=viewer_contact_id,
            listing_id=row["id"],
        )
        category_name, subtype_name = MarketplaceCatalogService.labels(
            str(row["category"]), row.get("subtype")
        )
        return {
            "id": row["id"],
            "title": row.get("title"),
            "category": row["category"],
            "category_name": category_name,
            "subtype": row.get("subtype"),
            "subtype_name": subtype_name,
            "kind": row["kind"],
            "price_amount": _money(row.get("price_amount")),
            "original_price_amount": _money(row.get("original_price_amount")),
            "cover_path": cover_path(media),
            "is_new_today": _is_new_today(row.get("published_at")),
            "tower_name": row.get("tower_name"),
            "project_name": row.get("project_name"),
            "flat": visible_flat(
                listing=row,
                viewer_contact_id=viewer_contact_id,
                viewer_project_id=viewer_project_id,
            ),
            "saved": saved,
        }

    async def _detail(
        self, row: dict[str, Any], *, viewer_contact_id: str, viewer_project_id: str | None
    ) -> dict[str, Any]:
        """Full listing, including seller-only fields for the owner."""
        card = await self._card(
            row, viewer_contact_id=viewer_contact_id, viewer_project_id=viewer_project_id
        )
        others = 0
        if row["status"] == "live":
            others = await self.repo.count_other_live(
                organization_id=self._org(),
                seller_contact_id=row["seller_contact_id"],
                project_id=row["project_id"],
                exclude_listing_id=row["id"],
            )
        year = row.get("purchase_year")
        age_years = None
        if year is not None:
            age_years = max(0, _now().year - int(year))
        card.update(
            {
                "status": row["status"],
                "description": row.get("description"),
                "purchase_year": year,
                "age_years": age_years,
                "negotiable": row.get("negotiable"),
                "brand": row.get("brand"),
                "condition": row.get("condition"),
                "product_url": row.get("product_url"),
                "original_bill_available": row.get("original_bill_available"),
                "show_flat_number": row.get("show_flat_number"),
                "published_at": row.get("published_at"),
                "expires_at": row.get("expires_at"),
                "seller_name": public_name(
                    row.get("seller_first_name"), row.get("seller_last_name")
                ),
                "seller_role": row.get("seller_role"),
                "member_since_year": _year(row.get("seller_role_started_at")),
                "viewer_is_seller": str(row["seller_contact_id"]) == str(viewer_contact_id),
                "collection_latitude": _float_or_none(row.get("tower_latitude")),
                "collection_longitude": _float_or_none(row.get("tower_longitude")),
                "media": listing_media(row),
                "more_from_seller_count": others,
                "removal_note": row.get("removal_note")
                if str(row["seller_contact_id"]) == str(viewer_contact_id)
                else None,
            }
        )
        return card

    def _mine_card(self, row: dict[str, Any], media_count: int) -> dict[str, Any]:
        """One row on the seller's own list."""
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
            "days_left": days_left,
            "missing": publish_gaps(row, media_count) if row["status"] == "draft" else [],
            "published_at": row.get("published_at"),
            "sold_at": row.get("sold_at"),
            "removal_note": row.get("removal_note"),
        }

    @staticmethod
    def _check_year(year: int | None) -> None:
        """Purchase year must fall between 1980 and this year."""
        if year is None:
            return
        current = _now().year
        if year < PURCHASE_YEAR_MIN or year > current:
            raise ValidationException(
                message_key="marketplace.errors.invalid_purchase_year",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    @staticmethod
    def _check_url(url: str | None) -> None:
        """Product link, when set, must be http or https."""
        if not url:
            return
        lowered = url.strip().lower()
        if not (lowered.startswith("http://") or lowered.startswith("https://")):
            raise ValidationException(
                message_key="marketplace.errors.invalid_product_url",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )


def _as_utc(value: datetime) -> datetime:
    """Treat a naive timestamp as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_new_today(published_at: datetime | None) -> bool:
    """True when the post went live today in Asia/Kolkata."""
    if published_at is None:
        return False
    published = _as_utc(published_at).astimezone(_KOLKATA).date()
    return published == datetime.now(_KOLKATA).date()


def _year(value: datetime | None) -> int | None:
    """Calendar year of a timestamp."""
    if value is None:
        return None
    return _as_utc(value).year


def _float_or_none(value: Any) -> float | None:
    """Coordinate as float, or None."""
    if value is None:
        return None
    return float(value)
