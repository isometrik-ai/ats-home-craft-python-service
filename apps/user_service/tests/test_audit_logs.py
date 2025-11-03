# pylint: disable=all

"""
Test module for audit logging functionality.

This module contains comprehensive tests for:
- Audit decorator (@audit_api_call)
- AuditLogger class
- AuditEventData class
- Audit utility functions
"""

import uuid
import json
import asyncio
from datetime import datetime, timezone
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request, HTTPException
from fastapi.testclient import TestClient

from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.audit_logs.audit_logger import AuditLogger, AuditEventData
from apps.user_service.app.dependencies.audit_logs.audit_logs_utils import (
    build_audit_logs_filter_message,
    format_audit_log_data,
    format_audit_log_detail_data,
    check_audit_logs_view_permission
)
from libs.shared_db.postgres_db.user_service_operations.exception_handling import DatabaseOperationError


@pytest_asyncio.fixture
async def mock_request():
    """Fixture for mocked FastAPI request."""
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {
        "user-agent": "test-agent",
        "x-forwarded-for": "10.0.0.1",
        "content-type": "application/json"
    }
    request.form = AsyncMock(return_value={})
    request.body = AsyncMock(return_value=b'{}')
    request.json = AsyncMock(return_value={})
    request.url = MagicMock()
    request.url.path = "/test/path"
    request.method = "POST"
    request.query_params = {}
    return request


@pytest.fixture
def mock_user_context():
    """Fixture for user context data."""
    return {
        "user_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "user_email": "test@example.com",
        "user_role": "admin"
    }


@pytest.fixture
def audit_event_data(mock_user_context):
    """Fixture for AuditEventData instance."""
    return AuditEventData(
        user_context=mock_user_context,
        action_type="CREATE",
        data_classification="confidential",
        table_name="users",
        record_id=str(uuid.uuid4()),
        old_values=None,
        new_values={"name": "test"},
        changed_fields=None,
        compliance_tags=["gdpr"],
        risk_level="low",
        description="Test audit event",
        status_code=200,
        category="user_management"
    )


class TestAuditDecorator:
    """Tests for @audit_api_call decorator."""

    @pytest.mark.asyncio
    async def test_audit_decorator_metadata_attachment(self):
        """Test that decorator properly attaches metadata to function."""

        @audit_api_call(
            action_type="CREATE",
            table_name="test_table",
            data_classification="confidential",
            compliance_tags=["gdpr"],
            category="test"
        )
        async def test_function(request: Request):
            return {"status": "success"}

        # Verify metadata is attached
        assert hasattr(test_function, "audit_metadata")
        metadata = test_function.audit_metadata
        assert metadata["action_type"] == "CREATE"
        assert metadata["table_name"] == "test_table"
        assert metadata["data_classification"] == "confidential"
        assert metadata["compliance_tags"] == ["gdpr"]
        assert metadata["category"] == "test"

    @pytest.mark.asyncio
    async def test_audit_decorator_with_missing_request(self):
        """Test decorator behavior when request argument is missing."""

        @audit_api_call(action_type="CREATE", table_name="test")
        async def test_function():
            return {"status": "success"}

        with pytest.raises(ValueError, match="Request must be passed as a keyword argument"):
            await test_function()



