"""Household pets business logic (ADR 0016)."""

from __future__ import annotations

from datetime import date
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.pets_repository import PetsRepository
from apps.user_service.app.schemas.enums.pets import PetEventType
from apps.user_service.app.schemas.pets import (
    CreatePetRequest,
    RemovePetRequest,
    UpdatePetRequest,
)
from apps.user_service.app.services.contact_onboarding_service import (
    ContactOnboardingService,
)
from apps.user_service.app.services.pet_catalog_service import PetCatalogService
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from apps.user_service.app.utils.user_utils import build_full_name
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class PetsService:
    """CRUD for unit-scoped household pets."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = PetsRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)

    async def get_catalog(
        self,
        *,
        pet_type_id: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Return static pet type/breed catalog."""
        return PetCatalogService.get_catalog(pet_type_id=pet_type_id, search=search)

    async def list_pets(
        self,
        *,
        contact_id: str,
        unit_id: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """List active pets on a unit the contact can access."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._validate_unit_for_contact(contact_id=contact_id, unit_id=unit_id)
        rows, total = await self.repo.list_for_unit(
            organization_id=org_id,
            unit_id=unit_id,
            page=page,
            page_size=page_size,
        )
        return [self._serialize_pet(row) for row in rows], total

    async def get_pet_detail(
        self,
        *,
        contact_id: str,
        pet_id: str,
        unit_id: str,
    ) -> dict[str, Any]:
        """Return one pet with unit, creator, and household members."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._validate_unit_for_contact(contact_id=contact_id, unit_id=unit_id)
        row = await self.repo.get_by_id(organization_id=org_id, pet_id=pet_id)
        if not row or str(row.get("unit_id")) != unit_id:
            raise NotFoundException(
                message_key="pets.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        onboarding = ContactOnboardingService(
            db_connection=self.db_connection,
            user_context=self.user_context,
            supabase_client=None,
        )
        household_members = await onboarding.list_household(
            contact_id=contact_id,
            unit_id=unit_id,
        )
        out = self._serialize_pet(row)
        out["household_members"] = household_members
        return out

    async def create_pet(
        self,
        *,
        contact_id: str,
        body: CreatePetRequest,
    ) -> dict[str, Any]:
        """Create a pet profile for a unit."""
        org_id = self.user_context.organization_id
        assert org_id
        unit = await self._validate_unit_for_contact(
            contact_id=contact_id,
            unit_id=body.unit_id,
        )
        if body.date_of_birth and body.date_of_birth > date.today():
            raise ValidationException(
                message_key="pets.errors.invalid_date_of_birth",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        pet_type, breed = PetCatalogService.resolve_type_and_breed(
            pet_type=body.pet_type,
            breed=body.breed,
        )
        row = await self.repo.create(
            organization_id=org_id,
            project_id=str(unit["project_id"]),
            unit_id=body.unit_id,
            created_by_contact_id=contact_id,
            name=body.name,
            pet_type=pet_type,
            breed=breed,
            gender=body.gender.value if body.gender else None,
            date_of_birth=body.date_of_birth,
            vaccination_status=body.vaccination_status.value,
            photo_paths=list(body.photo_paths),
            actor_contact_id=contact_id,
        )
        detail = await self.repo.get_by_id(organization_id=org_id, pet_id=str(row["id"]))
        return self._serialize_pet(detail or row)

    async def update_pet(
        self,
        *,
        contact_id: str,
        pet_id: str,
        unit_id: str,
        body: UpdatePetRequest,
    ) -> dict[str, Any]:
        """Patch an active pet profile."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._validate_unit_for_contact(contact_id=contact_id, unit_id=unit_id)
        existing = await self.repo.get_by_id(organization_id=org_id, pet_id=pet_id)
        if not existing or str(existing.get("unit_id")) != unit_id:
            raise NotFoundException(
                message_key="pets.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        update_data = self._build_pet_update_data(body=body, existing=existing)

        if not update_data:
            return self._serialize_pet(existing)

        photo_only = set(update_data.keys()) == {"photo_paths"}
        event_type = PetEventType.PHOTO_CHANGED.value if photo_only else PetEventType.UPDATED.value
        row = await self.repo.update(
            organization_id=org_id,
            pet_id=pet_id,
            update_data=update_data,
            event_type=event_type,
            actor_contact_id=contact_id,
        )
        if not row:
            raise NotFoundException(
                message_key="pets.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        detail = await self.repo.get_by_id(organization_id=org_id, pet_id=pet_id)
        return self._serialize_pet(detail or row)

    async def remove_pet(
        self,
        *,
        contact_id: str,
        pet_id: str,
        unit_id: str,
        body: RemovePetRequest,
    ) -> dict[str, Any]:
        """Soft-remove a pet with reason."""
        org_id = self.user_context.organization_id
        assert org_id
        await self._validate_unit_for_contact(contact_id=contact_id, unit_id=unit_id)
        existing = await self.repo.get_by_id(organization_id=org_id, pet_id=pet_id)
        if not existing or str(existing.get("unit_id")) != unit_id:
            raise NotFoundException(
                message_key="pets.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        row = await self.repo.soft_remove(
            organization_id=org_id,
            pet_id=pet_id,
            reason=body.reason.strip(),
            removed_by_contact_id=contact_id,
        )
        if not row:
            raise NotFoundException(
                message_key="pets.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._serialize_pet(row)

    def _build_pet_update_data(
        self,
        *,
        body: UpdatePetRequest,
        existing: dict[str, Any],
    ) -> dict[str, Any]:
        """Build patch fields from request body and existing row."""
        update_data: dict[str, Any] = {}
        if body.name is not None:
            update_data["name"] = body.name
        if body.pet_type is not None or body.breed is not None:
            pet_type = body.pet_type if body.pet_type is not None else str(existing["pet_type"])
            breed = body.breed if body.breed is not None else str(existing["breed"])
            resolved_type, resolved_breed = PetCatalogService.resolve_type_and_breed(
                pet_type=pet_type,
                breed=breed,
            )
            update_data["pet_type"] = resolved_type
            update_data["breed"] = resolved_breed
        if body.gender is not None:
            update_data["gender"] = body.gender.value
        if body.date_of_birth is not None:
            if body.date_of_birth > date.today():
                raise ValidationException(
                    message_key="pets.errors.invalid_date_of_birth",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            update_data["date_of_birth"] = body.date_of_birth
        if body.vaccination_status is not None:
            update_data["vaccination_status"] = body.vaccination_status.value
        if body.photo_paths is not None:
            update_data["photo_paths"] = list(body.photo_paths)
        return update_data

    async def _validate_unit_for_contact(
        self,
        *,
        contact_id: str,
        unit_id: str,
    ) -> dict[str, Any]:
        """Ensure the contact has an active unit link."""
        org_id = self.user_context.organization_id
        assert org_id
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
        unit = await self.contact_units_repo.get_unit_project(
            organization_id=org_id,
            unit_id=unit_id,
        )
        if not unit:
            raise NotFoundException(
                message_key="contact_onboarding.errors.unit_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return unit

    def _serialize_pet(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a pets row to API response shape."""
        out = dict(row)
        for key in (
            "id",
            "organization_id",
            "project_id",
            "unit_id",
            "created_by_contact_id",
            "removed_by_contact_id",
        ):
            if out.get(key) is not None:
                out[key] = str(out[key])

        photo_paths = out.get("photo_paths") or []
        out["photo_paths"] = list(photo_paths)
        out["primary_photo_path"] = photo_paths[0] if photo_paths else None

        dob = out.get("date_of_birth")
        out["date_of_birth"] = dob.isoformat() if hasattr(dob, "isoformat") else dob
        out["created_at"] = format_iso_datetime(out.get("created_at"))
        out["updated_at"] = format_iso_datetime(out.get("updated_at"))
        if out.get("deleted_at") is not None:
            out["deleted_at"] = format_iso_datetime(out.get("deleted_at"))

        creator_name = build_full_name(
            str(out.pop("creator_prefix", "") or ""),
            str(out.pop("creator_first_name", "") or ""),
            str(out.pop("creator_last_name", "") or ""),
        ).strip()
        out["created_by"] = {
            "contact_id": out.get("created_by_contact_id"),
            "display_name": creator_name or None,
            "profile_photo_url": out.pop("creator_profile_photo_url", None),
            "relationship": out.pop("creator_relationship", None),
        }
        unit_id = out.get("unit_id")
        unit_code = out.pop("unit_code", None)
        unit_label = out.pop("unit_label", None)
        if unit_id and (unit_code or unit_label):
            out["unit"] = {
                "id": unit_id,
                "code": unit_code,
                "unit_label": unit_label,
            }

        for key in (
            "removal_reason",
            "removed_by_contact_id",
            "deleted_at",
        ):
            out.pop(key, None)

        return out
