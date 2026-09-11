"""Pydantic schemas for household pets (ADR 0016)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.contact_onboarding import HouseholdMemberResponse
from apps.user_service.app.schemas.enums.pets import PetGender, PetVaccinationStatus
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


def _validate_pet_name(name: str) -> str:
    """Trim and reject empty pet names."""
    trimmed = name.strip()
    if not trimmed:
        raise ValidationException(
            message_key="pets.errors.invalid_name",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    return trimmed


def _validate_photo_paths(photo_paths: list[str] | None) -> list[str] | None:
    """Validate storage paths for pet images."""
    if photo_paths is None:
        return photo_paths
    for path in photo_paths:
        if not path or len(path) > 500:
            raise ValidationException(
                message_key="pets.errors.invalid_photo_path",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
    return photo_paths


class PetBreedOption(BaseModel):
    """One breed entry in the static catalog."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str


class PetTypeOption(BaseModel):
    """One pet type entry in the static catalog."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    icon: str | None = None
    breeds: list[PetBreedOption] = Field(default_factory=list)


class PetCatalogResponse(BaseModel):
    """Static pet type and breed catalog."""

    model_config = ConfigDict(extra="ignore")

    pet_types: list[PetTypeOption] = Field(default_factory=list)


class PetCreatedBySummary(BaseModel):
    """Resident who created the pet profile."""

    model_config = ConfigDict(extra="ignore")

    contact_id: str
    display_name: str | None = None
    profile_photo_url: str | None = None
    relationship: str | None = None


class PetUnitSummary(BaseModel):
    """Unit summary on pet detail."""

    model_config = ConfigDict(extra="ignore")

    id: str
    code: str | None = None
    unit_label: str | None = None


class CreatePetRequest(BaseModel):
    """Create a household pet profile."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    name: str = Field(..., min_length=1, max_length=100)
    pet_type: str = Field(..., min_length=1, max_length=100)
    breed: str = Field(..., min_length=1, max_length=100)
    vaccination_status: PetVaccinationStatus
    gender: PetGender | None = None
    date_of_birth: date | None = None
    photo_paths: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, name: str) -> str:
        """Trim and reject whitespace-only names."""
        if not isinstance(name, str):
            return name
        return _validate_pet_name(name)

    @field_validator("photo_paths")
    @classmethod
    def validate_photo_paths(cls, photo_paths: list[str]) -> list[str]:
        """Validate storage paths for pet images."""
        return _validate_photo_paths(photo_paths) or []


class UpdatePetRequest(BaseModel):
    """Patch a household pet profile."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=100)
    pet_type: str | None = Field(None, min_length=1, max_length=100)
    breed: str | None = Field(None, min_length=1, max_length=100)
    vaccination_status: PetVaccinationStatus | None = None
    gender: PetGender | None = None
    date_of_birth: date | None = None
    photo_paths: list[str] | None = Field(None, max_length=10)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, name: str | None) -> str | None:
        """Trim and reject whitespace-only names."""
        if name is None or not isinstance(name, str):
            return name
        return _validate_pet_name(name)

    @field_validator("photo_paths")
    @classmethod
    def validate_photo_paths(cls, photo_paths: list[str] | None) -> list[str] | None:
        """Validate storage paths for pet images."""
        return _validate_photo_paths(photo_paths)


class RemovePetRequest(BaseModel):
    """Soft-remove a pet profile with reason."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=3, max_length=500)


class PetResponse(BaseModel):
    """Pet profile row."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    project_id: str
    unit_id: str
    created_by_contact_id: str
    name: str
    pet_type: str
    breed: str
    gender: str | None = None
    date_of_birth: str | None = None
    vaccination_status: str
    photo_paths: list[str] = Field(default_factory=list)
    primary_photo_path: str | None = None
    status: str
    sort_order: int = 0
    created_at: str
    updated_at: str
    created_by: PetCreatedBySummary | None = None
    unit: PetUnitSummary | None = None


class PetDetailResponse(PetResponse):
    """Pet profile with household members on the unit."""

    household_members: list[HouseholdMemberResponse] = Field(default_factory=list)


class PetCatalogApiResponse(BaseModel):
    """API envelope for GET /pets/catalog."""

    model_config = ConfigDict(extra="ignore")

    data: PetCatalogResponse


class PetApiResponse(BaseModel):
    """API envelope for single pet."""

    model_config = ConfigDict(extra="ignore")

    data: PetResponse


class PetDetailApiResponse(BaseModel):
    """API envelope for pet detail."""

    model_config = ConfigDict(extra="ignore")

    data: PetDetailResponse


class PetListApiResponse(BaseModel):
    """API envelope for pet list."""

    model_config = ConfigDict(extra="ignore")

    data: list[PetResponse]
    total: int
    page: int
    page_size: int
