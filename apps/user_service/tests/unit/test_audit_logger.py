"""Unit tests for AuditLogger with mocked DB and queue."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.dependencies.audit_logs.audit_logger import (
    AuditEventData,
    AuditLogger,
)
from libs.shared_utils.http_exceptions import InternalServerErrorException

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
USER_ID = "660e8400-e29b-41d4-a716-446655440001"


def _request(*, headers=None, client_host="192.168.1.1"):
    """Build minimal FastAPI Request for IP extraction tests."""
    hdrs = headers or []
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs],
        "client": (client_host, 12345) if client_host else None,
    }
    return Request(scope)


def _event_data(**overrides) -> AuditEventData:
    """Build AuditEventData with defaults."""
    base = AuditEventData(
        user_context={
            "organization_id": ORG_ID,
            "user_id": USER_ID,
            "user_email": "user@example.com",
            "user_role": "admin",
        },
        action_type="UPDATE",
        data_classification="general",
        table_name="leads",
        record_id="rec-1",
        old_values={"name": "Old"},
        new_values={"name": "New"},
        changed_fields=["name"],
        compliance_tags=["gdpr"],
        risk_level="low",
        description="Updated lead",
        status_code=200,
        category="crm",
    )
    for key, val in overrides.items():
        setattr(base, key, val)
    return base


@pytest.fixture
def logger():
    """Fresh AuditLogger instance per test."""
    return AuditLogger()


def test_create_audit_event_dict(logger):
    """_create_audit_event_dict maps event fields and client IP."""
    event = logger._create_audit_event_dict(  # pylint: disable=protected-access
        _event_data(),
        _request(headers=[("X-Forwarded-For", "10.0.0.1, 10.0.0.2")]),
    )

    assert event["organization_id"] == ORG_ID
    assert event["user_email"] == "user@example.com"
    assert event["ip_address"] == "10.0.0.1"
    assert event["changed_fields"] == ["name"]
    assert isinstance(event["timestamp"], datetime)


def test_get_client_ip_real_ip(logger):
    """x-real-ip header is used when forwarded-for absent."""
    ip = logger._get_client_ip(_request(headers=[("X-Real-IP", "203.0.113.5")]))  # pylint: disable=protected-access
    assert ip == "203.0.113.5"


def test_get_client_ip_unknown_when_no_client(logger):
    """Missing client info returns unknown."""
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    req = Request(scope)
    assert logger._get_client_ip(req) == "unknown"  # pylint: disable=protected-access


def test_generate_hash_links_previous(logger):
    """Hash includes previous hash when set."""
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    event = {
        "organization_id": ORG_ID,
        "user_id": USER_ID,
        "action_type": "CREATE",
        "timestamp": ts,
        "description": "Created",
    }
    logger._last_hash = "prev-hash"  # pylint: disable=protected-access
    h1 = logger._generate_hash(event)  # pylint: disable=protected-access
    logger._last_hash = h1  # pylint: disable=protected-access
    h2 = logger._generate_hash(event)  # pylint: disable=protected-access

    assert len(h1) == 64
    assert h1 != h2


def test_calculate_retention_date(logger):
    """Retention years vary by data classification."""
    ts = datetime(2026, 3, 15, tzinfo=timezone.utc)

    phi = logger._calculate_retention_date(ts, "phi")  # pylint: disable=protected-access
    public = logger._calculate_retention_date(ts, "public")  # pylint: disable=protected-access
    unknown = logger._calculate_retention_date(ts, "other")  # pylint: disable=protected-access

    assert phi.year == 2033
    assert public.year == 2027
    assert unknown.year == 2029


def test_get_queue_stats(logger):
    """Queue stats reflect initial state."""
    stats = logger.get_queue_stats()

    assert stats["queue_size"] == 0
    assert stats["max_queue_size"] == 500
    assert stats["processing_active"] is None or stats["processing_active"] is False


@pytest.mark.asyncio
async def test_log_audit_event_enqueues(logger):
    """log_audit_event puts event on queue."""
    await logger.log_audit_event(_event_data(), _request())

    assert logger._queue.qsize() == 1  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_collect_batch_events_timeout(logger):
    """Empty queue returns no events on timeout."""
    batch, got = await logger._collect_batch_events(0.01)  # pylint: disable=protected-access

    assert batch == []
    assert got is False


@pytest.mark.asyncio
async def test_collect_batch_events_drains_queue(logger):
    """Batch collector drains up to batch_size."""
    for _ in range(3):
        logger._queue.put_nowait({"id": _})  # pylint: disable=protected-access

    batch, got = await logger._collect_batch_events(1.0)  # pylint: disable=protected-access

    assert got is True
    assert len(batch) == 3


@pytest.mark.asyncio
async def test_write_audit_batch_empty_noop(logger):
    """Empty batch skips DB write."""
    await logger._write_audit_batch([])  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_write_audit_batch_success(logger):
    """Batch write uses UnitOfWork and bulk insert."""
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    events = [
        {
            "organization_id": ORG_ID,
            "user_id": USER_ID,
            "user_email": "user@example.com",
            "user_role": "admin",
            "action_type": "CREATE",
            "data_classification": "general",
            "table_name": "leads",
            "record_id": "r1",
            "old_values": None,
            "new_values": {"x": 1},
            "changed_fields": [],
            "compliance_tags": [],
            "risk_level": "low",
            "ip_address": "127.0.0.1",
            "description": "Created",
            "timestamp": ts,
            "status_code": 200,
            "category": "crm",
        }
    ]

    mock_conn = MagicMock()
    mock_repo = MagicMock()
    mock_repo.get_last_audit_log_hash = AsyncMock(return_value="prev")
    mock_repo.bulk_create_audit_logs = AsyncMock(return_value=[])

    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_uow.__aexit__ = AsyncMock(return_value=None)

    logger._pool = MagicMock()  # pylint: disable=protected-access

    with (
        patch(
            "apps.user_service.app.dependencies.audit_logs.audit_logger.UnitOfWork",
            return_value=mock_uow,
        ),
        patch(
            "apps.user_service.app.dependencies.audit_logs.audit_logger.AuditLogRepository",
            return_value=mock_repo,
        ),
    ):
        await logger._write_audit_batch(events)  # pylint: disable=protected-access

    mock_repo.get_last_audit_log_hash.assert_awaited_once()
    mock_repo.bulk_create_audit_logs.assert_awaited_once()
    assert logger._last_hash is not None  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_write_audit_batch_raises_internal_error(logger):
    """DB failure wraps as InternalServerErrorException."""
    logger._pool = MagicMock()  # pylint: disable=protected-access
    mock_uow = MagicMock()
    mock_uow.__aenter__ = AsyncMock(side_effect=RuntimeError("db down"))
    mock_uow.__aexit__ = AsyncMock(return_value=None)

    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_logger.UnitOfWork",
        return_value=mock_uow,
    ):
        with pytest.raises(InternalServerErrorException):
            await logger._write_audit_batch(  # pylint: disable=protected-access
                [
                    {
                        "organization_id": ORG_ID,
                        "user_id": USER_ID,
                        "timestamp": datetime.now(timezone.utc),
                        "description": "x",
                        "action_type": "CREATE",
                        "data_classification": "general",
                        "table_name": "t",
                        "record_id": None,
                        "old_values": None,
                        "new_values": None,
                        "changed_fields": [],
                        "compliance_tags": [],
                        "risk_level": "low",
                        "ip_address": "1.1.1.1",
                        "user_email": "a@b.com",
                        "user_role": "admin",
                    }
                ]
            )


@pytest.mark.asyncio
async def test_start_processing_creates_task(logger):
    """start_processing initializes pool and background task."""
    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_logger.get_pool",
        new=AsyncMock(return_value=MagicMock()),
    ):
        await logger.start_processing()

    assert logger._processing_task is not None  # pylint: disable=protected-access
    logger._processing_task.cancel()  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_shutdown_cancels_on_timeout(logger):
    """shutdown cancels task when wait times out."""
    mock_task = MagicMock()
    mock_task.done.return_value = False

    async def slow_wait(*_args, **_kwargs):
        raise TimeoutError

    logger._processing_task = mock_task  # pylint: disable=protected-access
    logger._shutdown_event.clear()  # pylint: disable=protected-access

    with patch("asyncio.wait_for", side_effect=TimeoutError):
        await logger.shutdown()

    mock_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_log_audit_event_queue_full_then_enqueued(logger):
    """Full queue falls back to timed put and enqueues the event."""
    audit_event = {"description": "queued"}

    with (
        patch.object(logger._queue, "put_nowait", side_effect=asyncio.QueueFull()),  # pylint: disable=protected-access
        patch.object(logger._queue, "put", new=AsyncMock()) as mock_put,  # pylint: disable=protected-access
        patch.object(
            logger,
            "_create_audit_event_dict",
            return_value=audit_event,
        ),
    ):
        await logger.log_audit_event(_event_data(), _request())

    mock_put.assert_awaited_once_with(audit_event)


@pytest.mark.asyncio
async def test_log_audit_event_queue_timeout(logger):
    """Queue timeout during fallback is handled without raising."""
    with (
        patch.object(logger._queue, "put_nowait", side_effect=asyncio.QueueFull()),  # pylint: disable=protected-access
        patch.object(
            logger._queue,
            "put",
            side_effect=asyncio.TimeoutError(),
        ),  # pylint: disable=protected-access
        patch.object(
            logger,
            "_create_audit_event_dict",
            return_value={"description": "queued"},
        ),
    ):
        await logger.log_audit_event(_event_data(), _request())

    assert logger._queue.qsize() == 0  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_log_audit_event_validation_error(logger):
    """Data validation errors in audit logging are swallowed."""
    with patch.object(
        logger,
        "_create_audit_event_dict",
        side_effect=ValueError("bad data"),
    ):
        await logger.log_audit_event(_event_data(), _request())

    assert logger._queue.qsize() == 0  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_log_audit_event_runtime_error(logger):
    """Runtime errors in audit logging are logged and swallowed."""
    with patch.object(
        logger,
        "_create_audit_event_dict",
        side_effect=RuntimeError("loop closed"),
    ):
        await logger.log_audit_event(_event_data(), _request())

    assert logger._queue.qsize() == 0  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_process_audit_queue_writes_batch(logger):
    """Processing loop writes collected batches and clears state."""
    batch = [{"description": "event-1"}]
    logger._shutdown_event.clear()  # pylint: disable=protected-access

    collect_calls = {"count": 0}

    async def fake_collect(_timeout):
        collect_calls["count"] += 1
        if collect_calls["count"] == 1:
            return batch.copy(), True
        logger._shutdown_event.set()  # pylint: disable=protected-access
        return [], False

    write_mock = AsyncMock()

    with (
        patch.object(logger, "_collect_batch_events", side_effect=fake_collect),
        patch.object(logger, "_write_audit_batch_with_retry", write_mock),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await logger._process_audit_queue()  # pylint: disable=protected-access

    write_mock.assert_awaited()


@pytest.mark.asyncio
async def test_process_audit_queue_handles_processing_error(logger):
    """Processing loop sleeps and continues after unexpected errors."""
    logger._shutdown_event.clear()  # pylint: disable=protected-access
    calls = {"count": 0}

    async def fake_collect(_timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("transient")
        logger._shutdown_event.set()  # pylint: disable=protected-access
        return [], False

    sleep_mock = AsyncMock()

    with (
        patch.object(logger, "_collect_batch_events", side_effect=fake_collect),
        patch("asyncio.sleep", sleep_mock),
    ):
        await logger._process_audit_queue()  # pylint: disable=protected-access

    sleep_mock.assert_awaited()


@pytest.mark.asyncio
async def test_write_audit_batch_with_retry_eventual_success(logger):
    """Batch write retries transient failures before succeeding."""
    write_mock = AsyncMock(side_effect=[ValueError("retry"), None])

    with patch.object(logger, "_write_audit_batch", write_mock):
        await logger._write_audit_batch_with_retry([{"description": "x"}])  # pylint: disable=protected-access

    assert write_mock.await_count == 2


@pytest.mark.asyncio
async def test_write_audit_batch_with_retry_exhausted(logger):
    """Batch write stops after max retries."""
    write_mock = AsyncMock(side_effect=RuntimeError("persistent"))

    with patch.object(logger, "_write_audit_batch", write_mock):
        await logger._write_audit_batch_with_retry([{"description": "x"}])  # pylint: disable=protected-access

    assert write_mock.await_count == logger._max_retries  # pylint: disable=protected-access
