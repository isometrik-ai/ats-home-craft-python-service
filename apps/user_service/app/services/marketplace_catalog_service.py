"""Read-only marketplace categories from static JSON."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "marketplace_catalog.json"
_REPORT_REASONS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "marketplace_report_reasons.json"
)


@lru_cache(maxsize=1)
def _load_catalog_raw() -> dict[str, Any]:
    """Load and cache the marketplace catalog JSON."""
    with _CATALOG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _load_report_reasons_raw() -> dict[str, Any]:
    """Load and cache report reason copy for the resident report sheet."""
    with _REPORT_REASONS_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _categories() -> list[dict[str, Any]]:
    """Catalog category objects."""
    return list(_load_catalog_raw().get("categories") or [])


def _find_category(slug: str) -> dict[str, Any] | None:
    """Match a category by catalog slug."""
    wanted = slug.strip().lower()
    if not wanted:
        return None
    return next(
        (item for item in _categories() if str(item.get("slug", "")).lower() == wanted),
        None,
    )


def _find_subtype(category: dict[str, Any], slug: str) -> dict[str, Any] | None:
    """Match a subtype by catalog slug inside one category."""
    wanted = slug.strip().lower()
    if not wanted:
        return None
    return next(
        (
            item
            for item in category.get("subtypes") or []
            if str(item.get("slug", "")).lower() == wanted
        ),
        None,
    )


class MarketplaceCatalogService:
    """Category and subtype picker. Listings store catalog slugs, not display names."""

    @staticmethod
    def get_catalog() -> dict[str, Any]:
        """Return the category list."""
        return {"categories": _categories()}

    @staticmethod
    def get_report_reasons() -> dict[str, Any]:
        """Return report sheet copy and reason options (slugs match marketplace_report_reason)."""
        raw = _load_report_reasons_raw()
        return {
            "sheet_title": raw.get("sheet_title"),
            "sheet_subtitle": raw.get("sheet_subtitle"),
            "reasons": list(raw.get("reasons") or []),
        }

    @staticmethod
    def resolve_report_reason(reason: str) -> str:
        """Return the canonical reason slug, or raise if it is not in the catalog."""
        wanted = reason.strip().lower()
        if not wanted:
            raise ValidationException(
                message_key="marketplace.errors.invalid_report_reason",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        for item in _load_report_reasons_raw().get("reasons") or []:
            if str(item.get("slug", "")).lower() == wanted:
                return str(item["slug"])
        raise ValidationException(
            message_key="marketplace.errors.invalid_report_reason",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )

    @staticmethod
    def resolve(category: str, subtype: str | None) -> tuple[str, str | None]:
        """Return canonical category and subtype slugs, or raise."""
        match = _find_category(category)
        if match is None:
            raise ValidationException(
                message_key="marketplace.errors.invalid_category",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        subtypes = list(match.get("subtypes") or [])
        category_slug = str(match["slug"])
        if not subtypes:
            if subtype and subtype.strip():
                raise ValidationException(
                    message_key="marketplace.errors.invalid_subtype",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return category_slug, None
        if not subtype or not subtype.strip():
            raise ValidationException(
                message_key="marketplace.errors.subtype_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        subtype_match = _find_subtype(match, subtype)
        if subtype_match is None:
            raise ValidationException(
                message_key="marketplace.errors.invalid_subtype",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return category_slug, str(subtype_match["slug"])

    @staticmethod
    def parse_filter(category: str | None, subtype: str | None) -> tuple[str | None, str | None]:
        """Normalize browse slugs, or raise if they are not in the catalog."""
        category_slug: str | None = None
        if category and category.strip():
            match = _find_category(category)
            if match is None:
                raise ValidationException(
                    message_key="marketplace.errors.invalid_category",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            category_slug = str(match["slug"])
        if not subtype or not subtype.strip():
            return category_slug, None
        subtype_match = None
        if category_slug:
            match = _find_category(category_slug)
            assert match is not None
            subtype_match = _find_subtype(match, subtype)
        else:
            for row in _categories():
                subtype_match = _find_subtype(row, subtype)
                if subtype_match is not None:
                    break
        if subtype_match is None:
            raise ValidationException(
                message_key="marketplace.errors.invalid_subtype",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return category_slug, str(subtype_match["slug"])

    @staticmethod
    def labels(category: str, subtype: str | None) -> tuple[str | None, str | None]:
        """Current catalog display names for stored slugs. Survives a rename."""
        match = _find_category(category)
        if match is None:
            return None, None
        subtype_name = None
        if subtype and subtype.strip():
            subtype_match = _find_subtype(match, subtype)
            subtype_name = str(subtype_match["name"]) if subtype_match else None
        return str(match["name"]), subtype_name

    @staticmethod
    def category_requires_subtype(category: str) -> bool:
        """True when the catalog category has at least one subtype."""
        match = _find_category(category)
        if match is None:
            return False
        return bool(match.get("subtypes"))
