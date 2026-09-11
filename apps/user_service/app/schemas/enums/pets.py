"""Enumeration values for household pets (ADR 0016)."""

from enum import Enum


class PetVaccinationStatus(str, Enum):
    """Postgres pet_vaccination_status enum."""

    COMPLETELY = "completely"
    PARTIALLY = "partially"
    NOT_TAKEN = "not_taken"


class PetGender(str, Enum):
    """Postgres pet_gender enum."""

    MALE = "male"
    FEMALE = "female"


class PetStatus(str, Enum):
    """Postgres pet_status enum."""

    ACTIVE = "active"
    REMOVED = "removed"
