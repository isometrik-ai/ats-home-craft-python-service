"""Test module for audit operations database functions.

This module contains comprehensive tests for:
- create_audit_log
- get_audit_log_by_id
- delete_all_audit_logs
- get_audit_logs_list
- get_audit_logs_count
- get_last_audit_log_hash
- bulk_create_audit_logs
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from libs.shared_db.postgres_db.user_service_operations.audit_operations import (
    AuditLogFilter,
    bulk_create_audit_logs,
    create_audit_log,
    delete_all_audit_logs,
    get_audit_log_by_id,
    get_audit_logs_count,
    get_audit_logs_list,
    get_last_audit_log_hash,
)


class TestCreateAuditLog:
    """Test cases for create_audit_log function."""

    @pytest.mark.asyncio
    async def test_create_audit_log_success(self):
        """Test successful audit log creation."""
        audit_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "public",
            "table_name": "users",
            "record_id": str(uuid.uuid4()),
            "old_values": {"name": "Old Name"},
            "new_values": {"name": "New Name"},
            "changed_fields": ["name"],
            "compliance_tags": ["GDPR"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "timestamp": datetime.now(timezone.utc),
            "hash_signature": "abc123",
            "previous_hash": "def456",
            "description": "User created",
            "retention_date": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management",
        }

        mock_result = MagicMock()
        mock_result.data = [
            {"id": str(uuid.uuid4()), "organization_id": audit_data["organization_id"]}
        ]

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()
            MagicMock()

            mock_table.insert.return_value = mock_insert
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await create_audit_log(audit_data)

            assert result["id"] is not None
            assert result["organization_id"] == audit_data["organization_id"]
            mock_supabase.table.assert_called_once_with("audit_logs")

    @pytest.mark.asyncio
    async def test_create_audit_log_with_optional_fields(self):
        """Test audit log creation with optional fields."""
        audit_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "UPDATE",
            "data_classification": "private",
            "table_name": "organizations",
            "record_id": str(uuid.uuid4()),
            "risk_level": "medium",
            "ip_address": "192.0.2.1",
            "timestamp": datetime.now(timezone.utc),
            "hash_signature": "xyz789",
            "description": "Organization updated",
        }

        mock_result = MagicMock()
        mock_result.data = [{"id": str(uuid.uuid4())}]

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()

            mock_table.insert.return_value = mock_insert
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await create_audit_log(audit_data)

            assert result["id"] is not None

    @pytest.mark.asyncio
    async def test_create_audit_log_no_data_returned(self):
        """Test audit log creation when no data is returned."""
        audit_data = {
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "DELETE",
            "data_classification": "confidential",
            "table_name": "sessions",
            "record_id": str(uuid.uuid4()),
            "risk_level": "high",
            "ip_address": "10.0.0.1",
            "timestamp": datetime.now(timezone.utc),
            "hash_signature": "hash123",
            "description": "Session deleted",
        }

        mock_result = MagicMock()
        mock_result.data = []

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()

            mock_table.insert.return_value = mock_insert
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await create_audit_log(audit_data)

            assert result == {}


class TestGetAuditLogById:
    """Test cases for get_audit_log_by_id function."""

    @pytest.mark.asyncio
    async def test_get_audit_log_by_id_success(self):
        """Test successful audit log retrieval by ID."""
        audit_log_id = str(uuid.uuid4())
        mock_audit_log = {
            "id": audit_log_id,
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "user_email": "test@example.com",
            "action_type": "CREATE",
            "description": "Test audit log",
        }

        mock_result = MagicMock()
        mock_result.data = [mock_audit_log]

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq1 = MagicMock()
            mock_eq2 = MagicMock()
            mock_eq3 = MagicMock()
            mock_limit = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq1
            mock_eq1.eq.return_value = mock_eq2
            mock_eq2.eq.return_value = mock_eq3
            mock_eq3.limit.return_value = mock_limit
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_audit_log_by_id(audit_log_id, str(uuid.uuid4()), str(uuid.uuid4()))

            assert result == mock_audit_log
            mock_select.eq.assert_called_once_with("id", audit_log_id)

    @pytest.mark.asyncio
    async def test_get_audit_log_by_id_not_found(self):
        """Test audit log retrieval when ID doesn't exist."""
        audit_log_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = []

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq1 = MagicMock()
            mock_eq2 = MagicMock()
            mock_eq3 = MagicMock()
            mock_limit = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq1
            mock_eq1.eq.return_value = mock_eq2
            mock_eq2.eq.return_value = mock_eq3
            mock_eq3.limit.return_value = mock_limit
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_audit_log_by_id(audit_log_id, str(uuid.uuid4()), str(uuid.uuid4()))

            assert result is None


