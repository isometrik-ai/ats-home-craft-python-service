"""Read-only marketplace categories from static JSON."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "marketplace_catalog.json"


@lru_cache(maxsize=1)
def _load_catalog_raw() -> dict[str, Any]:
    """Load and cache the marketplace catalog JSON."""
    with _CATALOG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


class MarketplaceCatalogService:
    """Category and subtype picker. Names are stored on the listing, not ids."""

    @staticmethod
    def get_catalog() -> dict[str, Any]:
        """Return the category list."""
        raw = _load_catalog_raw()
        return {"categories": list(raw.get("categories") or [])}

    @staticmethod
    def resolve(category: str, subtype: str | None) -> tuple[str, str | None]:
        """Return canonical category and subtype names, or raise."""
        raw = _load_catalog_raw()
        wanted = category.strip()
        match = next(
            (
                item
                for item in raw.get("categories") or []
                if str(item.get("name", "")).lower() == wanted.lower()
            ),
            None,
        )
        if match is None:
            raise ValidationException(
                message_key="marketplace.errors.invalid_category",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        subtypes = list(match.get("subtypes") or [])
        canonical_category = str(match["name"])
        if not subtypes:
            if subtype and subtype.strip():
                raise ValidationException(
                    message_key="marketplace.errors.invalid_subtype",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return canonical_category, None
        if not subtype or not subtype.strip():
            raise ValidationException(
                message_key="marketplace.errors.subtype_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        wanted_subtype = subtype.strip()
        subtype_match = next(
            (
                item
                for item in subtypes
                if str(item.get("name", "")).lower() == wanted_subtype.lower()
            ),
            None,
        )
        if subtype_match is None:
            raise ValidationException(
                message_key="marketplace.errors.invalid_subtype",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return canonical_category, str(subtype_match["name"])

    @staticmethod
    def category_requires_subtype(category: str) -> bool:
        """True when the catalog category has at least one subtype."""
        raw = _load_catalog_raw()
        match = next(
            (
                item
                for item in raw.get("categories") or []
                if str(item.get("name", "")).lower() == category.strip().lower()
            ),
            None,
        )
        if match is None:
            return False
        return bool(match.get("subtypes"))
