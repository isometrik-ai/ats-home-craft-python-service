"""Unit tests for facility list query parsing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.enums import FacilityStatus, FacilityType
from apps.user_service.app.schemas.project_inventory import (
    FacilityListQuery,
    build_facility_list_query,
)


def test_build_facility_list_query_without_search():
    """No search param yields None search in query object."""
    query = build_facility_list_query(
        facility_types=None,
        status=FacilityStatus.ACTIVE,
    )
    assert query.search is None
    assert query.status == FacilityStatus.ACTIVE


def test_build_facility_list_query_trims_search():
    """Surrounding whitespace is stripped from search."""
    query = build_facility_list_query(
        facility_types=None,
        status=None,
        search="  Olym  ",
    )
    assert query.search == "Olym"


def test_build_facility_list_query_blank_search_becomes_none():
    """Whitespace-only search is treated as no filter."""
    query = build_facility_list_query(
        facility_types=None,
        status=None,
        search="   ",
    )
    assert query.search is None


def test_build_facility_list_query_with_types_and_search():
    """Search combines with comma-separated facility types."""
    query = build_facility_list_query(
        facility_types=["recreation", "sports"],
        status=FacilityStatus.ACTIVE,
        search="pool",
    )
    assert query.facility_types == [FacilityType.RECREATION, FacilityType.SPORTS]
    assert query.search == "pool"


def test_facility_list_query_rejects_empty_search_string():
    """Schema rejects empty search (min_length=1)."""
    with pytest.raises(ValidationError):
        FacilityListQuery(search="")