class TestDeleteAllAuditLogs:
    """Test cases for delete_all_audit_logs function."""

    @pytest.mark.asyncio
    async def test_delete_all_audit_logs_success(self):
        """Test successful deletion of all audit logs."""
        mock_count_result = MagicMock()
        mock_count_result.count = 5

        mock_delete_result = MagicMock()

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_delete = MagicMock()
            mock_neq = MagicMock()

            # Mock count query
            mock_table.select.return_value = mock_select
            mock_select.execute = AsyncMock(return_value=mock_count_result)

            # Mock delete query
            mock_table.delete.return_value = mock_delete
            mock_delete.neq.return_value = mock_neq
            mock_neq.execute = AsyncMock(return_value=mock_delete_result)

            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await delete_all_audit_logs()

            assert result == 5
            mock_table.select.assert_called_once_with("id", count="exact")
            mock_table.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_all_audit_logs_no_count(self):
        """Test deletion when count is None."""
        mock_count_result = MagicMock()
        mock_count_result.count = None

        mock_delete_result = MagicMock()

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_delete = MagicMock()
            mock_neq = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.execute = AsyncMock(return_value=mock_count_result)

            mock_table.delete.return_value = mock_delete
            mock_delete.neq.return_value = mock_neq
            mock_neq.execute = AsyncMock(return_value=mock_delete_result)

            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await delete_all_audit_logs()

            assert result == 0


class TestGetAuditLogsList:
    """Test cases for get_audit_logs_list function."""

    @pytest.mark.asyncio
    async def test_get_audit_logs_list_success(self):
        """Test successful audit logs list retrieval."""
        organization_id = str(uuid.uuid4())
        filter_params = AuditLogFilter(organization_id=organization_id, limit=10, offset=0)

        mock_audit_logs = [
            {
                "id": str(uuid.uuid4()),
                "organization_id": organization_id,
                "action_type": "CREATE",
            },
            {
                "id": str(uuid.uuid4()),
                "organization_id": organization_id,
                "action_type": "UPDATE",
            },
        ]

        mock_result = MagicMock()
        mock_result.data = mock_audit_logs

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_order = MagicMock()
            mock_range = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq
            mock_eq.eq.return_value = mock_eq  # Chain the eq calls
            mock_eq.order.return_value = mock_order
            mock_order.range.return_value = mock_range
            mock_range.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_audit_logs_list(filter_params)

            assert len(result) == 2
            assert result[0]["organization_id"] == organization_id

    @pytest.mark.asyncio
    async def test_get_audit_logs_list_with_filters(self):
        """Test audit logs list with multiple filters."""
        organization_id = str(uuid.uuid4())
        filter_params = AuditLogFilter(
            organization_id=organization_id,
            action_type="CREATE",
            table_name="users",
            user_id=str(uuid.uuid4()),
            start_date=datetime.now(timezone.utc),
            end_date=datetime.now(timezone.utc),
            search="test",
            limit=5,
            offset=10,
        )

        mock_result = MagicMock()
        mock_result.data = []

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq1 = MagicMock()
            mock_eq2 = MagicMock()
            mock_eq3 = MagicMock()
            mock_eq4 = MagicMock()
            mock_gte = MagicMock()
            mock_lte = MagicMock()
            mock_or = MagicMock()
            mock_order = MagicMock()
            mock_range = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq1
            mock_eq1.eq.return_value = mock_eq2
            mock_eq2.eq.return_value = mock_eq3
            mock_eq3.eq.return_value = mock_eq4
            mock_eq4.gte.return_value = mock_gte
            mock_gte.lte.return_value = mock_lte
            mock_lte.or_.return_value = mock_or
            mock_or.order.return_value = mock_order
            mock_order.range.return_value = mock_range
            mock_range.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_audit_logs_list(filter_params)

            assert result == []

    @pytest.mark.asyncio
    async def test_get_audit_logs_list_no_data(self):
        """Test audit logs list when no data is returned."""
        filter_params = AuditLogFilter(organization_id=str(uuid.uuid4()), limit=10, offset=0)

        mock_result = MagicMock()
        mock_result.data = None

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_order = MagicMock()
            mock_range = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq
            mock_eq.eq.return_value = mock_eq  # Chain the eq calls
            mock_eq.order.return_value = mock_order
            mock_order.range.return_value = mock_range
            mock_range.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_audit_logs_list(filter_params)

            assert result == []


