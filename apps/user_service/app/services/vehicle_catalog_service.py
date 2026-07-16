"""Vehicle catalog loaded from a static JSON file (brands, models, colors)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "vehicle_catalog.json"


@lru_cache(maxsize=1)
def _load_catalog_raw() -> dict[str, Any]:
    """Load and cache the vehicle catalog JSON."""
    with _CATALOG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _matches_search(name: str, search: str) -> bool:
    """Return True when name contains the search term (case-insensitive)."""
    return search.lower() in name.lower()


def _filter_models(models: list[dict[str, Any]], search: str | None) -> list[dict[str, Any]]:
    """Filter model rows by optional search term."""
    if not search:
        return models
    return [model for model in models if _matches_search(str(model["name"]), search)]


class VehicleCatalogService:
    """Read-only vehicle picker options from static JSON."""

    @staticmethod
    def get_catalog(
        *,
        brand_id: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Return brands (with models) and colors, with optional filters."""
        raw = _load_catalog_raw()
        brands = list(raw.get("brands") or [])
        colors = list(raw.get("colors") or [])

        if brand_id:
            brands = [brand for brand in brands if brand.get("id") == brand_id]
            if not brands:
                raise NotFoundException(
                    message_key="contact_onboarding.errors.vehicle_brand_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        if search:
            search = search.strip()
            if search:
                filtered_brands: list[dict[str, Any]] = []
                for brand in brands:
                    models = _filter_models(list(brand.get("models") or []), search)
                    if _matches_search(str(brand["name"]), search) or models:
                        filtered_brands.append({**brand, "models": models})
                brands = filtered_brands
                colors = [color for color in colors if _matches_search(str(color["name"]), search)]
        else:
            brands = [{**brand, "models": list(brand.get("models") or [])} for brand in brands]

        return {"brands": brands, "colors": colors}

    @staticmethod
    def clear_cache() -> None:
        """Clear the in-memory catalog cache (for tests)."""
        _load_catalog_raw.cache_clear()
