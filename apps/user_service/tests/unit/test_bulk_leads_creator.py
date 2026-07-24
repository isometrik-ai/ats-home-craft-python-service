"""Unit tests for BulkLeadCreator with mocked DB."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.services.bulk_leads_creator import BulkLeadCreator

ORG_ID = "550e8400-e29b-41d4-a716-446655440001"


def _row(
    *,
    row_number: int = 1,
    name: str = "Lead A",
    stage_id: str = "stage-1",
    contact_id: str | None = None,
    company_id: str | None = None,
) -> dict:
    return {
        "row_number": row_number,
        "name": name,
        "stage_id": stage_id,
        "lead_source": "import",
        "lead_score": "hot",
        "owner_id": None,
        "contact_id": contact_id,
        "company_id": company_id,
    }


@pytest.fixture
def conn() -> MagicMock:
    """Asyncpg connection stub."""
    db = MagicMock()
    db.fetch = AsyncMock(return_value=[])
    db.execute = AsyncMock()
    return db


@pytest.fixture
def creator(conn: MagicMock) -> BulkLeadCreator:
    """BulkLeadCreator with mocked connection."""
    return BulkLeadCreator(db_connection=conn)


def test_filter_eligible_rows_stage_not_found(creator: BulkLeadCreator) -> None:
    """Invalid stage_id produces stage_not_found error."""
    rows = [_row(row_number=1, stage_id="bad-stage")]
    eligible, errors = creator._filter_eligible_rows(  # pylint: disable=protected-access
        rows=rows,
        stage_ok={"bad-stage": False},
        found_contacts=set(),
        found_companies=set(),
    )
    assert eligible == []
    assert errors == [(1, "lead_stages.errors.stage_not_found")]


def test_filter_eligible_rows_contact_not_found(creator: BulkLeadCreator) -> None:
    """Missing contact reference is rejected."""
    rows = [_row(row_number=2, contact_id="missing-contact")]
    eligible, errors = creator._filter_eligible_rows(  # pylint: disable=protected-access
        rows=rows,
        stage_ok={"stage-1": True},
        found_contacts=set(),
        found_companies=set(),
    )
    assert eligible == []
    assert errors == [(2, "contacts.errors.contact_not_found")]


def test_filter_eligible_rows_company_not_found(creator: BulkLeadCreator) -> None:
    """Missing company reference is rejected."""
    rows = [_row(row_number=3, company_id="missing-company")]
    eligible, errors = creator._filter_eligible_rows(  # pylint: disable=protected-access
        rows=rows,
        stage_ok={"stage-1": True},
        found_contacts=set(),
        found_companies=set(),
    )
    assert eligible == []
    assert errors == [(3, "companies.errors.company_not_found")]


def test_filter_eligible_rows_all_valid(creator: BulkLeadCreator) -> None:
    """Valid references pass through to eligible list."""
    rows = [_row(row_number=4, contact_id="c1", company_id="co1")]
    eligible, errors = creator._filter_eligible_rows(  # pylint: disable=protected-access
        rows=rows,
        stage_ok={"stage-1": True},
        found_contacts={"c1"},
        found_companies={"co1"},
    )
    assert len(eligible) == 1
    assert errors == []


@pytest.mark.asyncio
async def test_fetch_reference_validation_merges_chunks(creator: BulkLeadCreator) -> None:
    """Multiple stage ids merge contact/company validation results."""

    async def fake_fetch(_org_id, *, stage_id, contact_ids, company_ids):
        del contact_ids, company_ids
        if stage_id == "stage-a":
            return (True, {"c1"}, {"co1"})
        if stage_id == "stage-b":
            return (False, {"c2"}, set())
        return (None, set(), set())

    creator.repo.fetch_lead_reference_validation = AsyncMock(side_effect=fake_fetch)
    stage_ok, contacts, companies = await creator._fetch_reference_validation(  # pylint: disable=protected-access
        organization_id=ORG_ID,
        stage_ids={"stage-a", "stage-b"},
        contact_ids=["c1", "c2"],
        company_ids=["co1"],
    )
    assert stage_ok == {"stage-a": True, "stage-b": False}
    assert contacts == {"c1", "c2"}
    assert companies == {"co1"}


@pytest.mark.asyncio
async def test_create_leads_empty_rows(creator: BulkLeadCreator) -> None:
    """Empty input returns empty mapping and no errors."""
    mapping, errors = await creator.create_leads_for_rows(organization_id=ORG_ID, rows=[])
    assert mapping == {}
    assert errors == []


@pytest.mark.asyncio
async def test_create_leads_validation_only_errors(creator: BulkLeadCreator) -> None:
    """All rows failing validation returns errors without insert."""
    creator.repo.fetch_lead_reference_validation = AsyncMock(return_value=(False, set(), set()))
    mapping, errors = await creator.create_leads_for_rows(
        organization_id=ORG_ID,
        rows=[_row(row_number=1, stage_id="bad")],
    )
    assert mapping == {}
    assert errors == [(1, "lead_stages.errors.stage_not_found")]
    creator.db_connection.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_create_leads_success_with_associations(
    creator: BulkLeadCreator, conn: MagicMock
) -> None:
    """Successful bulk insert maps row numbers and creates associations."""
    creator.repo.fetch_lead_reference_validation = AsyncMock(return_value=(True, {"c1"}, {"co1"}))
    conn.fetch.return_value = [{"lead_id": "lead-1"}, {"lead_id": "lead-2"}]

    mapping, errors = await creator.create_leads_for_rows(
        organization_id=ORG_ID,
        rows=[
            _row(row_number=2, contact_id="c1"),
            _row(row_number=1, company_id="co1"),
        ],
    )

    assert mapping == {1: "lead-1", 2: "lead-2"}
    assert errors == []
    assert conn.execute.await_count == 2


@pytest.mark.asyncio
async def test_create_leads_insert_count_mismatch(
    creator: BulkLeadCreator, conn: MagicMock
) -> None:
    """Fewer returned lead ids than rows yields creation_failed errors."""
    creator.repo.fetch_lead_reference_validation = AsyncMock(return_value=(True, set(), set()))
    conn.fetch.return_value = [{"lead_id": "lead-1"}]

    mapping, errors = await creator.create_leads_for_rows(
        organization_id=ORG_ID,
        rows=[_row(row_number=1), _row(row_number=2)],
    )

    assert mapping == {}
    assert errors == [
        (1, "leads.errors.lead_creation_failed"),
        (2, "leads.errors.lead_creation_failed"),
    ]


@pytest.mark.asyncio
async def test_insert_contact_pairs_skips_empty(creator: BulkLeadCreator, conn: MagicMock) -> None:
    """Empty contact pairs skip execute."""
    await creator._insert_contact_pairs(  # pylint: disable=protected-access
        organization_id=ORG_ID,
        contact_pairs=[],
    )
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_insert_company_pairs_skips_empty(creator: BulkLeadCreator, conn: MagicMock) -> None:
    """Empty company pairs skip execute."""
    await creator._insert_company_pairs(  # pylint: disable=protected-access
        organization_id=ORG_ID,
        company_pairs=[],
    )
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_create_leads_blank_inserted_id_skips_mapping(
    creator: BulkLeadCreator, conn: MagicMock
) -> None:
    """Blank lead ids from insert are omitted from the row mapping."""
    creator.repo.fetch_lead_reference_validation = AsyncMock(return_value=(True, set(), set()))
    conn.fetch.return_value = [{"lead_id": ""}]

    mapping, errors = await creator.create_leads_for_rows(
        organization_id=ORG_ID,
        rows=[_row(row_number=1)],
    )

    assert mapping == {}
    assert errors == []
    conn.execute.assert_not_called()