class TestGetAuditLogsCount:
    """Test cases for get_audit_logs_count function."""

    @pytest.mark.asyncio
    async def test_get_audit_logs_count_success(self):
        """Test successful audit logs count retrieval."""
        organization_id = str(uuid.uuid4())
        filter_params = AuditLogFilter(organization_id=organization_id, action_type="CREATE")

        mock_result = MagicMock()
        mock_result.count = 15

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq1 = MagicMock()
            mock_eq2 = MagicMock()
            mock_eq3 = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq1
            mock_eq1.eq.return_value = mock_eq2
            mock_eq2.eq.return_value = mock_eq3
            mock_eq3.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_audit_logs_count(str(uuid.uuid4()), str(uuid.uuid4()), filter_params)

            assert result == 15

    @pytest.mark.asyncio
    async def test_get_audit_logs_count_with_search(self):
        """Test audit logs count with search filter."""
        filter_params = AuditLogFilter(organization_id=str(uuid.uuid4()), search="test search")

        mock_result = MagicMock()
        mock_result.count = 3

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq1 = MagicMock()
            mock_eq2 = MagicMock()
            mock_or = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq1
            mock_eq1.eq.return_value = mock_eq2
            mock_eq2.or_.return_value = mock_or
            mock_or.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_audit_logs_count(str(uuid.uuid4()), str(uuid.uuid4()), filter_params)

            assert result == 3

    @pytest.mark.asyncio
    async def test_get_audit_logs_count_no_count(self):
        """Test audit logs count when count is None."""
        filter_params = AuditLogFilter(organization_id=str(uuid.uuid4()))

        mock_result = MagicMock()
        mock_result.count = None

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq1 = MagicMock()
            mock_eq2 = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq1
            mock_eq1.eq.return_value = mock_eq2
            mock_eq2.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_audit_logs_count(str(uuid.uuid4()), str(uuid.uuid4()), filter_params)

            assert result == 0


class TestGetLastAuditLogHash:
    """Test cases for get_last_audit_log_hash function."""

    @pytest.mark.asyncio
    async def test_get_last_audit_log_hash_success(self):
        """Test successful retrieval of last audit log hash."""
        organization_id = str(uuid.uuid4())
        mock_hash = "last_hash_signature_123"

        mock_result = MagicMock()
        mock_result.data = [{"hash_signature": mock_hash}]

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_order1 = MagicMock()
            mock_order2 = MagicMock()
            mock_limit = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq
            mock_eq.order.return_value = mock_order1
            mock_order1.order.return_value = mock_order2
            mock_order2.limit.return_value = mock_limit
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_last_audit_log_hash(organization_id)

            assert result == mock_hash

    @pytest.mark.asyncio
    async def test_get_last_audit_log_hash_no_data(self):
        """Test retrieval when no audit logs exist."""
        organization_id = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.data = []

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_select = MagicMock()
            mock_eq = MagicMock()
            mock_order1 = MagicMock()
            mock_order2 = MagicMock()
            mock_limit = MagicMock()

            mock_table.select.return_value = mock_select
            mock_select.eq.return_value = mock_eq
            mock_eq.order.return_value = mock_order1
            mock_order1.order.return_value = mock_order2
            mock_order2.limit.return_value = mock_limit
            mock_limit.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await get_last_audit_log_hash(organization_id)

            assert result is None


