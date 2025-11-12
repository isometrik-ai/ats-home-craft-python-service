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

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_supabase_admin_client',
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

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_supabase_admin_client',
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

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_supabase_admin_client',
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

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_supabase_admin_client',
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

        with patch('libs.shared_db.postgres_db.user_service_operations.verification_operations.get_supabase_admin_client',
                   AsyncMock(return_value=mock_supabase)):

            result = await get_verification_code_by_id(verification_id)

            assert result is None

