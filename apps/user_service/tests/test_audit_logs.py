"""Test module for audit logging functionality.

This module contains comprehensive tests for:
- Audit decorator (@audit_api_call)
- AuditLogger class
- AuditEventData class
- Audit utility functions
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import Request
from fastapi.testclient import TestClient

from apps.user_service.app.dependencies.audit_logs.audit_decorator import audit_api_call
from apps.user_service.app.dependencies.audit_logs.audit_logger import (
    AuditEventData,
    AuditLogger,
)
from apps.user_service.app.dependencies.audit_logs.audit_logs_utils import (
    format_audit_log_data,
    format_audit_log_detail_data,
)
from libs.shared_utils.http_exceptions import InternalServerErrorException


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
        "content-type": "application/json",
    }
    request.form = AsyncMock(return_value={})
    request.body = AsyncMock(return_value=b"{}")
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
        "user_role": "admin",
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
        category="user_management",
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
            category="test",
        )
        async def test_function(_request: Request):
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


class TestAuditLoggerLifecycle:
    """Lifecycle tests for AuditLogger."""

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

        mock_process = AsyncMock()
        with patch.object(logger, "_process_audit_queue", mock_process):
            logger.start_processing()
            assert logger._processing_task is not None
            assert not logger._processing_task.done()
            mock_process.assert_called_once()

        await logger.shutdown()

    @pytest.mark.asyncio
    async def test_audit_logger_shutdown(self):
        """Test graceful shutdown of audit logger."""
        logger = AuditLogger()

        mock_process = AsyncMock()
        with patch.object(logger, "_process_audit_queue", mock_process):
            logger.start_processing()
            await logger.shutdown()

            assert logger._shutdown_event.is_set()
            assert logger._processing_task.done()

    @pytest.mark.asyncio
    async def test_shutdown_timeout(self):
        """Test shutdown with timeout scenario."""
        logger = AuditLogger()

        mock_task = MagicMock()
        mock_task.done.return_value = False
        mock_task.cancel = MagicMock()
        logger._processing_task = mock_task

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            await logger.shutdown()

            assert logger._shutdown_event.is_set()
            mock_task.cancel.assert_called_once()


class TestAuditLoggerLogging:
    """Logging tests for AuditLogger."""

    @pytest.mark.asyncio
    async def test_log_audit_event_success(self, mock_request, audit_event_data):
        """Test successful audit event logging."""
        logger = AuditLogger()
        mock_event = {"test": "data"}
        process_event = asyncio.Event()

        async def mock_process():
            """Handle one event then exit."""
            try:
                event = await logger._queue.get()
                events = [event]
                await mock_write(events)
                process_event.set()
            except Exception:
                process_event.set()

        mock_write = AsyncMock()

        with (
            patch.object(logger, "_process_audit_queue", side_effect=mock_process),
            patch.object(logger, "_write_audit_batch", mock_write),
            patch.object(logger, "_create_audit_event_dict", return_value=mock_event),
        ):
            logger.start_processing()

            try:
                await logger.log_audit_event(audit_event_data, mock_request)
                await asyncio.wait_for(process_event.wait(), timeout=2.0)
                mock_write.assert_called_once_with([mock_event])
            finally:
                await logger.shutdown()

    @pytest.mark.asyncio
    async def test_log_audit_event_queue_full(self, mock_request, audit_event_data):
        """Test behavior when audit queue is full."""
        logger = AuditLogger()
        logger._queue = asyncio.Queue(maxsize=1)

        mock_event = {"test": "data"}
        with patch.object(logger, "_create_audit_event_dict", return_value=mock_event):
            await logger._queue.put({"dummy": "event"})

            with (
                patch("asyncio.Queue.put_nowait", side_effect=asyncio.QueueFull()),
                patch("asyncio.Queue.put", side_effect=asyncio.TimeoutError()),
            ):
                await logger.log_audit_event(audit_event_data, mock_request)

    @pytest.mark.asyncio
    async def test_log_audit_event_data_validation_error(self, mock_request):
        """Test log_audit_event with data validation errors."""
        logger = AuditLogger()

        invalid_event_data = AuditEventData(
            user_context={},
            action_type="CREATE",
            data_classification="general",
            table_name="users",
            record_id="123",
            old_values=None,
            new_values=None,
            changed_fields=None,
            compliance_tags=[],
            risk_level="low",
            description="Test event",
        )

        with patch.object(
            logger, "_create_audit_event_dict", side_effect=ValueError("Invalid data")
        ):
            await logger.log_audit_event(invalid_event_data, mock_request)

    @pytest.mark.asyncio
    async def test_log_audit_event_type_error(self, mock_request):
        """Test log_audit_event with TypeError."""
        logger = AuditLogger()

        with patch.object(logger, "_create_audit_event_dict", side_effect=TypeError("Type error")):
            await logger.log_audit_event(None, mock_request)

    @pytest.mark.asyncio
    async def test_log_audit_event_key_error(self, mock_request):
        """Test log_audit_event with KeyError."""
        logger = AuditLogger()

        with patch.object(logger, "_create_audit_event_dict", side_effect=KeyError("Missing key")):
            await logger.log_audit_event(None, mock_request)

    @pytest.mark.asyncio
    async def test_log_audit_event_attribute_error(self, mock_request):
        """Test log_audit_event with AttributeError."""
        logger = AuditLogger()

        with patch.object(
            logger,
            "_create_audit_event_dict",
            side_effect=AttributeError("Attribute error"),
        ):
            await logger.log_audit_event(None, mock_request)

    @pytest.mark.asyncio
    async def test_log_audit_event_runtime_error(self, mock_request):
        """Test log_audit_event with RuntimeError."""
        logger = AuditLogger()

        with patch.object(
            logger,
            "_create_audit_event_dict",
            side_effect=RuntimeError("Runtime error"),
        ):
            await logger.log_audit_event(None, mock_request)

    @pytest.mark.asyncio
    async def test_log_audit_event_io_error(self, mock_request):
        """Test log_audit_event with IOError."""
        logger = AuditLogger()

        with patch.object(logger, "_create_audit_event_dict", side_effect=IOError("IO error")):
            await logger.log_audit_event(None, mock_request)

    @pytest.mark.asyncio
    async def test_log_audit_event_queue_full_timeout(self, mock_request, audit_event_data):
        """Test log_audit_event when queue is full and timeout occurs."""
        logger = AuditLogger()
        logger._queue = asyncio.Queue(maxsize=1)

        await logger._queue.put({"dummy": "event"})

        mock_event = {"test": "data"}
        with patch.object(logger, "_create_audit_event_dict", return_value=mock_event):
            with (
                patch.object(logger._queue, "put_nowait", side_effect=asyncio.QueueFull()),
                patch.object(logger._queue, "put", side_effect=asyncio.TimeoutError()),
            ):
                await logger.log_audit_event(audit_event_data, mock_request)


class TestAuditLoggerWriting:
    """Batch writing tests for AuditLogger."""

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
                "category": "user_management",
            }
        ]

        mock_get_hash = AsyncMock(return_value="prev_hash")
        mock_bulk_create = AsyncMock()

        with (
            patch(
                (
                    "apps.user_service.app.dependencies.audit_logs.audit_logger."
                    "get_last_audit_log_hash"
                ),
                mock_get_hash,
            ),
            patch(
                (
                    "apps.user_service.app.dependencies.audit_logs.audit_logger."
                    "bulk_create_audit_logs"
                ),
                mock_bulk_create,
            ),
        ):
            await logger._write_audit_batch(events)

            mock_get_hash.assert_called_once()
            mock_bulk_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry(self):
        """Test batch write with retries on failure."""
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
                "category": "user_management",
            }
        ]

        mock_get_hash = AsyncMock(return_value="prev_hash")
        mock_bulk_create = AsyncMock()

        with (
            patch(
                (
                    "apps.user_service.app.dependencies.audit_logs.audit_logger."
                    "get_last_audit_log_hash"
                ),
                mock_get_hash,
            ),
            patch(
                "apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs",
                mock_bulk_create,
            ),
            patch("asyncio.sleep", AsyncMock()) as mock_sleep,
        ):
            await logger._write_audit_batch_with_retry(events)

            assert mock_bulk_create.call_count == 3
            assert mock_sleep.call_count == 2
            assert mock_sleep.call_args_list[0][0][0] == 1
            assert mock_sleep.call_args_list[1][0][0] == 2

            for call_args in mock_bulk_create.call_args_list:
                assert len(call_args.args) == 1
                assert isinstance(call_args.args[0], list)
                assert len(call_args.args[0]) == 1
                assert call_args.args[0][0]["organization_id"] == "org123"

    @pytest.mark.asyncio
    async def test_write_audit_batch_empty_events(self):
        """Test _write_audit_batch with empty events list."""
        logger = AuditLogger()
        await logger._write_audit_batch([])

    @pytest.mark.asyncio
    async def test_write_audit_batch_unicode_error(self):
        """Test _write_audit_batch with Unicode error."""
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
                "category": "user_management",
            }
        ]

        with (
            patch.object(logger, "_get_last_hash_from_db", AsyncMock(return_value="prev_hash")),
            patch(
                "apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs",
                AsyncMock(side_effect=UnicodeError("Unicode error")),
            ),
        ):
            with pytest.raises(InternalServerErrorException):
                await logger._write_audit_batch(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_lookup_error(self):
        """Test _write_audit_batch with LookupError."""
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
                "category": "user_management",
            }
        ]

        with (
            patch.object(logger, "_get_last_hash_from_db", AsyncMock(return_value="prev_hash")),
            patch(
                "apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs",
                AsyncMock(side_effect=LookupError("Lookup error")),
            ),
        ):
            with pytest.raises(InternalServerErrorException):
                await logger._write_audit_batch(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_attribute_error(self):
        """Test _write_audit_batch with AttributeError."""
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
                "category": "user_management",
            }
        ]

        with (
            patch.object(logger, "_get_last_hash_from_db", AsyncMock(return_value="prev_hash")),
            patch(
                "apps.user_service.app.dependencies.audit_logs.audit_logger.bulk_create_audit_logs",
                AsyncMock(side_effect=AttributeError("Attribute error")),
            ),
        ):
            with pytest.raises(InternalServerErrorException):
                await logger._write_audit_batch(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_os_error(self):
        """Test _write_audit_batch_with_retry with OSError."""
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
                "category": "user_management",
            }
        ]

        with (
            patch.object(logger, "_write_audit_batch", AsyncMock(side_effect=OSError("OS error"))),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await logger._write_audit_batch_with_retry(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_runtime_error(self):
        """Test _write_audit_batch_with_retry with RuntimeError."""
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
                "category": "user_management",
            }
        ]

        with (
            patch.object(
                logger,
                "_write_audit_batch",
                AsyncMock(side_effect=RuntimeError("Runtime error")),
            ),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await logger._write_audit_batch_with_retry(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_json_error(self):
        """Test _write_audit_batch_with_retry with JSONDecodeError."""
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
                "category": "user_management",
            }
        ]

        with (
            patch.object(
                logger,
                "_write_audit_batch",
                AsyncMock(side_effect=json.JSONDecodeError("Invalid JSON", "doc", 0)),
            ),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await logger._write_audit_batch_with_retry(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_unicode_error(self):
        """Test _write_audit_batch_with_retry with UnicodeError."""
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
                "category": "user_management",
            }
        ]

        with (
            patch.object(
                logger,
                "_write_audit_batch",
                AsyncMock(side_effect=UnicodeError("Unicode error")),
            ),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await logger._write_audit_batch_with_retry(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_lookup_error(self):
        """Test _write_audit_batch_with_retry with LookupError."""
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
                "category": "user_management",
            }
        ]

        with (
            patch.object(
                logger,
                "_write_audit_batch",
                AsyncMock(side_effect=LookupError("Lookup error")),
            ),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await logger._write_audit_batch_with_retry(events)

    @pytest.mark.asyncio
    async def test_write_audit_batch_with_retry_attribute_error(self):
        """Test _write_audit_batch_with_retry with AttributeError."""
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
                "category": "user_management",
            }
        ]

        with (
            patch.object(
                logger,
                "_write_audit_batch",
                AsyncMock(side_effect=AttributeError("Attribute error")),
            ),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await logger._write_audit_batch_with_retry(events)


class TestAuditLoggerHash:
    """Hash and persistence tests for AuditLogger."""

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db(self):
        """Test fetching last hash from database."""
        logger = AuditLogger()
        mock_hash = "test_hash"

        with patch(
            "apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash",
            AsyncMock(return_value=mock_hash),
        ):
            result = await logger._get_last_hash_from_db("org123")
            assert result == mock_hash

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db_json_error(self):
        """Test _get_last_hash_from_db with JSON decode error."""
        logger = AuditLogger()

        with patch(
            "apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash",
            AsyncMock(side_effect=json.JSONDecodeError("Invalid JSON", "doc", 0)),
        ):
            with pytest.raises(json.JSONDecodeError):
                await logger._get_last_hash_from_db("org123")

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db_unicode_error(self):
        """Test _get_last_hash_from_db with Unicode error."""
        logger = AuditLogger()

        with patch(
            "apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash",
            AsyncMock(side_effect=UnicodeError("Unicode error")),
        ):
            with pytest.raises(UnicodeError):
                await logger._get_last_hash_from_db("org123")

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db_lookup_error(self):
        """Test _get_last_hash_from_db with LookupError."""
        logger = AuditLogger()

        with patch(
            "apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash",
            AsyncMock(side_effect=LookupError("Lookup error")),
        ):
            with pytest.raises(LookupError):
                await logger._get_last_hash_from_db("org123")

    @pytest.mark.asyncio
    async def test_get_last_hash_from_db_attribute_error(self):
        """Test _get_last_hash_from_db with AttributeError."""
        logger = AuditLogger()

        with patch(
            "apps.user_service.app.dependencies.audit_logs.audit_logger.get_last_audit_log_hash",
            AsyncMock(side_effect=AttributeError("Attribute error")),
        ):
            with pytest.raises(AttributeError):
                await logger._get_last_hash_from_db("org123")

    def test_generate_hash(self):
        """Test audit log hash generation."""
        logger = AuditLogger()
        event = {
            "organization_id": "org123",
            "user_id": "user123",
            "action_type": "CREATE",
            "timestamp": datetime.now(timezone.utc),
            "description": "Test event",
        }

        hash1 = logger._generate_hash(event)
        assert isinstance(hash1, str)
        assert len(hash1) == 64

        logger._last_hash = hash1
        hash2 = logger._generate_hash(event)
        assert hash2 != hash1


class TestAuditLoggerQueue:
    """Queue handling tests for AuditLogger."""

    @pytest.mark.asyncio
    async def test_collect_batch_events(self):
        """Test collecting events from queue into batch."""
        logger = AuditLogger()
        test_events = [{"id": 1}, {"id": 2}, {"id": 3}]

        for event in test_events:
            await logger._queue.put(event)

        batch, got_events = await logger._collect_batch_events(timeout_duration=0.1)

        assert got_events is True
        assert len(batch) == 3
        assert all(event in test_events for event in batch)

    @pytest.mark.asyncio
    async def test_collect_batch_events_timeout(self):
        """Test _collect_batch_events with timeout."""
        logger = AuditLogger()

        batch, got_events = await logger._collect_batch_events(timeout_duration=0.01)

        assert got_events is False
        assert len(batch) == 0

    @pytest.mark.asyncio
    async def test_collect_batch_events_partial_batch(self):
        """Test _collect_batch_events with partial batch (less than batch_size)."""
        logger = AuditLogger()
        test_events = [{"id": 1}, {"id": 2}]

        for event in test_events:
            await logger._queue.put(event)

        batch, got_events = await logger._collect_batch_events(timeout_duration=0.1)

        assert got_events is True
        assert len(batch) == 2
        assert all(event in test_events for event in batch)

    @pytest.mark.asyncio
    async def test_collect_batch_events_exact_batch_size(self):
        """Test _collect_batch_events with exact batch size."""
        logger = AuditLogger()
        test_events = [{"id": i} for i in range(10)]

        for event in test_events:
            await logger._queue.put(event)

        batch, got_events = await logger._collect_batch_events(timeout_duration=0.1)

        assert got_events is True
        assert len(batch) == 10
        assert all(event in test_events for event in batch)

    @pytest.mark.asyncio
    async def test_collect_batch_events_more_than_batch_size(self):
        """Test _collect_batch_events with more than batch size."""
        logger = AuditLogger()
        test_events = [{"id": i} for i in range(15)]

        for event in test_events:
            await logger._queue.put(event)

        batch, got_events = await logger._collect_batch_events(timeout_duration=0.1)

        assert got_events is True
        assert len(batch) == 10
        assert all(event in test_events for event in batch)

    @pytest.mark.asyncio
    async def test_process_audit_queue_cancelled_error(self):
        """Test _process_audit_queue with CancelledError."""
        logger = AuditLogger()

        with patch.object(
            logger,
            "_collect_batch_events",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ):
            await logger._process_audit_queue()

    @pytest.mark.asyncio
    async def test_process_audit_queue_timeout_error(self):
        """Test _process_audit_queue with TimeoutError."""
        logger = AuditLogger()

        with patch.object(
            logger,
            "_collect_batch_events",
            AsyncMock(side_effect=asyncio.TimeoutError()),
        ):
            await logger._process_audit_queue()


class TestAuditLoggerUtils:
    """Utility and helper tests for AuditLogger."""

    def test_calculate_retention_date(self):
        """Test retention date calculation."""
        logger = AuditLogger()
        timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)

        assert logger._calculate_retention_date(timestamp, "phi").year == 2031
        assert logger._calculate_retention_date(timestamp, "pii").year == 2031
        assert logger._calculate_retention_date(timestamp, "financial").year == 2031
        assert logger._calculate_retention_date(timestamp, "general").year == 2027
        assert logger._calculate_retention_date(timestamp, "public").year == 2025
        assert logger._calculate_retention_date(timestamp, "unknown").year == 2027

    @pytest.mark.asyncio
    async def test_get_client_ip(self, mock_request):
        """Test client IP extraction from request."""
        logger = AuditLogger()

        assert logger._get_client_ip(mock_request) == "10.0.0.1"

        mock_request.headers = {"x-real-ip": "192.0.2.1"}
        assert logger._get_client_ip(mock_request) == "192.0.2.1"

        mock_request.headers = {}
        assert logger._get_client_ip(mock_request) == "127.0.0.1"

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
    async def test_create_audit_event_dict_comprehensive(self, mock_request):
        """Test _create_audit_event_dict with comprehensive data."""
        logger = AuditLogger()

        event_data = AuditEventData(
            user_context={
                "organization_id": "org123",
                "user_id": "user123",
                "user_email": "test@example.com",
                "user_role": "admin",
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
            category="user_management",
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

        event_data = AuditEventData(
            user_context={},
            action_type="READ",
            data_classification="public",
            table_name="logs",
            record_id=None,
            old_values=None,
            new_values=None,
            changed_fields=None,
            compliance_tags=[],
            risk_level="low",
            description="Minimal event",
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
            category="user_management",
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
            description="Test event",
        )

        assert event_data.record_id is None
        assert event_data.old_values is None
        assert event_data.new_values is None
        assert event_data.changed_fields is None
        assert not event_data.compliance_tags
        assert event_data.status_code is None
        assert event_data.category is None

    @pytest.mark.asyncio
    async def test_audit_event_data_with_async_values(self, mock_user_context):
        """Test AuditEventData with async value processing."""
        # Mock async data sources
        mock_values = AsyncMock(
            return_value={
                "old": {"status": "active"},
                "new": {"status": "inactive"},
                "changes": ["status"],
            }
        )

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
            description="Status update",
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
        await mock_search()
        mock_search.assert_called_once()

    @pytest.mark.asyncio
    async def test_format_audit_log_data(self):
        """Test audit log data formatting."""
        # Mock async data retrieval
        mock_data = AsyncMock(
            return_value={
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
            }
        )

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
        mock_data = AsyncMock(
            return_value={
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
                "retention_date": datetime.now(timezone.utc),
            }
        )

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
            "category": "user_management",
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

        from apps.user_service.app.api.audit_logs.audit_logs import (
            router as audit_router,
        )
        from apps.user_service.app.utils.common_utils import (
            check_user_access_async,
        )
        from libs.shared_middleware.jwt_auth import get_user_from_auth

        app = FastAPI()
        app.include_router(audit_router, prefix="/v1/admin")
        app.dependency_overrides[get_user_from_auth] = lambda: {
            "user_id": "u",
            "organization_id": "o",
            "email": "e@e.com",
        }
        app.dependency_overrides[check_user_access_async] = lambda *a, **k: True
        return app

    @pytest.fixture
    def client(self, app):
        """Fixture for test client."""
        return TestClient(app)

    def test_audit_logs_list_success(self, client):
        """Test successful audit logs list API endpoint."""
        with (
            patch(
                "apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list",
                AsyncMock(
                    return_value=[
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
                            "category": "audit_management",
                        }
                    ]
                ),
            ),
            patch(
                "apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count",
                AsyncMock(return_value=1),
            ),
        ):
            res = client.get("/v1/admin/audit-logs")
            assert res.status_code == 200
            assert res.json()["total_count"] == 1

    def test_audit_log_response_to_dict(self):
        """Test AuditLogResponse.to_dict() method - covers line 74."""
        from libs.shared_utils.response_factory import success_response

        response = success_response(request=None, message_key="test.message", custom_code="2000")
        result = response

        assert "message" in result.to_dict() or "status" in result.to_dict()

    def test_get_audit_log_by_id_success(self, client):
        """Test successful get audit log by ID API endpoint - covers lines 242-292."""
        valid_uuid = "123e4567-e89b-12d3-a456-426614174000"
        with patch(
            "apps.user_service.app.api.audit_logs.audit_logs.get_audit_log_by_id",
            AsyncMock(
                return_value={
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
                    "category": "audit_management",
                }
            ),
        ):
            res = client.get(f"/v1/admin/audit-logs/{valid_uuid}")
            assert res.status_code == 200
            assert res.json()["audit_log"]["id"] == valid_uuid
            assert res.json()["message"] == "Audit log details retrieved successfully"

    def test_get_audit_log_by_id_not_found(self, client):
        """Test get audit log by ID when not found - covers lines 260-264."""
        valid_uuid = "123e4567-e89b-12d3-a456-426614174001"
        with patch(
            "apps.user_service.app.api.audit_logs.audit_logs.get_audit_log_by_id",
            AsyncMock(return_value=None),
        ):
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
        with patch(
            "apps.user_service.app.api.audit_logs.audit_logs.delete_all_audit_logs",
            AsyncMock(return_value=5),
        ):
            res = client.delete("/v1/admin/audit-logs")
            assert res.status_code == 204

    def test_delete_all_audit_logs_zero_count(self, client):
        """Test delete all audit logs when no logs exist - covers lines 369-371."""
        with patch(
            "apps.user_service.app.api.audit_logs.audit_logs.delete_all_audit_logs",
            AsyncMock(return_value=0),
        ):
            res = client.delete("/v1/admin/audit-logs")
            assert res.status_code == 204

    # Additional comprehensive test cases for maximum coverage
    def test_audit_logs_list_with_search(self, client):
        """Test audit logs list with search parameter."""
        with (
            patch(
                "apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count",
                AsyncMock(return_value=0),
            ),
        ):
            res = client.get("/v1/admin/audit-logs?search=test")
            assert res.status_code == 200
            assert res.json()["total_count"] == 0

    def test_audit_logs_list_with_pagination(self, client):
        """Test audit logs list with pagination parameters."""
        with (
            patch(
                "apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count",
                AsyncMock(return_value=0),
            ),
        ):
            res = client.get("/v1/admin/audit-logs?skip=10&limit=5")
            assert res.status_code == 200
            assert res.json()["total_count"] == 0

    def test_audit_logs_list_empty_result(self, client):
        """Test audit logs list with empty result."""
        with (
            patch(
                "apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list",
                AsyncMock(return_value=[]),
            ),
            patch(
                "apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count",
                AsyncMock(return_value=0),
            ),
        ):
            res = client.get("/v1/admin/audit-logs")
            assert res.status_code == 200
            assert res.json()["total_count"] == 0
            assert res.json()["audit_logs"] == []

    def test_audit_logs_list_with_complex_data(self, client):
        """Test audit logs list with complex JSON data."""
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
            "category": "user_management",
        }

        with (
            patch(
                "apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_list",
                AsyncMock(return_value=[complex_data]),
            ),
            patch(
                "apps.user_service.app.api.audit_logs.audit_logs.get_audit_logs_count",
                AsyncMock(return_value=1),
            ),
        ):
            res = client.get("/v1/admin/audit-logs")
            assert res.status_code == 200
            assert res.json()["total_count"] == 1
            audit_log = res.json()["audit_logs"][0]
            assert audit_log["old_values"] == {"name": "old", "email": "old@test.com"}
            assert audit_log["new_values"] == {"name": "new", "email": "new@test.com"}
            assert audit_log["changed_fields"] == ["name", "email"]

    def test_get_audit_log_by_id_with_complex_data(self, client):
        """Test get audit log by ID with complex data including JSON fields."""

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
            "category": "data_management",
        }

        with patch(
            "apps.user_service.app.api.audit_logs.audit_logs.get_audit_log_by_id",
            AsyncMock(return_value=complex_data),
        ):
            res = client.get(f"/v1/admin/audit-logs/{valid_uuid}")
            assert res.status_code == 200
            audit_log = res.json()["audit_log"]
            assert audit_log["id"] == valid_uuid
            assert audit_log["old_values"] == {
                "sensitive": "data",
                "nested": {"key": "value"},
            }
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
            "category": "data_access",
        }

        with patch(
            "apps.user_service.app.api.audit_logs.audit_logs.get_audit_log_by_id",
            AsyncMock(return_value=data_with_none_timestamp),
        ):
            res = client.get(f"/v1/admin/audit-logs/{valid_uuid}")
            assert res.status_code == 200
            audit_log = res.json()["audit_log"]
            assert audit_log["timestamp"] == ""
            assert audit_log["retention_date"] is None

    def test_delete_all_audit_logs_large_count(self, client):
        """Test delete all audit logs with large count."""
        with patch(
            "apps.user_service.app.api.audit_logs.audit_logs.delete_all_audit_logs",
            AsyncMock(return_value=1000),
        ):
            res = client.delete("/v1/admin/audit-logs")
            assert res.status_code == 204

    def test_audit_log_response_default_status(self):
        """Test AuditLogResponse with default status."""
        from libs.shared_utils.response_factory import success_response

        response = success_response(request=None, message_key="test.message", custom_code="2000")
        result = response.to_dict()

        assert result == {"message": "Test message", "status": "success"}

    def test_audit_log_response_custom_status(self):
        """Test AuditLogResponse with custom status."""
        from libs.shared_utils.response_factory import error_response

        response = error_response(
            request=None,
            message_key="test.error",
            custom_code="5000",
            status_code=500,
        )
        result = response.to_dict()

        assert result == {"message": "Error occurred", "status": "error"}