class TestBulkCreateAuditLogs:
    """Test cases for bulk_create_audit_logs function."""

    @pytest.mark.asyncio
    async def test_bulk_create_audit_logs_success(self):
        """Test successful bulk creation of audit logs."""
        audit_logs_data = [
            {
                "organization_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "user_email": "test1@example.com",
                "user_role": "admin",
                "action_type": "CREATE",
                "data_classification": "public",
                "table_name": "users",
                "record_id": str(uuid.uuid4()),
                "risk_level": "low",
                "ip_address": "127.0.0.1",
                "timestamp": datetime.now(timezone.utc),
                "hash_signature": "hash1",
                "description": "User 1 created",
                "retention_date": datetime.now(timezone.utc),
            },
            {
                "organization_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "user_email": "test2@example.com",
                "user_role": "user",
                "action_type": "UPDATE",
                "data_classification": "private",
                "table_name": "profiles",
                "record_id": str(uuid.uuid4()),
                "risk_level": "medium",
                "ip_address": "192.0.2.1",
                "timestamp": datetime.now(timezone.utc),
                "hash_signature": "hash2",
                "description": "Profile 2 updated",
                "retention_date": datetime.now(timezone.utc),
            },
        ]

        mock_result = MagicMock()
        mock_result.data = [
            {"id": str(uuid.uuid4()), "hash_signature": "hash1"},
            {"id": str(uuid.uuid4()), "hash_signature": "hash2"},
        ]

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()

            mock_table.insert.return_value = mock_insert
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await bulk_create_audit_logs(audit_logs_data)

            assert len(result) == 2
            assert result[0]["hash_signature"] == "hash1"
            assert result[1]["hash_signature"] == "hash2"

    @pytest.mark.asyncio
    async def test_bulk_create_audit_logs_empty_list(self):
        """Test bulk creation with empty list."""
        result = await bulk_create_audit_logs([])
        assert result == []

    @pytest.mark.asyncio
    async def test_bulk_create_audit_logs_no_data_returned(self):
        """Test bulk creation when no data is returned."""
        audit_logs_data = [
            {
                "organization_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "user_email": "test@example.com",
                "user_role": "admin",
                "action_type": "DELETE",
                "data_classification": "confidential",
                "table_name": "sessions",
                "record_id": str(uuid.uuid4()),
                "risk_level": "high",
                "ip_address": "10.0.0.1",
                "timestamp": datetime.now(timezone.utc),
                "hash_signature": "hash123",
                "description": "Session deleted",
                "retention_date": datetime.now(timezone.utc),
            }
        ]

        mock_result = MagicMock()
        mock_result.data = None

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()

            mock_table.insert.return_value = mock_insert
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await bulk_create_audit_logs(audit_logs_data)

            assert result == []

    @pytest.mark.asyncio
    async def test_bulk_create_audit_logs_with_optional_fields(self):
        """Test bulk creation with optional fields."""
        audit_logs_data = [
            {
                "organization_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "user_email": "test@example.com",
                "user_role": "admin",
                "action_type": "READ",
                "data_classification": "public",
                "table_name": "reports",
                "record_id": str(uuid.uuid4()),
                "old_values": {"status": "draft"},
                "new_values": {"status": "published"},
                "changed_fields": ["status"],
                "compliance_tags": ["SOX"],
                "risk_level": "low",
                "ip_address": "172.16.0.1",
                "timestamp": datetime.now(timezone.utc),
                "hash_signature": "hash456",
                "previous_hash": "hash789",
                "description": "Report published",
                "status_code": 200,
                "category": "reporting",
            }
        ]

        mock_result = MagicMock()
        mock_result.data = [{"id": str(uuid.uuid4())}]

        with patch(
            (
                "libs.shared_db.postgres_db.user_service_operations."
                "audit_operations.get_supabase_admin_client"
            )
        ) as mock_get_client:
            mock_supabase = MagicMock()
            mock_table = MagicMock()
            mock_insert = MagicMock()

            mock_table.insert.return_value = mock_insert
            mock_insert.execute = AsyncMock(return_value=mock_result)
            mock_supabase.table.return_value = mock_table
            mock_get_client.return_value = mock_supabase

            result = await bulk_create_audit_logs(audit_logs_data)

            assert len(result) == 1
            assert result[0]["id"] is not None
