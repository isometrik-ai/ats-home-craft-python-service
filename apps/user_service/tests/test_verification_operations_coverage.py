# pylint: disable=all

"""
Additional test cases for verification operations to increase coverage.
Tests edge cases, different types, and error paths.
"""

import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from libs.shared_db.postgres_db.user_service_operations.verification_operations import (
    create_verification_code,
    get_verification_code_by_id,
    get_recent_verification_codes,
    update_verification_code
)


@pytest.mark.asyncio
async def test_create_verification_code_phone_type():
    """Test create_verification_code with PHONE_NUMBER type."""
    mock_result = {
        "id": str(uuid.uuid4()),
        "type_text": "PHONE_NUMBER",
        "given_input": "+1234567890",
        "triggered_text": "+1234567890",
        "verification_code": "5678",
        "verified": False,
        "expiry_at": int(datetime.now(timezone.utc).timestamp() * 1000) + 600000,
        "attempts": []
    }

    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_insert = MagicMock()
    mock_execute = AsyncMock(return_value=MagicMock(data=[mock_result]))

    mock_supabase.table.return_value = mock_table
    mock_table.insert.return_value = mock_insert
    mock_insert.execute = mock_execute

    with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        result = await create_verification_code(
            type_text="PHONE_NUMBER",
            given_input="+1234567890",
            triggered_text="+1234567890"
        )
        assert result["type_text"] == "PHONE_NUMBER"


@pytest.mark.asyncio
async def test_create_verification_code_phone_default_otp():
    """Test create_verification_code with PHONE_NUMBER type and default OTP."""
    mock_result = {
        "id": str(uuid.uuid4()),
        "type_text": "PHONE_NUMBER",
        "given_input": "+1234567890",
        "triggered_text": "+1234567890",
        "verification_code": "2222",
        "verified": False,
        "expiry_at": int(datetime.now(timezone.utc).timestamp() * 1000) + 600000,
        "attempts": []
    }

    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_insert = MagicMock()
    mock_execute = AsyncMock(return_value=MagicMock(data=[mock_result]))

    mock_supabase.table.return_value = mock_table
    mock_table.insert.return_value = mock_insert
    mock_insert.execute = mock_execute

    with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)), \
         patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.PHONE_OTP_ENABLED', False), \
         patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.PHONE_DEFAULT_OTP', "2222"):
        result = await create_verification_code(
            type_text="PHONE_NUMBER",
            given_input="+1234567890",
            triggered_text="+1234567890"
        )
        assert result["id"] == mock_result["id"]
        call_data = mock_table.insert.call_args[0][0]
        assert call_data["verification_code"] == "2222"


@pytest.mark.asyncio
async def test_create_verification_code_unknown_type():
    """Test create_verification_code with unknown type (falls back to legacy settings)."""
    mock_result = {
        "id": str(uuid.uuid4()),
        "type_text": "UNKNOWN",
        "given_input": "test",
        "triggered_text": "test",
        "verification_code": "3333",
        "verified": False,
        "expiry_at": int(datetime.now(timezone.utc).timestamp() * 1000) + 600000,
        "attempts": []
    }

    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_insert = MagicMock()
    mock_execute = AsyncMock(return_value=MagicMock(data=[mock_result]))

    mock_supabase.table.return_value = mock_table
    mock_table.insert.return_value = mock_insert
    mock_insert.execute = mock_execute

    with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)), \
         patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.OTP_ENABLED', False), \
         patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.DEFAULT_OTP', "3333"):
        result = await create_verification_code(
            type_text="UNKNOWN",
            given_input="test",
            triggered_text="test"
        )
        assert result["id"] == mock_result["id"]
        call_data = mock_table.insert.call_args[0][0]
        assert call_data["verification_code"] == "3333"


@pytest.mark.asyncio
async def test_get_recent_verification_codes_with_none_data():
    """Test get_recent_verification_codes when data is None."""
    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_select = MagicMock()
    mock_eq1 = MagicMock()
    mock_eq2 = MagicMock()
    mock_order = MagicMock()
    mock_limit = MagicMock()
    mock_execute = AsyncMock(return_value=MagicMock(data=None))

    mock_supabase.table.return_value = mock_table
    mock_table.select.return_value = mock_select
    mock_select.eq.return_value = mock_eq1
    mock_eq1.eq.return_value = mock_eq2
    mock_eq2.order.return_value = mock_order
    mock_order.limit.return_value = mock_limit
    mock_limit.execute = mock_execute

    with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        result = await get_recent_verification_codes(
            type_text="EMAIL",
            given_input="test@example.com",
            limit=5
        )
        assert result == []


@pytest.mark.asyncio
async def test_update_verification_code_with_attempts():
    """Test update_verification_code with attempts array."""
    verification_id = str(uuid.uuid4())
    attempts = [
        {"timestamp": datetime.now(timezone.utc).isoformat(), "success": False},
        {"timestamp": datetime.now(timezone.utc).isoformat(), "success": True}
    ]
    mock_result = {
        "id": verification_id,
        "type_text": "EMAIL",
        "given_input": "test@example.com",
        "verification_code": "1234",
        "verified": True,
        "attempts": attempts
    }

    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_update = MagicMock()
    mock_eq = MagicMock()
    mock_execute = AsyncMock(return_value=MagicMock(data=[mock_result]))

    mock_supabase.table.return_value = mock_table
    mock_table.update.return_value = mock_update
    mock_update.eq.return_value = mock_eq
    mock_eq.execute = mock_execute

    with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        result = await update_verification_code(
            verification_id=verification_id,
            verified=True,
            attempts=attempts
        )
        assert result["verified"] is True
        assert len(result["attempts"]) == 2


@pytest.mark.asyncio
async def test_update_verification_code_without_attempts():
    """Test update_verification_code without attempts."""
    verification_id = str(uuid.uuid4())
    mock_result = {
        "id": verification_id,
        "type_text": "EMAIL",
        "given_input": "test@example.com",
        "verification_code": "1234",
        "verified": True,
        "attempts": []
    }

    mock_supabase = MagicMock()
    mock_table = MagicMock()
    mock_update = MagicMock()
    mock_eq = MagicMock()
    mock_execute = AsyncMock(return_value=MagicMock(data=[mock_result]))

    mock_supabase.table.return_value = mock_table
    mock_table.update.return_value = mock_update
    mock_update.eq.return_value = mock_eq
    mock_eq.execute = mock_execute

    with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
               AsyncMock(return_value=mock_supabase)):
        result = await update_verification_code(
            verification_id=verification_id,
            verified=True,
            attempts=None
        )
        assert result["verified"] is True

