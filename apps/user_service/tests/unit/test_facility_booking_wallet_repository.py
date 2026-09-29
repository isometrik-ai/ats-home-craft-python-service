"""Unit tests for FacilityBookingWalletRepository with fake asyncpg connection."""

from __future__ import annotations

import pytest

from apps.user_service.app.db.repositories.facility_booking_wallet_repository import (
    FacilityBookingWalletRepository,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_ID = "660e8400-e29b-41d4-a716-446655440001"
CONTACT_ID = "770e8400-e29b-41d4-a716-446655440002"
WALLET_ID = "880e8400-e29b-41d4-a716-446655440003"
USER_ID = "990e8400-e29b-41d4-a716-446655440004"
TXN_ID = "aa0e8400-e29b-41d4-a716-446655440005"


def _wallet_row(**overrides) -> dict:
    base = {
        "id": WALLET_ID,
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "contact_id": CONTACT_ID,
        "balance": 1000,
        "credit_limit": 500,
        "created_at": None,
        "updated_at": None,
    }
    base.update(overrides)
    return base


class _FakeConn:
    """Minimal fake asyncpg connection with call recording."""

    def __init__(self, *, rows=None, row=None):
        self.rows = rows or []
        self.row = row
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query, *args):
        self.fetch_calls.append((query.strip(), args))
        return self.rows

    async def fetchrow(self, query, *args):
        self.fetchrow_calls.append((query.strip(), args))
        return self.row


@pytest.mark.asyncio
async def test_get_or_create():
    conn = _FakeConn(row=_wallet_row())
    repo = FacilityBookingWalletRepository(db_connection=conn)

    wallet = await repo.get_or_create(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
    )

    assert wallet["id"] == WALLET_ID
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO facility_booking_wallets" in query
    assert "ON CONFLICT (project_id, contact_id)" in query
    assert args == (ORG_ID, PROJECT_ID, CONTACT_ID)


@pytest.mark.asyncio
async def test_list_project():
    conn = _FakeConn(rows=[{**_wallet_row(), "contact_name": "Jane Doe"}])
    repo = FacilityBookingWalletRepository(db_connection=conn)

    wallets = await repo.list_project(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
    )

    assert len(wallets) == 1
    assert wallets[0]["contact_name"] == "Jane Doe"
    query, args = conn.fetch_calls[0]
    assert "FROM facility_booking_wallets w" in query
    assert "LEFT JOIN contacts c" in query
    assert args == (ORG_ID, PROJECT_ID)


@pytest.mark.asyncio
async def test_update_balance_without_credit_limit():
    conn = _FakeConn(row=_wallet_row(balance=2500))
    repo = FacilityBookingWalletRepository(db_connection=conn)

    wallet = await repo.update_balance(wallet_id=WALLET_ID, balance=2500)

    assert wallet["balance"] == 2500
    query, args = conn.fetchrow_calls[0]
    assert "SET balance = $2, updated_at = NOW()" in query
    assert "credit_limit" not in query
    assert args == (WALLET_ID, 2500)


@pytest.mark.asyncio
async def test_update_balance_with_credit_limit():
    conn = _FakeConn(row=_wallet_row(balance=2500, credit_limit=1000))
    repo = FacilityBookingWalletRepository(db_connection=conn)

    wallet = await repo.update_balance(
        wallet_id=WALLET_ID,
        balance=2500,
        credit_limit=1000,
    )

    assert wallet["credit_limit"] == 1000
    query, args = conn.fetchrow_calls[0]
    assert "credit_limit = $3" in query
    assert args == (WALLET_ID, 2500, 1000)


@pytest.mark.asyncio
async def test_update_balance_clear_credit_limit():
    conn = _FakeConn(row=_wallet_row(credit_limit=None))
    repo = FacilityBookingWalletRepository(db_connection=conn)

    await repo.update_balance(wallet_id=WALLET_ID, balance=0, credit_limit=None)

    query, args = conn.fetchrow_calls[0]
    assert "credit_limit = $3" in query
    assert args == (WALLET_ID, 0, None)


@pytest.mark.asyncio
async def test_set_limit():
    conn = _FakeConn(row=_wallet_row(credit_limit=2000))
    repo = FacilityBookingWalletRepository(db_connection=conn)

    wallet = await repo.set_limit(wallet_id=WALLET_ID, credit_limit=2000)

    assert wallet["credit_limit"] == 2000
    query, args = conn.fetchrow_calls[0]
    assert "SET credit_limit = $2" in query
    assert args == (WALLET_ID, 2000)


@pytest.mark.asyncio
async def test_insert_transaction():
    conn = _FakeConn(
        row={
            "id": TXN_ID,
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "wallet_id": WALLET_ID,
            "contact_id": CONTACT_ID,
            "entry_type": "credit",
            "amount": 500,
            "method": "cash",
            "description": "Top-up",
            "created_by_user_id": USER_ID,
        }
    )
    repo = FacilityBookingWalletRepository(db_connection=conn)

    txn = await repo.insert_transaction(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "wallet_id": WALLET_ID,
            "contact_id": CONTACT_ID,
            "entry_type": "credit",
            "amount": 500,
            "method": "cash",
            "description": "Top-up",
            "created_by_user_id": USER_ID,
        }
    )

    assert txn["id"] == TXN_ID
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO facility_booking_wallet_transactions" in query
    assert "::facility_booking_wallet_txn_type" in query
    assert args == (
        ORG_ID,
        PROJECT_ID,
        WALLET_ID,
        CONTACT_ID,
        "credit",
        500,
        "cash",
        "Top-up",
        USER_ID,
    )


@pytest.mark.asyncio
async def test_insert_transaction_optional_fields_default_none():
    conn = _FakeConn(row={"id": TXN_ID})
    repo = FacilityBookingWalletRepository(db_connection=conn)

    await repo.insert_transaction(
        {
            "organization_id": ORG_ID,
            "project_id": PROJECT_ID,
            "wallet_id": WALLET_ID,
            "contact_id": CONTACT_ID,
            "entry_type": "debit",
            "amount": 100,
            "description": "Booking charge",
        }
    )

    _, args = conn.fetchrow_calls[0]
    assert args[6] is None
    assert args[8] is None


@pytest.mark.asyncio
async def test_list_transactions_pagination():
    conn = _FakeConn(rows=[{"id": TXN_ID}])
    repo = FacilityBookingWalletRepository(db_connection=conn)

    txns = await repo.list_transactions(
        organization_id=ORG_ID,
        project_id=PROJECT_ID,
        contact_id=CONTACT_ID,
        page=3,
        page_size=25,
    )

    assert len(txns) == 1
    query, args = conn.fetch_calls[0]
    assert "FROM facility_booking_wallet_transactions" in query
    assert "ORDER BY posted_at DESC, created_at DESC" in query
    assert args == (ORG_ID, PROJECT_ID, CONTACT_ID, 50, 25)
