"""Unit tests for VerificationCodeRepository with fake connection."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from apps.user_service.app.db.repositories.verification_code_repository import (
    VerificationCodeRepository,
)

VERIFICATION_ID = "550e8400-e29b-41d4-a716-446655440000"
USER_ID = "660e8400-e29b-41d4-a716-446655440001"


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
async def test_get_verification_code_by_id_found_and_not_found():
    conn = _FakeConn(row={"id": VERIFICATION_ID, "verified": False})
    repo = VerificationCodeRepository(db_connection=conn)

    found = await repo.get_verification_code_by_id(VERIFICATION_ID)
    assert found["id"] == VERIFICATION_ID
    query, args = conn.fetchrow_calls[0]
    assert "FROM verification_codes" in query
    assert args == (VERIFICATION_ID,)

    conn.row = None
    missing = await repo.get_verification_code_by_id(VERIFICATION_ID)
    assert missing is None


@pytest.mark.asyncio
async def test_update_verification_code_success():
    attempts = [{"at": "2026-01-01T00:00:00Z", "success": False}]
    conn = _FakeConn(row={"id": VERIFICATION_ID, "verified": True, "attempts": attempts})
    repo = VerificationCodeRepository(db_connection=conn)

    updated = await repo.update_verification_code(
        VERIFICATION_ID,
        verified=True,
        attempts=attempts,
    )

    assert updated["verified"] is True
    query, args = conn.fetchrow_calls[0]
    assert "UPDATE verification_codes" in query
    assert args[0] is True
    assert args[1] == json.dumps(attempts)
    assert args[2] == VERIFICATION_ID


@pytest.mark.asyncio
async def test_update_verification_code_not_found_returns_none():
    conn = _FakeConn(row=None)
    repo = VerificationCodeRepository(db_connection=conn)

    result = await repo.update_verification_code(
        VERIFICATION_ID,
        verified=False,
        attempts=[],
    )

    assert result is None


@pytest.mark.asyncio
async def test_get_recent_verification_codes_without_window():
    conn = _FakeConn(rows=[{"id": VERIFICATION_ID}, {"id": "ver-2"}])
    repo = VerificationCodeRepository(db_connection=conn)

    rows = await repo.get_recent_verification_codes(
        type_text="EMAIL",
        given_input="user@example.com",
        limit=5,
    )

    assert len(rows) == 2
    query, args = conn.fetch_calls[0]
    assert "type_text = $1" in query
    assert "given_input = $2" in query
    assert "created_at >=" not in query
    assert args[:2] == ("EMAIL", "user@example.com")
    assert args[-1] == 5


@pytest.mark.asyncio
async def test_get_recent_verification_codes_with_window():
    conn = _FakeConn(rows=[{"id": VERIFICATION_ID}])
    repo = VerificationCodeRepository(db_connection=conn)

    rows = await repo.get_recent_verification_codes(
        type_text="PHONE_NUMBER",
        given_input="+15551234567",
        limit=3,
        window_hours=24,
    )

    assert len(rows) == 1
    query, args = conn.fetch_calls[0]
    assert "created_at >= $3" in query
    assert "ORDER BY created_at DESC LIMIT $4" in query
    assert args[0] == "PHONE_NUMBER"
    assert args[1] == "+15551234567"
    assert isinstance(args[2], datetime)
    assert args[3] == 3
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    assert abs((args[2] - cutoff).total_seconds()) < 5


@pytest.mark.asyncio
async def test_insert_verification_code_with_defaults():
    expiry = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
    conn = _FakeConn(
        row={
            "id": VERIFICATION_ID,
            "type_text": "EMAIL",
            "given_input": "user@example.com",
            "verified": False,
        }
    )
    repo = VerificationCodeRepository(db_connection=conn)

    inserted = await repo.insert_verification_code(
        {
            "type_text": "EMAIL",
            "given_input": "user@example.com",
            "triggered_text": "user@example.com",
            "verification_code": "123456",
            "expiry_at": expiry,
            "user_id": USER_ID,
            "ip_address": "127.0.0.1",
        }
    )

    assert inserted["id"] == VERIFICATION_ID
    query, args = conn.fetchrow_calls[0]
    assert "INSERT INTO verification_codes" in query
    assert args[0] == "EMAIL"
    assert args[3] == "123456"
    assert args[4] is False
    assert args[5] == expiry
    assert args[6] == json.dumps([])
    assert args[7] == USER_ID
    assert args[8] == "127.0.0.1"


@pytest.mark.asyncio
async def test_insert_verification_code_with_attempts():
    attempts = [{"code": "000000", "success": False}]
    expiry = datetime(2026, 6, 1, tzinfo=timezone.utc)
    conn = _FakeConn(row={"id": VERIFICATION_ID})
    repo = VerificationCodeRepository(db_connection=conn)

    await repo.insert_verification_code(
        {
            "type_text": "EMAIL",
            "given_input": "other@example.com",
            "triggered_text": "other@example.com",
            "verification_code": "654321",
            "verified": True,
            "expiry_at": expiry,
            "attempts": attempts,
        }
    )

    _, args = conn.fetchrow_calls[0]
    assert args[3] == "654321"
    assert args[4] is True
    assert args[5] == expiry
    assert args[6] == json.dumps(attempts)
