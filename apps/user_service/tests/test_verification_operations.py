# pylint: disable=all

"""
Unit tests for verification operations.
Tests the verification_operations.py module directly.
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


class TestVerificationOperations:
    """Test cases for verification_operations.py module."""

    @pytest.mark.asyncio
    async def test_create_verification_code_with_user_id(self):
        """Test create_verification_code with user_id (covers user_id branch)."""
        user_id = str(uuid.uuid4())
        mock_result = {
            "id": str(uuid.uuid4()),
            "type_text": "EMAIL",
            "given_input": "test@example.com",
            "triggered_text": "test@example.com",
            "verification_code": "1234",
            "verified": False,
            "expiry_at": int(datetime.now(timezone.utc).timestamp() * 1000) + 600000,
            "user_id": user_id,
            "ip_address": "127.0.0.1",
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
                type_text="EMAIL",
                given_input="test@example.com",
                triggered_text="test@example.com",
                user_id=user_id,
                ip_address="127.0.0.1"
            )

            assert result["id"] == mock_result["id"]
            assert result["user_id"] == user_id
            # Verify insert was called with user_id in the data
            mock_table.insert.assert_called_once()
            call_data = mock_table.insert.call_args[0][0]
            assert call_data["user_id"] == user_id

    @pytest.mark.asyncio
    async def test_create_verification_code_without_user_id(self):
        """Test create_verification_code without user_id."""
        mock_result = {
            "id": str(uuid.uuid4()),
            "type_text": "EMAIL",
            "given_input": "test@example.com",
            "triggered_text": "test@example.com",
            "verification_code": "1234",
            "verified": False,
            "expiry_at": int(datetime.now(timezone.utc).timestamp() * 1000) + 600000,
            "user_id": None,
            "ip_address": "127.0.0.1",
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
                type_text="EMAIL",
                given_input="test@example.com",
                triggered_text="test@example.com",
                user_id=None,
                ip_address="127.0.0.1"
            )

            assert result["id"] == mock_result["id"]
            # Verify insert was called without user_id in the data
            mock_table.insert.assert_called_once()
            call_data = mock_table.insert.call_args[0][0]
            assert "user_id" not in call_data

    @pytest.mark.asyncio
    async def test_create_verification_code_with_ip_address(self):
        """Test create_verification_code with ip_address (covers ip_address branch)."""
        ip_address = "192.168.1.1"
        mock_result = {
            "id": str(uuid.uuid4()),
            "type_text": "EMAIL",
            "given_input": "test@example.com",
            "triggered_text": "test@example.com",
            "verification_code": "1234",
            "verified": False,
            "expiry_at": int(datetime.now(timezone.utc).timestamp() * 1000) + 600000,
            "ip_address": ip_address,
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
                type_text="EMAIL",
                given_input="test@example.com",
                triggered_text="test@example.com",
                user_id=None,
                ip_address=ip_address
            )

            assert result["id"] == mock_result["id"]
            # Verify insert was called with ip_address in the data
            mock_table.insert.assert_called_once()
            call_data = mock_table.insert.call_args[0][0]
            assert call_data["ip_address"] == ip_address

    @pytest.mark.asyncio
    async def test_get_verification_code_by_id_success(self):
        """Test successful retrieval of verification code by ID."""
        verification_id = str(uuid.uuid4())
        mock_result = {
            "id": verification_id,
            "type_text": "EMAIL",
            "given_input": "test@example.com",
            "verification_code": "1234",
            "verified": False
        }

        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_eq = MagicMock()
        mock_execute = AsyncMock(return_value=MagicMock(data=[mock_result]))

        mock_supabase.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.eq.return_value = mock_eq
        mock_eq.execute = mock_execute

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
                   AsyncMock(return_value=mock_supabase)):

            result = await get_verification_code_by_id(verification_id)

            assert result["id"] == verification_id
            assert result["verification_code"] == "1234"

    @pytest.mark.asyncio
    async def test_get_verification_code_by_id_not_found(self):
        """Test retrieval of non-existent verification code."""
        verification_id = str(uuid.uuid4())

        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_eq = MagicMock()
        mock_execute = AsyncMock(return_value=MagicMock(data=[]))

        mock_supabase.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.eq.return_value = mock_eq
        mock_eq.execute = mock_execute

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
                   AsyncMock(return_value=mock_supabase)):

            result = await get_verification_code_by_id(verification_id)

            assert result is None

    @pytest.mark.asyncio
    async def test_create_verification_code_with_default_otp(self):
        """Test create_verification_code when EMAIL_OTP_ENABLED is False (covers EMAIL_DEFAULT_OTP branch)."""
        mock_result = {
            "id": str(uuid.uuid4()),
            "type_text": "EMAIL",
            "given_input": "test@example.com",
            "triggered_text": "test@example.com",
            "verification_code": "1111",
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
             patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.EMAIL_OTP_ENABLED', False), \
             patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.EMAIL_DEFAULT_OTP', "1111"):

            result = await create_verification_code(
                type_text="EMAIL",
                given_input="test@example.com",
                triggered_text="test@example.com"
            )

            assert result["id"] == mock_result["id"]
            # Verify insert was called with EMAIL_DEFAULT_OTP
            mock_table.insert.assert_called_once()
            call_data = mock_table.insert.call_args[0][0]
            assert call_data["verification_code"] == "1111"

    @pytest.mark.asyncio
    async def test_create_verification_code_empty_result_error(self):
        """Test create_verification_code when result.data is empty (covers error handling)."""
        from libs.shared_db.postgres_db.user_service_operations.exception_handling import DatabaseOperationError

        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_insert = MagicMock()
        mock_execute = AsyncMock(return_value=MagicMock(data=[]))

        mock_supabase.table.return_value = mock_table
        mock_table.insert.return_value = mock_insert
        mock_insert.execute = mock_execute

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
                   AsyncMock(return_value=mock_supabase)):

            with pytest.raises(DatabaseOperationError) as exc_info:
                await create_verification_code(
                    type_text="EMAIL",
                    given_input="test@example.com",
                    triggered_text="test@example.com"
                )

            assert "Failed to create verification code" in str(exc_info.value)
            assert exc_info.value.operation == "create_verification_code"

    @pytest.mark.asyncio
    async def test_get_recent_verification_codes_with_window(self):
        """Test get_recent_verification_codes with window_hours (covers time window filtering)."""
        mock_results = [
            {
                "id": str(uuid.uuid4()),
                "type_text": "EMAIL",
                "given_input": "test@example.com",
                "verification_code": "1234",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        ]

        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_eq1 = MagicMock()
        mock_eq2 = MagicMock()
        mock_gte = MagicMock()
        mock_order = MagicMock()
        mock_limit = MagicMock()
        mock_execute = AsyncMock(return_value=MagicMock(data=mock_results))

        mock_supabase.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.eq.return_value = mock_eq1
        mock_eq1.eq.return_value = mock_eq2
        mock_eq2.gte.return_value = mock_gte
        mock_gte.order.return_value = mock_order
        mock_order.limit.return_value = mock_limit
        mock_limit.execute = mock_execute

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
                   AsyncMock(return_value=mock_supabase)):

            result = await get_recent_verification_codes(
                type_text="EMAIL",
                given_input="test@example.com",
                limit=5,
                window_hours=24
            )

            assert len(result) == 1
            assert result[0]["type_text"] == "EMAIL"
            # Verify gte was called for time window filtering
            mock_eq2.gte.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_recent_verification_codes_without_window(self):
        """Test get_recent_verification_codes without window_hours."""
        mock_results = [
            {
                "id": str(uuid.uuid4()),
                "type_text": "EMAIL",
                "given_input": "test@example.com",
                "verification_code": "1234",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        ]

        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_eq1 = MagicMock()
        mock_eq2 = MagicMock()
        mock_order = MagicMock()
        mock_limit = MagicMock()
        mock_execute = AsyncMock(return_value=MagicMock(data=mock_results))

        mock_supabase.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.eq.return_value = mock_eq1
        mock_eq1.eq.return_value = mock_eq2
        # When window_hours is None, query goes directly from eq2 to order (no gte call)
        mock_eq2.order.return_value = mock_order
        mock_order.limit.return_value = mock_limit
        mock_limit.execute = mock_execute

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
                   AsyncMock(return_value=mock_supabase)):

            result = await get_recent_verification_codes(
                type_text="EMAIL",
                given_input="test@example.com",
                limit=5,
                window_hours=None
            )

            assert len(result) == 1
            # Verify order was called directly on eq2 (no gte in between)
            mock_eq2.order.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_recent_verification_codes_empty_result(self):
        """Test get_recent_verification_codes when no results found."""
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
    async def test_update_verification_code_success(self):
        """Test successful update of verification code."""
        verification_id = str(uuid.uuid4())
        mock_result = {
            "id": verification_id,
            "type_text": "EMAIL",
            "given_input": "test@example.com",
            "verification_code": "1234",
            "verified": True,
            "attempts": [{"timestamp": datetime.now(timezone.utc).isoformat(), "success": True}]
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
                attempts=[{"timestamp": datetime.now(timezone.utc).isoformat(), "success": True}]
            )

            assert result["id"] == verification_id
            assert result["verified"] is True
            assert len(result["attempts"]) == 1

    @pytest.mark.asyncio
    async def test_update_verification_code_empty_result_error(self):
        """Test update_verification_code when result.data is empty (covers error handling)."""
        from libs.shared_db.postgres_db.user_service_operations.exception_handling import DatabaseOperationError

        verification_id = str(uuid.uuid4())

        mock_supabase = MagicMock()
        mock_table = MagicMock()
        mock_update = MagicMock()
        mock_eq = MagicMock()
        mock_execute = AsyncMock(return_value=MagicMock(data=[]))

        mock_supabase.table.return_value = mock_table
        mock_table.update.return_value = mock_update
        mock_update.eq.return_value = mock_eq
        mock_eq.execute = mock_execute

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_fresh_supabase_admin_client',
                   AsyncMock(return_value=mock_supabase)):

            with pytest.raises(DatabaseOperationError) as exc_info:
                await update_verification_code(
                    verification_id=verification_id,
                    verified=True,
                    attempts=[]
                )

            assert f"Failed to update verification code: {verification_id}" in str(exc_info.value)
            assert exc_info.value.operation == "update_verification_code"

