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
    AddListingMediaRequest,
    CreateListingRequest,
    CreateReportRequest,
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
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

LIVE_WINDOW = timedelta(days=30)
SOLD_HISTORY = timedelta(days=365)
MAX_MEDIA = 8
MIN_PUBLISH_MEDIA = 2
PURCHASE_YEAR_MIN = 1980
_KOLKATA = ZoneInfo("Asia/Kolkata")
_EDITABLE = frozenset({"draft", "live", "removed"})
_MEDIA_TYPES = frozenset({"jpeg", "png"})


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
    viewer_project_id: str,
) -> str | None:
    """The only place a flat number is shown or hidden."""
    label = flat_label(listing.get("unit_label"), listing.get("unit_code"))
    if str(listing.get("seller_contact_id")) == str(viewer_contact_id):
        return label
    same_society = str(listing.get("project_id")) == str(viewer_project_id)
    if same_society and listing.get("show_flat_number"):
        return label
    return None


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
    """Listings, media, saves, reports, and committee takedown."""

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

    async def _reload_if_still_live(self, *, contact_id: str, listing_id: str) -> dict[str, Any]:
        """Expire a finished window, then require the row to still be live."""
        await self.repo.expire_due(organization_id=self._org())
        listing = await self._listing_for_seller(contact_id=contact_id, listing_id=listing_id)
        if listing["status"] != "live" or _past_expiry(listing.get("expires_at")):
            raise ValidationException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return listing

    async def _locked_seller_listing(self, *, contact_id: str, listing_id: str) -> dict[str, Any]:
        """Lock the seller's row. Call inside a transaction. Expire it if the window is over."""
        listing = await self.repo.lock_listing(organization_id=self._org(), listing_id=listing_id)
        if not listing or str(listing["seller_contact_id"]) != str(contact_id):
            raise NotFoundException(
                message_key="marketplace.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if listing["status"] == "live" and _past_expiry(listing.get("expires_at")):
            await self.repo.update_listing(
                organization_id=self._org(),
                listing_id=listing_id,
                fields={"status": "expired"},
            )
            raise ValidationException(
                message_key="marketplace.errors.not_editable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if listing["status"] not in {"draft", "live"}:
            raise ValidationException(
                message_key="marketplace.errors.not_editable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return listing

    async def get_catalog(self) -> dict[str, Any]:
        """Static categories."""
        return MarketplaceCatalogService.get_catalog()

    async def home(self, *, contact_id: str, unit_id: str) -> dict[str, Any]:
        """Society header, latest draft, and recently listed in this society."""
        await self.repo.expire_due(organization_id=self._org())
        unit = await self._require_unit(contact_id=contact_id, unit_id=unit_id)
        org_id = self._org()
        draft = await self.repo.latest_draft(organization_id=org_id, seller_contact_id=contact_id)
        draft_payload = None
        if draft:
            media_count = await self.repo.count_media(
                organization_id=org_id, listing_id=draft["id"]
            )
            draft_payload = {
                "id": draft["id"],
                "title": draft.get("title"),
                "missing": publish_gaps(draft, media_count),
            }
        recent_rows = await self.repo.list_recent(
            organization_id=org_id, project_id=unit["project_id"]
        )
        recent = await self._cards(
            recent_rows,
            viewer_contact_id=contact_id,
            viewer_project_id=unit["project_id"],
        )
        return {
            "project_name": unit["project_name"],
            "tower_count": await self.repo.count_towers(
                organization_id=org_id, project_id=unit["project_id"]
            ),
            "draft": draft_payload,
            "recent": recent,
            "live_count": await self.repo.count_live(
                organization_id=org_id, project_id=unit["project_id"]
            ),
        }

    async def pickup_units(self, *, contact_id: str, unit_id: str) -> list[dict[str, Any]]:
        """Flats the caller may pick up from. unit_id confirms the caller is a resident."""
        await self._require_unit(contact_id=contact_id, unit_id=unit_id)
        rows = await self.repo.list_pickup_units(organization_id=self._org(), contact_id=contact_id)
        return [
            {
                "unit_id": row["unit_id"],
                "label": flat_label(row.get("unit_label"), row.get("unit_code")),
                "project_name": row["project_name"],
            }
            for row in rows
        ]

    async def create_listing(
        self, *, contact_id: str, body: CreateListingRequest
    ) -> dict[str, Any]:
        """Open a draft in the chosen category."""
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
        )
        listing = await self.repo.get_listing(organization_id=self._org(), listing_id=created["id"])
        assert listing
        return await self._detail(
            listing, viewer_contact_id=contact_id, viewer_project_id=unit["project_id"]
        )

    async def update_listing(
        self, *, contact_id: str, listing_id: str, body: UpdateListingRequest
    ) -> dict[str, Any]:
        """Edit a draft, a live post, or a committee-removed post."""
        listing = await self._listing_for_seller(contact_id=contact_id, listing_id=listing_id)
        if listing["status"] not in _EDITABLE:
            raise ValidationException(
                message_key="marketplace.errors.not_editable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        unit = await self._require_poster(
            contact_id=contact_id, unit_id=body.unit_id or listing["unit_id"]
        )
        if listing["status"] == "live":
            listing = await self._reload_if_still_live(contact_id=contact_id, listing_id=listing_id)
        fields = self._patch_fields(listing, body, unit)
        if listing["status"] == "removed":
            fields["status"] = "draft"
        merged = {**listing, **fields}
        if listing["status"] == "live":
            media_count = await self.repo.count_media(
                organization_id=self._org(), listing_id=listing_id
            )
            gaps = publish_gaps(merged, media_count)
            self._raise_price_rules(merged)
            if gaps:
                raise ValidationException(
                    message_key="marketplace.errors.live_incomplete",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"missing": gaps},
                )
        saved = await self.repo.update_listing(
            organization_id=self._org(),
            listing_id=listing_id,
            fields=fields,
            only_live=listing["status"] == "live",
        )
        if listing["status"] == "live" and not saved:
            raise ConflictException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.CONFLICT,
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
        if body.unit_id:
            fields["unit_id"] = unit["id"]
            fields["project_id"] = unit["project_id"]
            fields["tower_id"] = unit.get("tower_id")
        provided = body.model_fields_set
        self._copy_text_fields(fields, body, provided)
        self._apply_kind_and_price(fields, listing, body, provided)
        self._apply_clears(fields, body)
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

    @staticmethod
    def _apply_clears(fields: dict[str, Any], body: UpdateListingRequest) -> None:
        """Explicit clears win over a value sent in the same request."""
        if body.clear_price:
            fields["price_amount"] = None
        if body.clear_original_price:
            fields["original_price_amount"] = None
        if body.clear_brand:
            fields["brand"] = None
        if body.clear_product_url:
            fields["product_url"] = None

    async def add_media(
        self, *, contact_id: str, listing_id: str, body: AddListingMediaRequest
    ) -> dict[str, Any]:
        """Attach a presigned upload. Live posts stay live."""
        await self._require_poster(contact_id=contact_id, unit_id=body.unit_id)
        file_type = body.file_type.strip().lower()
        if file_type not in _MEDIA_TYPES:
            raise ValidationException(
                message_key="marketplace.errors.invalid_file_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        async with self.db_connection.transaction():
            await self._locked_seller_listing(contact_id=contact_id, listing_id=listing_id)
            count = await self.repo.count_media(organization_id=self._org(), listing_id=listing_id)
            if count >= MAX_MEDIA:
                raise ValidationException(
                    message_key="marketplace.errors.too_many_files",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            if body.is_cover:
                await self.repo.clear_cover(organization_id=self._org(), listing_id=listing_id)
            await self.repo.insert_media(
                organization_id=self._org(),
                listing_id=listing_id,
                path=body.path.strip(),
                file_type=file_type,
                size_bytes=body.size_bytes,
                original_name=body.original_name,
                sort_order=body.sort_order,
                is_cover=body.is_cover or count == 0,
            )
        return {"id": listing_id}

    async def delete_media(
        self, *, contact_id: str, listing_id: str, media_id: str, unit_id: str
    ) -> None:
        """Remove one file. A live post must keep at least two."""
        await self._require_poster(contact_id=contact_id, unit_id=unit_id)
        async with self.db_connection.transaction():
            listing = await self._locked_seller_listing(
                contact_id=contact_id, listing_id=listing_id
            )
            if listing["status"] == "live":
                count = await self.repo.count_media(
                    organization_id=self._org(), listing_id=listing_id
                )
                if count <= MIN_PUBLISH_MEDIA:
                    raise ValidationException(
                        message_key="marketplace.errors.live_incomplete",
                        custom_code=CustomStatusCode.VALIDATION_ERROR,
                    )
            deleted = await self.repo.delete_media(
                organization_id=self._org(), listing_id=listing_id, media_id=media_id
            )
        if not deleted:
            raise NotFoundException(
                message_key="marketplace.errors.media_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

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
        self, *, contact_id: str, listing_id: str, unit_id: str
    ) -> dict[str, Any]:
        """Take a live post off the board and keep it as a draft."""
        await self._require_poster(contact_id=contact_id, unit_id=unit_id)
        await self._reload_if_still_live(contact_id=contact_id, listing_id=listing_id)
        saved = await self.repo.update_listing(
            organization_id=self._org(),
            listing_id=listing_id,
            fields={"status": "draft"},
            only_live=True,
        )
        if not saved:
            raise ConflictException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.CONFLICT,
            )
        updated = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        assert updated
        return {
            "id": listing_id,
            "status": updated["status"],
            "published_at": updated["published_at"],
        }

    async def restore_listing(
        self, *, contact_id: str, listing_id: str, unit_id: str
    ) -> dict[str, Any]:
        """Put a seller-removed draft back on the board after an optional edit."""
        await self._require_poster(contact_id=contact_id, unit_id=unit_id)
        listing = await self._listing_for_seller(contact_id=contact_id, listing_id=listing_id)
        if listing["status"] != "draft" or listing.get("published_at") is None:
            raise ValidationException(
                message_key="marketplace.errors.not_restorable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self._assert_publishable(listing)
        now = _now()
        expires_at = listing.get("expires_at")
        fields: dict[str, Any] = {"status": "live"}
        if expires_at is None or _as_utc(expires_at) <= now:
            fields["published_at"] = now
            fields["expires_at"] = now + LIVE_WINDOW
        await self.repo.update_listing(
            organization_id=self._org(), listing_id=listing_id, fields=fields
        )
        updated = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        assert updated
        return {
            "id": listing_id,
            "status": updated["status"],
            "published_at": updated["published_at"],
            "expires_at": updated["expires_at"],
        }

    async def renew(self, *, contact_id: str, listing_id: str, unit_id: str) -> dict[str, Any]:
        """Add 30 days, keeping any time still left."""
        await self._require_poster(contact_id=contact_id, unit_id=unit_id)
        listing = await self._reload_if_still_live(contact_id=contact_id, listing_id=listing_id)
        now = _now()
        current = _as_utc(listing["expires_at"]) if listing.get("expires_at") else now
        expires_at = max(current, now) + LIVE_WINDOW
        saved = await self.repo.update_listing(
            organization_id=self._org(),
            listing_id=listing_id,
            fields={"expires_at": expires_at, "renewal_count": int(listing["renewal_count"]) + 1},
            only_live=True,
        )
        if not saved:
            raise ConflictException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.CONFLICT,
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
        listing = await self._reload_if_still_live(contact_id=contact_id, listing_id=listing_id)
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
        sold = await self.repo.sell_live_listing(
            organization_id=self._org(),
            listing_id=listing_id,
            buyer_contact_id=body.buyer_contact_id,
            sold_at=_now(),
            seller_contact_id=contact_id,
            rating=body.rating.value if body.rating is not None else None,
        )
        if not sold:
            raise ConflictException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.CONFLICT,
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
        unit = await self._require_unit(contact_id=contact_id, unit_id=unit_id)
        await self.repo.expire_due(organization_id=self._org())
        listing = await self._visible_listing(listing_id=listing_id, unit=unit)
        if listing["status"] != "live":
            raise ValidationException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
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

    async def list_saved(self, *, contact_id: str, unit_id: str) -> list[dict[str, Any]]:
        """Live bookmarks."""
        unit = await self._require_unit(contact_id=contact_id, unit_id=unit_id)
        await self.repo.expire_due(organization_id=self._org())
        rows = await self.repo.list_saved(organization_id=self._org(), contact_id=contact_id)
        return await self._cards(
            rows, viewer_contact_id=contact_id, viewer_project_id=unit["project_id"]
        )

    async def get_listing(
        self, *, contact_id: str, listing_id: str, unit_id: str
    ) -> dict[str, Any]:
        """Listing detail. Opening it can expire a post that has run out of days."""
        unit = await self._require_unit(contact_id=contact_id, unit_id=unit_id)
        await self.repo.expire_due(organization_id=self._org())
        listing = await self.repo.get_listing(organization_id=self._org(), listing_id=listing_id)
        if not listing:
            raise NotFoundException(
                message_key="marketplace.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        seller = str(listing["seller_contact_id"]) == str(contact_id)
        if not seller:
            listing = await self._visible_listing(listing_id=listing_id, unit=unit)
            if listing["status"] != "live":
                raise NotFoundException(
                    message_key="marketplace.errors.not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
        return await self._detail(
            listing, viewer_contact_id=contact_id, viewer_project_id=unit["project_id"]
        )

    async def list_listings(
        self,
        *,
        contact_id: str,
        unit_id: str,
        category: str | None,
        subtype: str | None,
        query: str | None,
        sort: str,
        price_band: str | None,
        conditions: list[str],
        where: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Browse live listings. Default scope is this society plus nearby."""
        unit = await self._require_unit(contact_id=contact_id, unit_id=unit_id)
        await self.repo.expire_due(organization_id=self._org())
        nearby = await self._nearby_project_ids(unit)
        tower_id = None
        project_ids: list[str]
        if where == "my_tower":
            tower_id = unit.get("tower_id")
            project_ids = []
            if not tower_id:
                return {"items": [], "total": 0, "nearby_project_count": len(nearby)}
        elif where == "my_society":
            project_ids = [unit["project_id"]]
        elif where == "nearby":
            project_ids = nearby
        else:
            project_ids = [unit["project_id"], *nearby]
        rows, total = await self.repo.list_listings(
            organization_id=self._org(),
            project_ids=project_ids,
            tower_id=tower_id,
            category=category,
            subtype=subtype,
            query=query,
            conditions=conditions,
            price_band=price_band,
            sort=sort,
            viewer_lat=_float_or_none(unit.get("tower_latitude")),
            viewer_lng=_float_or_none(unit.get("tower_longitude")),
            page=page,
            page_size=page_size,
        )
        items = await self._cards(
            rows, viewer_contact_id=contact_id, viewer_project_id=unit["project_id"]
        )
        return {"items": items, "total": total, "nearby_project_count": len(nearby)}

    async def my_listings(
        self, *, contact_id: str, unit_id: str, status: str, page: int, page_size: int
    ) -> dict[str, Any]:
        """One page of the seller's own posts."""
        await self._require_unit(contact_id=contact_id, unit_id=unit_id)
        await self.repo.expire_due(organization_id=self._org())
        rows, total = await self.repo.list_mine(
            organization_id=self._org(),
            seller_contact_id=contact_id,
            status=status,
            page=page,
            page_size=page_size,
        )
        counts = await self.repo.media_counts(
            organization_id=self._org(), listing_ids=[row["id"] for row in rows]
        )
        items = [self._mine_card(row, counts.get(row["id"], 0)) for row in rows]
        live_count = await self.repo.count_seller_live(
            organization_id=self._org(), seller_contact_id=contact_id
        )
        earned = await self.repo.earned_amount(
            organization_id=self._org(), seller_contact_id=contact_id
        )
        return {
            "live_count": live_count,
            "earned_amount": _money(earned),
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def create_report(
        self, *, contact_id: str, listing_id: str, body: CreateReportRequest
    ) -> dict[str, Any]:
        """Send a report to the committee. The listing stays live."""
        unit = await self._require_unit(contact_id=contact_id, unit_id=body.unit_id)
        listing = await self._visible_listing(listing_id=listing_id, unit=unit)
        if str(listing["seller_contact_id"]) == str(contact_id):
            raise ValidationException(
                message_key="marketplace.errors.cannot_report_own",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if listing["status"] != "live":
            raise ValidationException(
                message_key="marketplace.errors.not_live",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        try:
            created = await self.repo.insert_report(
                organization_id=self._org(),
                project_id=listing["project_id"],
                listing_id=listing_id,
                reporter_contact_id=contact_id,
                reason=body.reason.value,
            )
        except asyncpg.UniqueViolationError as exc:
            raise ConflictException(
                message_key="marketplace.errors.report_exists",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return {"id": created["id"], "status": "open"}

    async def list_reports(self, *, status: str | None) -> list[dict[str, Any]]:
        """Organization reports. The route drops societies the staff member cannot open."""
        return await self.repo.list_reports(organization_id=self._org(), status=status)

    async def get_report(self, *, report_id: str) -> dict[str, Any]:
        """One report, used to check staff access on its society."""
        report = await self.repo.get_report(organization_id=self._org(), report_id=report_id)
        if not report:
            raise NotFoundException(
                message_key="marketplace.errors.report_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return report

    async def uphold_report(
        self, *, report_id: str, removal_note: str, reviewer_user_id: str
    ) -> dict[str, Any]:
        """Take the listing down and store the committee note."""
        report = await self.get_report(report_id=report_id)
        if report["status"] != "open":
            raise ValidationException(
                message_key="marketplace.errors.report_closed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        now = _now()
        await self.repo.update_listing(
            organization_id=self._org(),
            listing_id=report["listing_id"],
            fields={
                "status": "removed",
                "removed_at": now,
                "removal_note": removal_note.strip(),
                "removed_by_user_id": reviewer_user_id,
            },
        )
        await self.repo.review_report(
            organization_id=self._org(),
            report_id=report_id,
            status="upheld",
            reviewed_by_user_id=reviewer_user_id,
            reviewed_at=now,
        )
        return {"id": report_id, "listing_id": report["listing_id"], "status": "upheld"}

    async def dismiss_report(self, *, report_id: str, reviewer_user_id: str) -> dict[str, Any]:
        """Leave the listing live."""
        report = await self.get_report(report_id=report_id)
        if report["status"] != "open":
            raise ValidationException(
                message_key="marketplace.errors.report_closed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        await self.repo.review_report(
            organization_id=self._org(),
            report_id=report_id,
            status="dismissed",
            reviewed_by_user_id=reviewer_user_id,
            reviewed_at=_now(),
        )
        return {"id": report_id, "status": "dismissed"}

    async def feedback_patterns(self, *, project_ids: list[str]) -> list[dict[str, Any]]:
        """Sellers with three or more had_trouble ratings in societies the caller can view."""
        return await self.repo.feedback_patterns(
            organization_id=self._org(), project_ids=project_ids
        )

    async def _assert_publishable(self, listing: dict[str, Any]) -> None:
        """Raise when a listing is not ready to go live."""
        await self.repo.ensure_cover(organization_id=self._org(), listing_id=listing["id"])
        media_count = await self.repo.count_media(
            organization_id=self._org(), listing_id=listing["id"]
        )
        gaps = publish_gaps(listing, media_count)
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
        allowed = {str(unit["project_id"]), *(str(item) for item in nearby)}
        if str(listing["project_id"]) not in allowed:
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

    async def _cards(
        self,
        rows: list[dict[str, Any]],
        *,
        viewer_contact_id: str,
        viewer_project_id: str,
    ) -> list[dict[str, Any]]:
        """Cards for a page, with covers and bookmarks loaded in two queries."""
        listing_ids = [row["id"] for row in rows]
        covers = await self.repo.cover_paths(organization_id=self._org(), listing_ids=listing_ids)
        saved_ids = await self.repo.saved_listing_ids(
            organization_id=self._org(),
            contact_id=viewer_contact_id,
            listing_ids=listing_ids,
        )
        return [
            self._card_body(
                row,
                cover=covers.get(row["id"]),
                saved=row["id"] in saved_ids,
                viewer_contact_id=viewer_contact_id,
                viewer_project_id=viewer_project_id,
            )
            for row in rows
        ]

    async def _card(
        self, row: dict[str, Any], *, viewer_contact_id: str, viewer_project_id: str
    ) -> dict[str, Any]:
        """Card fields for one listing."""
        media = await self.repo.list_media(organization_id=self._org(), listing_id=row["id"])
        cover = next((item["path"] for item in media if item["is_cover"]), None)
        if cover is None and media:
            cover = media[0]["path"]
        saved = await self.repo.is_saved(
            organization_id=self._org(),
            contact_id=viewer_contact_id,
            listing_id=row["id"],
        )
        return self._card_body(
            row,
            cover=cover,
            saved=saved,
            viewer_contact_id=viewer_contact_id,
            viewer_project_id=viewer_project_id,
        )

    @staticmethod
    def _card_body(
        row: dict[str, Any],
        *,
        cover: str | None,
        saved: bool,
        viewer_contact_id: str,
        viewer_project_id: str,
    ) -> dict[str, Any]:
        """Card fields from a listing row and already loaded cover and bookmark."""
        return {
            "id": row["id"],
            "title": row.get("title"),
            "category": row["category"],
            "kind": row["kind"],
            "price_amount": _money(row.get("price_amount")),
            "original_price_amount": _money(row.get("original_price_amount")),
            "cover_path": cover,
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
        self, row: dict[str, Any], *, viewer_contact_id: str, viewer_project_id: str
    ) -> dict[str, Any]:
        """Full listing, including seller-only fields for the owner."""
        card = await self._card(
            row, viewer_contact_id=viewer_contact_id, viewer_project_id=viewer_project_id
        )
        media = await self.repo.list_media(organization_id=self._org(), listing_id=row["id"])
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
                "subtype": row.get("subtype"),
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
                "media": media,
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
            "can_restore": row["status"] == "draft" and row.get("published_at") is not None,
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


def _past_expiry(value: datetime | None) -> bool:
    """True when a live window has already ended."""
    if value is None:
        return False
    return _as_utc(value) <= _now()


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