class TestAuditLogger:
    """Tests for AuditLogger class."""

    @pytest.mark.asyncio
    async def test_audit_logger_initialization(self):
        """Test AuditLogger initialization."""
        logger = AuditLogger()
        assert logger._queue.maxsize == 500
        assert logger._batch_size == 10
        assert logger._batch_timeout == 3
        assert logger._max_retries == 3
        assert logger._processing_task is None
        assert logger._last_hash is None
        assert not logger._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_audit_logger_start_processing(self):
        """Test starting the audit processing task."""
        logger = AuditLogger()

        # Mock the processing task
        mock_process = AsyncMock()
        with patch.object(logger, '_process_audit_queue', mock_process):
            # Start processing
            logger.start_processing()
            assert logger._processing_task is not None
            assert not logger._processing_task.done()

            # Verify processing task was created
            mock_process.assert_called_once()

        # Cleanup
        await logger.shutdown()

    @pytest.mark.asyncio
    async def test_audit_logger_shutdown(self):
        """Test graceful shutdown of audit logger."""
        logger = AuditLogger()

        # Mock the processing task
        mock_process = AsyncMock()
        with patch.object(logger, '_process_audit_queue', mock_process):
            # Start and then shutdown
            logger.start_processing()
            await logger.shutdown()

            assert logger._shutdown_event.is_set()
            assert logger._processing_task.done()

    @pytest.mark.asyncio
    async def test_log_audit_event_success(self, mock_request, audit_event_data):
        """Test successful audit event logging."""
        logger = AuditLogger()

        # Create a mock event
        mock_event = {"test": "data"}

        # Create an event to control the processing loop
        process_event = asyncio.Event()

        async def mock_process():
            """Mock the processing task to handle one event then exit"""
            # Wait for an event to be available in the queue
            try:
                event = await logger._queue.get()
                events = [event]
                await mock_write(events)
                process_event.set()
            except Exception:
                # Handle any errors gracefully
                process_event.set()

        # Mock write operation
        mock_write = AsyncMock()

        with patch.object(logger, '_process_audit_queue', side_effect=mock_process), \
             patch.object(logger, '_write_audit_batch', mock_write), \
             patch.object(logger, '_create_audit_event_dict', return_value=mock_event):

            # Start processing
            logger.start_processing()

            try:
                # Log event
                await logger.log_audit_event(audit_event_data, mock_request)

                # Wait for processing to complete
                await asyncio.wait_for(process_event.wait(), timeout=2.0)

                # Verify write was called with correct data
                mock_write.assert_called_once_with([mock_event])
            finally:
                # Cleanup
                await logger.shutdown()

    @pytest.mark.asyncio
    async def test_log_audit_event_queue_full(self, mock_request, audit_event_data):
        """Test behavior when audit queue is full."""
        logger = AuditLogger()
        logger._queue = asyncio.Queue(maxsize=1)  # Small queue for testing

        # Mock event creation
        mock_event = {"test": "data"}
        with patch.object(logger, '_create_audit_event_dict', return_value=mock_event):
            # Fill the queue
            await logger._queue.put({"dummy": "event"})

            # Try to log when queue is full
            with patch('asyncio.Queue.put_nowait', side_effect=asyncio.QueueFull()), \
                 patch('asyncio.Queue.put', side_effect=asyncio.TimeoutError()):
                await logger.log_audit_event(audit_event_data, mock_request)
                # Should handle the error gracefully

    @pytest.mark.asyncio
    async def test_write_audit_batch_success(self):
        """Test successful batch write to database."""
        logger = AuditLogger()
        events = [
            {
                "organization_id": "org123",
                "user_id": "user123",
                "user_email": "test@example.com",
                "user_role": "admin",
                "action_type": "CREATE",
                "data_classification": "confidential",
                "table_name": "users",
                "record_id": "123",
                "old_values": None,
                "new_values": {"name": "test"},
                "changed_fields": None,
                "compliance_tags": ["gdpr"],
                "risk_level": "low",
                "ip_address": "127.0.0.1",
                "description": "Test event",
                "timestamp": datetime.now(timezone.utc),
                "status_code": 200,
                "category": "user_management"
            }
        ]

        # Mock database operations
        mock_get_hash = AsyncMock(return_value="prev_hash")
        mock_bulk_create = AsyncMock()

        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash', mock_get_hash), \
             patch('apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs', mock_bulk_create):
            await logger._write_audit_batch(events)

            # Verify database operations were called
            mock_get_hash.assert_called_once()
            mock_bulk_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry(self):
        """Test batch write with retries on failure."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock database operations
        mock_get_hash = AsyncMock(return_value="prev_hash")
        mock_bulk_create = AsyncMock()

        # Set up side effects for three attempts
        failures = [
            DatabaseOperationError("First failure"),
            DatabaseOperationError("Second failure")
        ]
        mock_bulk_create.side_effect = failures + [None]  # Last attempt succeeds

        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash', mock_get_hash), \
             patch('apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs', mock_bulk_create), \
             patch('asyncio.sleep', AsyncMock()) as mock_sleep:  # Mock sleep to speed up test

            # Should retry and eventually succeed
            await logger._write_audit_batch_with_retry(events)

            # Verify the mock was called exactly three times
            assert mock_bulk_create.call_count == 3, "Bulk create should be called exactly 3 times"

            # Verify exponential backoff
            assert mock_sleep.call_count == 2  # Called after first and second failures
            assert mock_sleep.call_args_list[0][0][0] == 1  # First retry after 1 second
            assert mock_sleep.call_args_list[1][0][0] == 2  # Second retry after 2 seconds

            # Verify the calls were made with the correct arguments
            for call_args in mock_bulk_create.call_args_list:
                assert len(call_args.args) == 1
                assert isinstance(call_args.args[0], list)
                assert len(call_args.args[0]) == 1
                assert call_args.args[0][0]["organization_id"] == "org123"

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db(self):
        """Test fetching last hash from database."""
        logger = AuditLogger()
        mock_hash = "test_hash"

        # Mock database operation
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash',
                  AsyncMock(return_value=mock_hash)):
            result = await logger._get_last_hash_from_db("org123")
            assert result == mock_hash

    @pytest.mark.asyncio
    async def test_collect_batch_events(self):
        """Test collecting events from queue into batch."""
        logger = AuditLogger()
        test_events = [{"id": 1}, {"id": 2}, {"id": 3}]

        # Add events to queue
        for event in test_events:
            await logger._queue.put(event)

        # Collect batch
        batch, got_events = await logger._collect_batch_events(timeout_duration=0.1)

        assert got_events is True
        assert len(batch) == 3
        assert all(event in test_events for event in batch)

    def test_generate_hash(self):
        """Test audit log hash generation."""
        logger = AuditLogger()
        event = {
            "organization_id": "org123",
            "user_id": "user123",
            "action_type": "CREATE",
            "timestamp": datetime.now(timezone.utc),
            "description": "Test event"
        }

        # Generate hash without previous hash
        hash1 = logger._generate_hash(event)
        assert isinstance(hash1, str)
        assert len(hash1) == 64  # SHA-256 hash length

        # Generate hash with previous hash
        logger._last_hash = hash1
        hash2 = logger._generate_hash(event)
        assert hash2 != hash1  # Should be different due to previous hash

    def test_calculate_retention_date(self):
        """Test retention date calculation."""
        logger = AuditLogger()
        timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)

        # Test different classifications
        assert logger._calculate_retention_date(timestamp, "phi").year == 2031  # 7 years
        assert logger._calculate_retention_date(timestamp, "pii").year == 2031  # 7 years
        assert logger._calculate_retention_date(timestamp, "financial").year == 2031  # 7 years
        assert logger._calculate_retention_date(timestamp, "general").year == 2027  # 3 years
        assert logger._calculate_retention_date(timestamp, "public").year == 2025  # 1 year
        assert logger._calculate_retention_date(timestamp, "unknown").year == 2027  # Default 3 years

    @pytest.mark.asyncio
    async def test_get_client_ip(self, mock_request):
        """Test client IP extraction from request."""
        logger = AuditLogger()

        # Test X-Forwarded-For
        assert logger._get_client_ip(mock_request) == "10.0.0.1"

        # Test X-Real-IP
        mock_request.headers = {"x-real-ip": "192.168.1.1"}
        assert logger._get_client_ip(mock_request) == "192.168.1.1"

        # Test direct client IP
        mock_request.headers = {}
        assert logger._get_client_ip(mock_request) == "127.0.0.1"

        # Test fallback
        mock_request.client = None
        assert logger._get_client_ip(mock_request) == "unknown"

    def test_get_queue_stats(self):
        """Test queue statistics retrieval."""
        logger = AuditLogger()
        stats = logger.get_queue_stats()

        assert "queue_size" in stats
        assert "max_queue_size" in stats
        assert "processing_active" in stats
        assert "shutdown_requested" in stats
        assert stats["max_queue_size"] == 500

    @pytest.mark.asyncio
    async def test_log_audit_event_data_validation_error(self, mock_request):
        """Test log_audit_event with data validation errors."""
        logger = AuditLogger()

        # Create invalid event data that will cause validation errors
        invalid_event_data = AuditEventData(
            user_context={},  # Missing required fields
            action_type="CREATE",
            data_classification="general",
            table_name="users",
            record_id="123",
            old_values=None,
            new_values=None,
            changed_fields=None,
            compliance_tags=[],
            risk_level="low",
            description="Test event"
        )

        # Mock _create_audit_event_dict to raise ValueError
        with patch.object(logger, '_create_audit_event_dict', side_effect=ValueError("Invalid data")):
            await logger.log_audit_event(invalid_event_data, mock_request)
            # Should handle the error gracefully without crashing

    @pytest.mark.asyncio
    async def test_log_audit_event_type_error(self, mock_request):
        """Test log_audit_event with TypeError."""
        logger = AuditLogger()

        # Mock _create_audit_event_dict to raise TypeError
        with patch.object(logger, '_create_audit_event_dict', side_effect=TypeError("Type error")):
            await logger.log_audit_event(None, mock_request)
            # Should handle the error gracefully

    @pytest.mark.asyncio
    async def test_log_audit_event_key_error(self, mock_request):
        """Test log_audit_event with KeyError."""
        logger = AuditLogger()

        # Mock _create_audit_event_dict to raise KeyError
        with patch.object(logger, '_create_audit_event_dict', side_effect=KeyError("Missing key")):
            await logger.log_audit_event(None, mock_request)
            # Should handle the error gracefully

    @pytest.mark.asyncio
    async def test_log_audit_event_attribute_error(self, mock_request):
        """Test log_audit_event with AttributeError."""
        logger = AuditLogger()

        # Mock _create_audit_event_dict to raise AttributeError
        with patch.object(logger, '_create_audit_event_dict', side_effect=AttributeError("Attribute error")):
            await logger.log_audit_event(None, mock_request)
            # Should handle the error gracefully

    @pytest.mark.asyncio
    async def test_log_audit_event_runtime_error(self, mock_request):
        """Test log_audit_event with RuntimeError."""
        logger = AuditLogger()

        # Mock _create_audit_event_dict to raise RuntimeError
        with patch.object(logger, '_create_audit_event_dict', side_effect=RuntimeError("Runtime error")):
            await logger.log_audit_event(None, mock_request)
            # Should handle the error gracefully

    @pytest.mark.asyncio
    async def test_log_audit_event_io_error(self, mock_request):
        """Test log_audit_event with IOError."""
        logger = AuditLogger()

        # Mock _create_audit_event_dict to raise IOError
        with patch.object(logger, '_create_audit_event_dict', side_effect=IOError("IO error")):
            await logger.log_audit_event(None, mock_request)
            # Should handle the error gracefully

    @pytest.mark.asyncio
    async def test_log_audit_event_queue_full_timeout(self, mock_request, audit_event_data):
        """Test log_audit_event when queue is full and timeout occurs."""
        logger = AuditLogger()
        logger._queue = asyncio.Queue(maxsize=1)  # Small queue for testing

        # Fill the queue
        await logger._queue.put({"dummy": "event"})

        # Mock event creation
        mock_event = {"test": "data"}
        with patch.object(logger, '_create_audit_event_dict', return_value=mock_event):
            # Mock put_nowait to raise QueueFull and put to timeout
            with patch.object(logger._queue, 'put_nowait', side_effect=asyncio.QueueFull()), \
                 patch.object(logger._queue, 'put', side_effect=asyncio.TimeoutError()):
                await logger.log_audit_event(audit_event_data, mock_request)
                # Should handle the timeout gracefully

    @pytest.mark.asyncio
    async def test_shutdown_timeout(self):
        """Test shutdown with timeout scenario."""
        logger = AuditLogger()

        # Create a mock processing task that never completes
        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        logger._processing_task = mock_task

        # Mock asyncio.wait_for to raise TimeoutError
        with patch('asyncio.wait_for', side_effect=asyncio.TimeoutError()):
            await logger.shutdown()

            # Verify shutdown event was set
            assert logger._shutdown_event.is_set()
            # Verify task was cancelled
            mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db_database_error(self):
        """Test _get_last_hash_from_db with database error."""
        logger = AuditLogger()

        # Mock get_last_audit_log_hash to raise DatabaseOperationError
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash',
                  AsyncMock(side_effect=DatabaseOperationError("Database error"))):
            result = await logger._get_last_hash_from_db("org123")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db_json_error(self):
        """Test _get_last_hash_from_db with JSON decode error."""
        logger = AuditLogger()

        # Mock get_last_audit_log_hash to raise JSONDecodeError
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash',
                  AsyncMock(side_effect=json.JSONDecodeError("Invalid JSON", "doc", 0))):
            result = await logger._get_last_hash_from_db("org123")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db_unicode_error(self):
        """Test _get_last_hash_from_db with Unicode error."""
        logger = AuditLogger()

        # Mock get_last_audit_log_hash to raise UnicodeError
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash',
                  AsyncMock(side_effect=UnicodeError("Unicode error"))):
            result = await logger._get_last_hash_from_db("org123")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db_lookup_error(self):
        """Test _get_last_hash_from_db with LookupError."""
        logger = AuditLogger()

        # Mock get_last_audit_log_hash to raise LookupError
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash',
                  AsyncMock(side_effect=LookupError("Lookup error"))):
            result = await logger._get_last_hash_from_db("org123")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db_attribute_error(self):
        """Test _get_last_hash_from_db with AttributeError."""
        logger = AuditLogger()

        # Mock get_last_audit_log_hash to raise AttributeError
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash',
                  AsyncMock(side_effect=AttributeError("Attribute error"))):
            result = await logger._get_last_hash_from_db("org123")
            assert result is None

    @pytest.mark.asyncio
    async def test_write_audit_batch_empty_events(self):
        """Test _write_audit_batch with empty events list."""
        logger = AuditLogger()

        # Should return early without doing anything
        await logger._write_audit_batch([])

    @pytest.mark.asyncio
    async def test_write_audit_batch_database_error(self):
        """Test _write_audit_batch with database error."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock get_last_hash_from_db to return a hash
        with patch.object(logger, '_get_last_hash_from_db', AsyncMock(return_value="prev_hash")), \
             patch('apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs',
                  AsyncMock(side_effect=DatabaseOperationError("Database error"))):

            with pytest.raises(DatabaseOperationError):
                await logger._write_audit_batch(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_json_error(self):
        """Test _write_audit_batch with JSON decode error."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock get_last_hash_from_db to return a hash
        with patch.object(logger, '_get_last_hash_from_db', AsyncMock(return_value="prev_hash")), \
             patch('apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs',
                  AsyncMock(side_effect=json.JSONDecodeError("Invalid JSON", "doc", 0))):

            with pytest.raises(json.JSONDecodeError):
                await logger._write_audit_batch(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_unicode_error(self):
        """Test _write_audit_batch with Unicode error."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock get_last_hash_from_db to return a hash
        with patch.object(logger, '_get_last_hash_from_db', AsyncMock(return_value="prev_hash")), \
             patch('apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs',
                  AsyncMock(side_effect=UnicodeError("Unicode error"))):

            with pytest.raises(UnicodeError):
                await logger._write_audit_batch(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_lookup_error(self):
        """Test _write_audit_batch with LookupError."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock get_last_hash_from_db to return a hash
        with patch.object(logger, '_get_last_hash_from_db', AsyncMock(return_value="prev_hash")), \
             patch('apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs',
                  AsyncMock(side_effect=LookupError("Lookup error"))):

            with pytest.raises(LookupError):
                await logger._write_audit_batch(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_attribute_error(self):
        """Test _write_audit_batch with AttributeError."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock get_last_hash_from_db to return a hash
        with patch.object(logger, '_get_last_hash_from_db', AsyncMock(return_value="prev_hash")), \
             patch('apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs',
                  AsyncMock(side_effect=AttributeError("Attribute error"))):

            with pytest.raises(AttributeError):
                await logger._write_audit_batch(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_os_error(self):
        """Test _write_audit_batch_with_retry with OSError."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock _write_audit_batch to raise OSError on all attempts
        with patch.object(logger, '_write_audit_batch', AsyncMock(side_effect=OSError("OS error"))), \
             patch('asyncio.sleep', AsyncMock()):  # Mock sleep to speed up test

            await logger._write_audit_batch_with_retry(events)
            # Should complete without raising (after max retries)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_runtime_error(self):
        """Test _write_audit_batch_with_retry with RuntimeError."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock _write_audit_batch to raise RuntimeError on all attempts
        with patch.object(logger, '_write_audit_batch', AsyncMock(side_effect=RuntimeError("Runtime error"))), \
             patch('asyncio.sleep', AsyncMock()):  # Mock sleep to speed up test

            await logger._write_audit_batch_with_retry(events)
            # Should complete without raising (after max retries)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_json_error(self):
        """Test _write_audit_batch_with_retry with JSONDecodeError."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock _write_audit_batch to raise JSONDecodeError on all attempts
        with patch.object(logger, '_write_audit_batch', AsyncMock(side_effect=json.JSONDecodeError("Invalid JSON", "doc", 0))), \
             patch('asyncio.sleep', AsyncMock()):  # Mock sleep to speed up test

            await logger._write_audit_batch_with_retry(events)
            # Should complete without raising (after max retries)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_unicode_error(self):
        """Test _write_audit_batch_with_retry with UnicodeError."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock _write_audit_batch to raise UnicodeError on all attempts
        with patch.object(logger, '_write_audit_batch', AsyncMock(side_effect=UnicodeError("Unicode error"))), \
             patch('asyncio.sleep', AsyncMock()):  # Mock sleep to speed up test

            await logger._write_audit_batch_with_retry(events)
            # Should complete without raising (after max retries)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_lookup_error(self):
        """Test _write_audit_batch_with_retry with LookupError."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock _write_audit_batch to raise LookupError on all attempts
        with patch.object(logger, '_write_audit_batch', AsyncMock(side_effect=LookupError("Lookup error"))), \
             patch('asyncio.sleep', AsyncMock()):  # Mock sleep to speed up test

            await logger._write_audit_batch_with_retry(events)
            # Should complete without raising (after max retries)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_attribute_error(self):
        """Test _write_audit_batch_with_retry with AttributeError."""
        logger = AuditLogger()
        events = [{
            "organization_id": "org123",
            "user_id": "user123",
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": None,
            "new_values": {"name": "test"},
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }]

        # Mock _write_audit_batch to raise AttributeError on all attempts
        with patch.object(logger, '_write_audit_batch', AsyncMock(side_effect=AttributeError("Attribute error"))), \
             patch('asyncio.sleep', AsyncMock()):  # Mock sleep to speed up test

            await logger._write_audit_batch_with_retry(events)
            # Should complete without raising (after max retries)

    @pytest.mark.asyncio
    async def test_collect_batch_events_timeout(self):
        """Test _collect_batch_events with timeout."""
        logger = AuditLogger()

        # Test with timeout when queue is empty
        batch, got_events = await logger._collect_batch_events(timeout_duration=0.01)

        assert got_events is False
        assert len(batch) == 0

    @pytest.mark.asyncio
    async def test_collect_batch_events_partial_batch(self):
        """Test _collect_batch_events with partial batch (less than batch_size)."""
        logger = AuditLogger()
        test_events = [{"id": 1}, {"id": 2}]  # Less than batch_size (10)

        # Add events to queue
        for event in test_events:
            await logger._queue.put(event)

        # Collect batch
        batch, got_events = await logger._collect_batch_events(timeout_duration=0.1)

        assert got_events is True
        assert len(batch) == 2
        assert all(event in test_events for event in batch)

    @pytest.mark.asyncio
    async def test_collect_batch_events_exact_batch_size(self):
        """Test _collect_batch_events with exact batch size."""
        logger = AuditLogger()
        test_events = [{"id": i} for i in range(10)]  # Exactly batch_size (10)

        # Add events to queue
        for event in test_events:
            await logger._queue.put(event)

        # Collect batch
        batch, got_events = await logger._collect_batch_events(timeout_duration=0.1)

        assert got_events is True
        assert len(batch) == 10
        assert all(event in test_events for event in batch)

    @pytest.mark.asyncio
    async def test_collect_batch_events_more_than_batch_size(self):
        """Test _collect_batch_events with more than batch size."""
        logger = AuditLogger()
        test_events = [{"id": i} for i in range(15)]  # More than batch_size (10)

        # Add events to queue
        for event in test_events:
            await logger._queue.put(event)

        # Collect batch
        batch, got_events = await logger._collect_batch_events(timeout_duration=0.1)

        assert got_events is True
        assert len(batch) == 10  # Should only collect up to batch_size
        assert all(event in test_events for event in batch)

    @pytest.mark.asyncio
    async def test_process_audit_queue_cancelled_error(self):
        """Test _process_audit_queue with CancelledError."""
        logger = AuditLogger()

        # Mock _collect_batch_events to raise CancelledError
        with patch.object(logger, '_collect_batch_events', AsyncMock(side_effect=asyncio.CancelledError())):
            # This should break the loop gracefully
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_timeout_error(self):
        """Test _process_audit_queue with TimeoutError."""
        logger = AuditLogger()

        # Mock _collect_batch_events to raise TimeoutError
        with patch.object(logger, '_collect_batch_events', AsyncMock(side_effect=asyncio.TimeoutError())):
            # This should break the loop gracefully
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_database_error(self):
        """Test _process_audit_queue with DatabaseOperationError."""
        logger = AuditLogger()

        # Mock _collect_batch_events to return events
        mock_batch = [{"test": "event"}]
        with patch.object(logger, '_collect_batch_events', AsyncMock(return_value=(mock_batch, True))), \
             patch.object(logger, '_write_audit_batch_with_retry', AsyncMock(side_effect=DatabaseOperationError("Database error"))), \
             patch.object(logger, '_handle_batch_error', MagicMock()), \
             patch('asyncio.sleep', AsyncMock()):

            # Set shutdown event to break the loop after one iteration
            logger._shutdown_event.set()
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_os_error(self):
        """Test _process_audit_queue with OSError."""
        logger = AuditLogger()

        # Mock _collect_batch_events to return events
        mock_batch = [{"test": "event"}]
        with patch.object(logger, '_collect_batch_events', AsyncMock(return_value=(mock_batch, True))), \
             patch.object(logger, '_write_audit_batch_with_retry', AsyncMock(side_effect=OSError("OS error"))), \
             patch.object(logger, '_handle_batch_error', MagicMock()), \
             patch('asyncio.sleep', AsyncMock()):

            # Set shutdown event to break the loop after one iteration
            logger._shutdown_event.set()
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_runtime_error(self):
        """Test _process_audit_queue with RuntimeError."""
        logger = AuditLogger()

        # Mock _collect_batch_events to return events
        mock_batch = [{"test": "event"}]
        with patch.object(logger, '_collect_batch_events', AsyncMock(return_value=(mock_batch, True))), \
             patch.object(logger, '_write_audit_batch_with_retry', AsyncMock(side_effect=RuntimeError("Runtime error"))), \
             patch.object(logger, '_handle_batch_error', MagicMock()), \
             patch('asyncio.sleep', AsyncMock()):

            # Set shutdown event to break the loop after one iteration
            logger._shutdown_event.set()
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_io_error(self):
        """Test _process_audit_queue with IOError."""
        logger = AuditLogger()

        # Mock _collect_batch_events to return events
        mock_batch = [{"test": "event"}]
        with patch.object(logger, '_collect_batch_events', AsyncMock(return_value=(mock_batch, True))), \
             patch.object(logger, '_write_audit_batch_with_retry', AsyncMock(side_effect=IOError("IO error"))), \
             patch.object(logger, '_handle_batch_error', MagicMock()), \
             patch('asyncio.sleep', AsyncMock()):

            # Set shutdown event to break the loop after one iteration
            logger._shutdown_event.set()
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_json_error(self):
        """Test _process_audit_queue with JSONDecodeError."""
        logger = AuditLogger()

        # Mock _collect_batch_events to return events
        mock_batch = [{"test": "event"}]
        with patch.object(logger, '_collect_batch_events', AsyncMock(return_value=(mock_batch, True))), \
             patch.object(logger, '_write_audit_batch_with_retry', AsyncMock(side_effect=json.JSONDecodeError("Invalid JSON", "doc", 0))), \
             patch.object(logger, '_handle_batch_error', MagicMock()), \
             patch('asyncio.sleep', AsyncMock()):

            # Set shutdown event to break the loop after one iteration
            logger._shutdown_event.set()
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_unicode_error(self):
        """Test _process_audit_queue with UnicodeError."""
        logger = AuditLogger()

        # Mock _collect_batch_events to return events
        mock_batch = [{"test": "event"}]
        with patch.object(logger, '_collect_batch_events', AsyncMock(return_value=(mock_batch, True))), \
             patch.object(logger, '_write_audit_batch_with_retry', AsyncMock(side_effect=UnicodeError("Unicode error"))), \
             patch.object(logger, '_handle_batch_error', MagicMock()), \
             patch('asyncio.sleep', AsyncMock()):

            # Set shutdown event to break the loop after one iteration
            logger._shutdown_event.set()
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_attribute_error(self):
        """Test _process_audit_queue with AttributeError."""
        logger = AuditLogger()

        # Mock _collect_batch_events to return events
        mock_batch = [{"test": "event"}]
        with patch.object(logger, '_collect_batch_events', AsyncMock(return_value=(mock_batch, True))), \
             patch.object(logger, '_write_audit_batch_with_retry', AsyncMock(side_effect=AttributeError("Attribute error"))), \
             patch.object(logger, '_handle_batch_error', MagicMock()), \
             patch('asyncio.sleep', AsyncMock()):

            # Set shutdown event to break the loop after one iteration
            logger._shutdown_event.set()
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_lookup_error(self):
        """Test _process_audit_queue with LookupError."""
        logger = AuditLogger()

        # Mock _collect_batch_events to return events
        mock_batch = [{"test": "event"}]
        with patch.object(logger, '_collect_batch_events', AsyncMock(return_value=(mock_batch, True))), \
             patch.object(logger, '_write_audit_batch_with_retry', AsyncMock(side_effect=LookupError("Lookup error"))), \
             patch.object(logger, '_handle_batch_error', MagicMock()), \
             patch('asyncio.sleep', AsyncMock()):

            # Set shutdown event to break the loop after one iteration
            logger._shutdown_event.set()
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_final_batch_error(self):
        """Test _process_audit_queue with final batch error."""
        logger = AuditLogger()

        # Mock _collect_batch_events to return events
        mock_batch = [{"test": "event"}]
        with patch.object(logger, '_collect_batch_events', AsyncMock(return_value=(mock_batch, True))), \
             patch.object(logger, '_write_audit_batch_with_retry', AsyncMock(side_effect=DatabaseOperationError("Database error"))), \
             patch.object(logger, '_handle_batch_error', MagicMock()), \
             patch('asyncio.sleep', AsyncMock()):

            # Set shutdown event to break the loop after one iteration
            logger._shutdown_event.set()
            await logger._process_audit_queue()

    def test_handle_batch_error_database(self):
        """Test _handle_batch_error with DatabaseOperationError."""
        logger = AuditLogger()

        # Mock logger.error
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.logger') as mock_logger:

            error = DatabaseOperationError("Database error")
            logger._handle_batch_error(error)

            mock_logger.error.assert_called_once()

    def test_handle_batch_error_system(self):
        """Test _handle_batch_error with system errors."""
        logger = AuditLogger()

        # Mock logger.error
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.logger') as mock_logger:

            error = OSError("OS error")
            logger._handle_batch_error(error)

            mock_logger.error.assert_called_once()

    def test_handle_batch_error_serialization(self):
        """Test _handle_batch_error with serialization errors."""
        logger = AuditLogger()

        # Mock logger.error
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.logger') as mock_logger:

            error = json.JSONDecodeError("Invalid JSON", "doc", 0)
            logger._handle_batch_error(error)

            mock_logger.error.assert_called_once()

    def test_handle_batch_error_data_access(self):
        """Test _handle_batch_error with data access errors."""
        logger = AuditLogger()

        # Mock logger.error
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.logger') as mock_logger:

            error = AttributeError("Attribute error")
            logger._handle_batch_error(error)

            mock_logger.error.assert_called_once()

    def test_handle_batch_error_final(self):
        """Test _handle_batch_error with is_final=True."""
        logger = AuditLogger()

        # Mock logger.error
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.logger') as mock_logger:

            error = DatabaseOperationError("Database error")
            logger._handle_batch_error(error, is_final=True)

            mock_logger.error.assert_called_once()

    def test_handle_write_error(self):
        """Test _handle_write_error."""
        logger = AuditLogger()

        # Mock logger.error
        with patch('apps.user_service.app.dependencies.audit_logs.audit_logger.logger') as mock_logger:

            error = DatabaseOperationError("Database error")
            logger._handle_write_error(error, 0, "database")

            mock_logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_audit_event_dict_comprehensive(self, mock_request):
        """Test _create_audit_event_dict with comprehensive data."""
        logger = AuditLogger()

        # Create comprehensive event data
        event_data = AuditEventData(
            user_context={
                "organization_id": "org123",
                "user_id": "user123",
                "user_email": "test@example.com",
                "user_role": "admin"
            },
            action_type="CREATE",
            data_classification="confidential",
            table_name="users",
            record_id="123",
            old_values={"old": "data"},
            new_values={"new": "data"},
            changed_fields=["name", "email"],
            compliance_tags=["gdpr", "sox"],
            risk_level="medium",
            description="Test event",
            status_code=201,
            category="user_management"
        )

        result = logger._create_audit_event_dict(event_data, mock_request)

        assert result["organization_id"] == "org123"
        assert result["user_id"] == "user123"
        assert result["user_email"] == "test@example.com"
        assert result["user_role"] == "admin"
        assert result["action_type"] == "CREATE"
        assert result["data_classification"] == "confidential"
        assert result["table_name"] == "users"
        assert result["record_id"] == "123"
        assert result["old_values"] == {"old": "data"}
        assert result["new_values"] == {"new": "data"}
        assert result["changed_fields"] == ["name", "email"]
        assert result["compliance_tags"] == ["gdpr", "sox"]
        assert result["risk_level"] == "medium"
        assert result["ip_address"] == "10.0.0.1"
        assert result["description"] == "Test event"
        assert result["status_code"] == 201
        assert result["category"] == "user_management"
        assert "timestamp" in result

    @pytest.mark.asyncio
    async def test_create_audit_event_dict_minimal(self, mock_request):
        """Test _create_audit_event_dict with minimal data."""
        logger = AuditLogger()

        # Create minimal event data
        event_data = AuditEventData(
            user_context={},  # Empty context
            action_type="READ",
            data_classification="public",
            table_name="logs",
            record_id=None,
            old_values=None,
            new_values=None,
            changed_fields=None,
            compliance_tags=[],
            risk_level="low",
            description="Minimal event"
        )

        result = logger._create_audit_event_dict(event_data, mock_request)

        assert result["organization_id"] is None
        assert result["user_id"] is None
        assert result["user_email"] == "unknown"
        assert result["user_role"] == "unknown"
        assert result["action_type"] == "READ"
        assert result["data_classification"] == "public"
        assert result["table_name"] == "logs"
        assert result["record_id"] is None
        assert result["old_values"] is None
        assert result["new_values"] is None
        assert result["changed_fields"] == []
        assert result["compliance_tags"] == []
        assert result["risk_level"] == "low"
        assert result["description"] == "Minimal event"
        assert result["status_code"] is None
        assert result["category"] is None
        assert "timestamp" in result


class TestAuditEventData:
    """Tests for AuditEventData class."""

    @pytest.mark.asyncio
    async def test_audit_event_data_creation(self, mock_user_context):
        """Test creation of AuditEventData with all fields."""
        # Mock async values
        mock_old_values = AsyncMock(return_value={"old": "data"})
        mock_new_values = AsyncMock(return_value={"name": "test"})

        event_data = AuditEventData(
            user_context=mock_user_context,
            action_type="CREATE",
            data_classification="confidential",
            table_name="users",
            record_id="123",
            old_values=await mock_old_values(),
            new_values=await mock_new_values(),
            changed_fields=None,
            compliance_tags=["gdpr"],
            risk_level="low",
            description="Test event",
            status_code=200,
            category="user_management"
        )

        assert event_data.user_context == mock_user_context
        assert event_data.action_type == "CREATE"
        assert event_data.data_classification == "confidential"
        assert event_data.table_name == "users"
        assert event_data.record_id == "123"
        assert event_data.old_values == {"old": "data"}
        assert event_data.new_values == {"name": "test"}
        assert event_data.changed_fields is None
        assert event_data.compliance_tags == ["gdpr"]
        assert event_data.risk_level == "low"
        assert event_data.description == "Test event"
        assert event_data.status_code == 200
        assert event_data.category == "user_management"

        # Verify mocks were called
        mock_old_values.assert_called_once()
        mock_new_values.assert_called_once()

    @pytest.mark.asyncio
    async def test_audit_event_data_with_optional_fields(self, mock_user_context):
        """Test AuditEventData creation with only required fields."""
        event_data = AuditEventData(
            user_context=mock_user_context,
            action_type="READ",
            data_classification="general",
            table_name="users",
            record_id=None,
            old_values=None,
            new_values=None,
            changed_fields=None,
            compliance_tags=[],
            risk_level="low",
            description="Test event"
        )

        assert event_data.record_id is None
        assert event_data.old_values is None
        assert event_data.new_values is None
        assert event_data.changed_fields is None
        assert event_data.compliance_tags == []
        assert event_data.status_code is None
        assert event_data.category is None

    @pytest.mark.asyncio
    async def test_audit_event_data_with_async_values(self, mock_user_context):
        """Test AuditEventData with async value processing."""
        # Mock async data sources
        mock_values = AsyncMock(return_value={
            "old": {"status": "active"},
            "new": {"status": "inactive"},
            "changes": ["status"]
        })

        # Get values asynchronously
        values = await mock_values()

        event_data = AuditEventData(
            user_context=mock_user_context,
            action_type="UPDATE",
            data_classification="confidential",
            table_name="users",
            record_id="123",
            old_values=values["old"],
            new_values=values["new"],
            changed_fields=values["changes"],
            compliance_tags=["gdpr"],
            risk_level="medium",
            description="Status update"
        )

        assert event_data.old_values == {"status": "active"}
        assert event_data.new_values == {"status": "inactive"}
        assert event_data.changed_fields == ["status"]

        # Verify mock was called
        mock_values.assert_called_once()


class TestAuditLogsUtils:
    """Tests for audit logs utility functions."""

    @pytest.mark.asyncio
    async def test_build_audit_logs_filter_message_with_search(self):
        """Test filter message building with search term."""
        # Mock async search term
        mock_search = AsyncMock(return_value="test query")
        search_term = await mock_search()

        message = build_audit_logs_filter_message(
            search=search_term,
            skip=20,
            limit=10
        )
        assert "test query" in message
        assert "10 records" in message
        assert "21" in message  # skip + 1
        mock_search.assert_called_once()

    def test_build_audit_logs_filter_message_without_search(self):
        """Test filter message building without search term."""
        message = build_audit_logs_filter_message(
            skip=0,
            limit=20
        )
        assert "search" not in message
        assert "20 records" in message
        assert "1" in message  # skip + 1

    def test_check_user_access_success(self, mock_user_context):
        """Test successful permission check for viewing audit logs."""
        result = check_audit_logs_view_permission(mock_user_context)
        assert result == mock_user_context

    @pytest.mark.asyncio
    async def test_check_user_access_missing_org_id(self):
        """Test permission check with missing organization ID."""
        invalid_context = {"user_id": "123"}  # Missing organization_id

        with pytest.raises(HTTPException) as exc:
            await check_audit_logs_view_permission(invalid_context)

        assert exc.value.status_code == 400
        assert "missing organization_id" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_format_audit_log_data(self):
        """Test audit log data formatting."""
        # Mock async data retrieval
        mock_data = AsyncMock(return_value={
            "id": uuid.uuid4(),
            "organization_id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": json.dumps({"name": "old"}),
            "new_values": json.dumps({"name": "new"}),
            "changed_fields": json.dumps(["name"]),
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        })

        test_data = await mock_data()
        formatted = format_audit_log_data(test_data)

        assert formatted["old_values"] == {"name": "old"}
        assert formatted["new_values"] == {"name": "new"}
        assert formatted["changed_fields"] == ["name"]
        assert formatted["compliance_tags"] == ["gdpr"]
        assert isinstance(formatted["timestamp"], str)
        mock_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_format_audit_log_detail_data(self):
        """Test detailed audit log data formatting."""
        # Mock async data retrieval
        mock_data = AsyncMock(return_value={
            # Basic fields
            "id": uuid.uuid4(),
            "organization_id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": json.dumps({"name": "old"}),
            "new_values": json.dumps({"name": "new"}),
            "changed_fields": json.dumps(["name"]),
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management",
            # Additional detail fields
            "hash_signature": "abc123",
            "previous_hash": "def456",
            "retention_date": datetime.now(timezone.utc)
        })

        test_data = await mock_data()
        formatted = format_audit_log_detail_data(test_data)

        # Check additional detail fields
        assert formatted["hash_signature"] == "abc123"
        assert formatted["previous_hash"] == "def456"
        assert isinstance(formatted["retention_date"], str)

        # Verify basic fields are still present
        assert formatted["id"]
        assert formatted["organization_id"]
        assert formatted["user_id"]
        mock_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_format_audit_log_data_with_invalid_json(self):
        """Test handling of invalid JSON in audit log data."""
        test_data = {
            "id": uuid.uuid4(),
            "organization_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "user_email": "test@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "123",
            "old_values": "invalid json",
            "new_values": "{also invalid}",
            "changed_fields": "[not valid either]",
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Test event",
            "timestamp": datetime.now(timezone.utc),
            "status_code": 200,
            "category": "user_management"
        }

        formatted = format_audit_log_data(test_data)

        # Should handle invalid JSON gracefully
        assert formatted["old_values"] is None
        assert formatted["new_values"] is None
        assert formatted["changed_fields"] is None
        assert isinstance(formatted["timestamp"], str)
        assert formatted["organization_id"] == test_data["organization_id"]
        assert formatted["user_id"] == test_data["user_id"]


class TestAuditLogsAPI:
    """Tests for audit logs API endpoints."""

    @pytest.fixture
    def app(self):
        """Fixture for FastAPI app with audit logs router."""
        from fastapi import FastAPI
        from apps.user_service.app.api.audit_logs.audit_logs import router as audit_router
        from libs.shared_middleware.jwt_auth import get_user_from_auth
        from apps.user_service.app.dependencies.common_utils import check_user_access_async

        app = FastAPI()
        app.include_router(audit_router, prefix="/v1/admin")
        app.dependency_overrides[get_user_from_auth] = lambda: {"user_id": "u", "organization_id": "o", "email": "e@e.com"}
        app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
        return app

    @pytest.fixture
    def client(self, app):
        """Fixture for test client."""
        return TestClient(app)

    def test_audit_logs_list_success(self, client):
        """Test successful audit logs list API endpoint."""
        with patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list", AsyncMock(return_value=[
            {
                "id": "a1",
                "organization_id": "o",
                "user_id": "u",
                "user_email": "e@e.com",
                "user_role": "Admin",
                "action_type": "CREATE",
                "data_classification": "confidential",
                "table_name": "users",
                "record_id": "r1",
                "old_values": None,
                "new_values": None,
                "changed_fields": None,
                "compliance_tags": ["gdpr"],
                "risk_level": "low",
                "ip_address": 3232235777,
                "description": "created",
                "timestamp": "",
                "status_code": 200,
                "category": "audit_management"
            }
        ])), patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count", AsyncMock(return_value=1)):
            res = client.get("/v1/admin/audit-logs")
            assert res.status_code == 200
            assert res.json()["total_count"] == 1

    def test_audit_log_response_to_dict(self):
        """Test AuditLogResponse.to_dict() method - covers line 74."""
        from apps.user_service.app.api.audit_logs.audit_logs import AuditLogResponse

        response = AuditLogResponse(message="Test message", status="success")
        result = response.to_dict()

        assert result == {"message": "Test message", "status": "success"}

    def test_get_audit_log_by_id_success(self, client):
        """Test successful get audit log by ID API endpoint - covers lines 242-292."""
        valid_uuid = "123e4567-e89b-12d3-a456-426614174000"
        with patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_log_by_id", AsyncMock(return_value={
            "id": valid_uuid,
            "organization_id": "o",
            "user_id": "u",
            "user_email": "e@e.com",
            "user_role": "Admin",
            "action_type": "CREATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "r1",
            "old_values": None,
            "new_values": None,
            "changed_fields": None,
            "compliance_tags": ["gdpr"],
            "risk_level": "low",
            "ip_address": 3232235777,
            "description": "created",
            "timestamp": "",
            "hash_signature": "hash123",
            "previous_hash": "prev_hash123",
            "retention_date": None,
            "status_code": 200,
            "category": "audit_management"
        })):
            res = client.get(f"/v1/admin/audit-logs/{valid_uuid}")
            assert res.status_code == 200
            assert res.json()["audit_log"]["id"] == valid_uuid
            assert res.json()["message"] == "Audit log details retrieved successfully"

    def test_get_audit_log_by_id_not_found(self, client):
        """Test get audit log by ID when not found - covers lines 260-264."""
        valid_uuid = "123e4567-e89b-12d3-a456-426614174001"
        with patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_log_by_id", AsyncMock(return_value=None)):
            res = client.get(f"/v1/admin/audit-logs/{valid_uuid}")
            assert res.status_code == 404
            assert "Audit log not found" in res.json()["detail"]

    def test_get_audit_log_by_id_invalid_uuid(self, client):
        """Test get audit log by ID with invalid UUID format - covers lines 242-243."""
        res = client.get("/v1/admin/audit-logs/invalid-uuid")
        assert res.status_code == 400
        assert "Invalid audit log ID format" in res.json()["detail"]

    def test_delete_all_audit_logs_success(self, client):
        """Test successful delete all audit logs API endpoint - covers lines 369-371."""
        with patch("apps.user_service.app.api.audit_logs.audit_logs.delete_all_audit_logs", AsyncMock(return_value=5)):
            res = client.delete("/v1/admin/audit-logs")
            assert res.status_code == 204

    def test_delete_all_audit_logs_zero_count(self, client):
        """Test delete all audit logs when no logs exist - covers lines 369-371."""
        with patch("apps.user_service.app.api.audit_logs.audit_logs.delete_all_audit_logs", AsyncMock(return_value=0)):
            res = client.delete("/v1/admin/audit-logs")
            assert res.status_code == 204

    # Additional comprehensive test cases for maximum coverage
    def test_audit_logs_list_with_search(self, client):
        """Test audit logs list with search parameter."""
        with patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list", AsyncMock(return_value=[])), \
             patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count", AsyncMock(return_value=0)):
            res = client.get("/v1/admin/audit-logs?search=test")
            assert res.status_code == 200
            assert res.json()["total_count"] == 0

    def test_audit_logs_list_with_pagination(self, client):
        """Test audit logs list with pagination parameters."""
        with patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list", AsyncMock(return_value=[])), \
             patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count", AsyncMock(return_value=0)):
            res = client.get("/v1/admin/audit-logs?skip=10&limit=5")
            assert res.status_code == 200
            assert res.json()["total_count"] == 0

    def test_audit_logs_list_empty_result(self, client):
        """Test audit logs list with empty result."""
        with patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list", AsyncMock(return_value=[])), \
             patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count", AsyncMock(return_value=0)):
            res = client.get("/v1/admin/audit-logs")
            assert res.status_code == 200
            assert res.json()["total_count"] == 0
            assert res.json()["audit_logs"] == []

    def test_audit_logs_list_with_complex_data(self, client):
        """Test audit logs list with complex JSON data."""
        from datetime import datetime

        complex_data = {
            "id": "123e4567-e89b-12d3-a456-426614174002",
            "organization_id": "org-123",
            "user_id": "user-456",
            "user_email": "test@example.com",
            "user_role": "Admin",
            "action_type": "UPDATE",
            "data_classification": "confidential",
            "table_name": "users",
            "record_id": "record-789",
            "old_values": '{"name": "old", "email": "old@test.com"}',
            "new_values": '{"name": "new", "email": "new@test.com"}',
            "changed_fields": '["name", "email"]',
            "compliance_tags": ["gdpr", "sox"],
            "risk_level": "medium",
            "ip_address": 3232235777,
            "description": "User profile updated",
            "timestamp": datetime(2024, 1, 1, 0, 0, 0),
            "status_code": 200,
            "category": "user_management"
        }

        with patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list", AsyncMock(return_value=[complex_data])), \
             patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count", AsyncMock(return_value=1)):
            res = client.get("/v1/admin/audit-logs")
            assert res.status_code == 200
            assert res.json()["total_count"] == 1
            audit_log = res.json()["audit_logs"][0]
            assert audit_log["old_values"] == {"name": "old", "email": "old@test.com"}
            assert audit_log["new_values"] == {"name": "new", "email": "new@test.com"}
            assert audit_log["changed_fields"] == ["name", "email"]

    def test_get_audit_log_by_id_with_complex_data(self, client):
        """Test get audit log by ID with complex data including JSON fields."""
        from datetime import datetime

        valid_uuid = "123e4567-e89b-12d3-a456-426614174003"
        complex_data = {
            "id": valid_uuid,
            "organization_id": "org-456",
            "user_id": "user-789",
            "user_email": "admin@example.com",
            "user_role": "SuperAdmin",
            "action_type": "DELETE",
            "data_classification": "restricted",
            "table_name": "sensitive_data",
            "record_id": "record-999",
            "old_values": '{"sensitive": "data", "nested": {"key": "value"}}',
            "new_values": None,
            "changed_fields": '["sensitive", "nested"]',
            "compliance_tags": ["pci", "hipaa", "gdpr"],
            "risk_level": "high",
            "ip_address": 3232235778,
            "description": "Sensitive data deleted",
            "timestamp": datetime(2024, 1, 2, 12, 30, 0),
            "hash_signature": "complex_hash_123",
            "previous_hash": "previous_complex_hash_456",
            "retention_date": datetime(2025, 1, 2, 12, 30, 0),
            "status_code": 204,
            "category": "data_management"
        }

        with patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_log_by_id", AsyncMock(return_value=complex_data)):
            res = client.get(f"/v1/admin/audit-logs/{valid_uuid}")
            assert res.status_code == 200
            audit_log = res.json()["audit_log"]
            assert audit_log["id"] == valid_uuid
            assert audit_log["old_values"] == {"sensitive": "data", "nested": {"key": "value"}}
            assert audit_log["new_values"] is None
            assert audit_log["changed_fields"] == ["sensitive", "nested"]
            assert audit_log["retention_date"] == "2025-01-02T12:30:00"

    def test_get_audit_log_by_id_with_none_timestamp(self, client):
        """Test get audit log by ID with None timestamp."""
        valid_uuid = "123e4567-e89b-12d3-a456-426614174004"
        data_with_none_timestamp = {
            "id": valid_uuid,
            "organization_id": "org-789",
            "user_id": "user-123",
            "user_email": "test@example.com",
            "user_role": "User",
            "action_type": "READ",
            "data_classification": "public",
            "table_name": "public_data",
            "record_id": "record-111",
            "old_values": None,
            "new_values": None,
            "changed_fields": None,
            "compliance_tags": [],
            "risk_level": "low",
            "ip_address": 3232235779,
            "description": "Public data accessed",
            "timestamp": None,
            "hash_signature": "simple_hash",
            "previous_hash": "prev_simple_hash",
            "retention_date": None,
            "status_code": 200,
            "category": "data_access"
        }

        with patch("apps.user_service.app.api.audit_logs.audit_logs.get_audit_log_by_id", AsyncMock(return_value=data_with_none_timestamp)):
            res = client.get(f"/v1/admin/audit-logs/{valid_uuid}")
            assert res.status_code == 200
            audit_log = res.json()["audit_log"]
            assert audit_log["timestamp"] == ""
            assert audit_log["retention_date"] is None

    def test_delete_all_audit_logs_large_count(self, client):
        """Test delete all audit logs with large count."""
        with patch("apps.user_service.app.api.audit_logs.audit_logs.delete_all_audit_logs", AsyncMock(return_value=1000)):
            res = client.delete("/v1/admin/audit-logs")
            assert res.status_code == 204

    def test_audit_log_response_default_status(self):
        """Test AuditLogResponse with default status."""
        from apps.user_service.app.api.audit_logs.audit_logs import AuditLogResponse

        response = AuditLogResponse(message="Test message")
        result = response.to_dict()

        assert result == {"message": "Test message", "status": "success"}

    def test_audit_log_response_custom_status(self):
        """Test AuditLogResponse with custom status."""
        from apps.user_service.app.api.audit_logs.audit_logs import AuditLogResponse

        response = AuditLogResponse(message="Error occurred", status="error")
        result = response.to_dict()

        assert result == {"message": "Error occurred", "status": "error"}
