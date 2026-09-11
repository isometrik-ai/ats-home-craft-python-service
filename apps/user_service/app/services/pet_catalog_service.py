"""Pet catalog loaded from a static JSON file (types and breeds)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

_CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "pet_catalog.json"


@lru_cache(maxsize=1)
def _load_catalog_raw() -> dict[str, Any]:
    """Load and cache the pet catalog JSON."""
    with _CATALOG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _matches_search(name: str, search: str) -> bool:
    """Return True when name contains the search term (case-insensitive)."""
    return search.lower() in name.lower()


def _filter_breeds(breeds: list[dict[str, Any]], search: str | None) -> list[dict[str, Any]]:
    """Filter breed rows by optional search term."""
    if not search:
        return breeds
    return [breed for breed in breeds if _matches_search(str(breed["name"]), search)]


class PetCatalogService:
    """Read-only pet picker options from static JSON."""

    @staticmethod
    def get_catalog(
        *,
        pet_type_id: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Return pet types with breeds; optional filter by type id or search."""
        raw = _load_catalog_raw()
        pet_types = list(raw.get("pet_types") or [])

        if pet_type_id:
            pet_types = [pet_type for pet_type in pet_types if pet_type.get("id") == pet_type_id]
            if not pet_types:
                raise NotFoundException(
                    message_key="pets.errors.invalid_pet_type",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        if search:
            search = search.strip()
            if search:
                filtered_types: list[dict[str, Any]] = []
                for pet_type in pet_types:
                    breeds = _filter_breeds(list(pet_type.get("breeds") or []), search)
                    if _matches_search(str(pet_type["name"]), search) or breeds:
                        filtered_types.append({**pet_type, "breeds": breeds})
                pet_types = filtered_types
        else:
            pet_types = [
                {**pet_type, "breeds": list(pet_type.get("breeds") or [])} for pet_type in pet_types
            ]

        return {"pet_types": pet_types}

    @staticmethod
    def resolve_type_and_breed(*, pet_type: str, breed: str) -> tuple[str, str]:
        """Validate and return canonical catalog names for type and breed."""
        raw = _load_catalog_raw()
        pet_types = list(raw.get("pet_types") or [])
        type_name = pet_type.strip()
        breed_name = breed.strip()
        if not type_name or not breed_name:
            raise ValidationException(
                message_key="pets.errors.invalid_pet_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        matched_type: dict[str, Any] | None = None
        for entry in pet_types:
            if str(entry.get("name", "")).lower() == type_name.lower():
                matched_type = entry
                break
        if not matched_type:
            raise ValidationException(
                message_key="pets.errors.invalid_pet_type",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        matched_breed: dict[str, Any] | None = None
        for entry in matched_type.get("breeds") or []:
            if str(entry.get("name", "")).lower() == breed_name.lower():
                matched_breed = entry
                break
        if not matched_breed:
            raise ValidationException(
                message_key="pets.errors.invalid_breed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        return str(matched_type["name"]), str(matched_breed["name"])

    @staticmethod
    def clear_cache() -> None:
        """Clear the in-memory catalog cache (for tests)."""
        _load_catalog_raw.cache_clear()
