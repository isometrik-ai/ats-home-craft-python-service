"""Unit tests for centralized logging utilities."""

from __future__ import annotations

import json
import logging

import pytest

from libs.shared_config.app_settings import EnvironmentOption, LogLevelOption
from libs.shared_utils import logger as logger_module
from libs.shared_utils.logger import (
    CustomJSONFormatter,
    CustomTextFormatter,
    RequestIdFilter,
    get_logger,
    request_id_var,
    setup_logging,
)


def test_request_id_filter_uses_context_var() -> None:
    """RequestIdFilter should inject the active request ID."""
    token = request_id_var.set("req-123")
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "req-123"
    finally:
        request_id_var.reset(token)


def test_request_id_filter_defaults_when_unset() -> None:
    """Missing request ID context should fall back to no-request-id."""
    token = request_id_var.set(None)
    try:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        RequestIdFilter().filter(record)
        assert record.request_id == "no-request-id"
    finally:
        request_id_var.reset(token)


def test_custom_json_formatter_includes_core_fields() -> None:
    """JSON formatter should emit structured log fields."""
    record = logging.LogRecord(
        name="svc",
        level=logging.WARNING,
        pathname=__file__,
        lineno=10,
        msg="warn-msg",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-1"

    payload = json.loads(CustomJSONFormatter().format(record))

    assert payload["level"] == "WARNING"
    assert payload["logger"] == "svc"
    assert payload["message"] == "warn-msg"
    assert payload["request_id"] == "req-1"
    assert "timestamp" in payload


def test_custom_text_formatter_includes_request_id() -> None:
    """Text formatter should include request ID in the output line."""
    record = logging.LogRecord(
        name="svc",
        level=logging.INFO,
        pathname=__file__,
        lineno=12,
        msg="info-msg",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-99"

    formatted = CustomTextFormatter().format(record)

    assert "[req-99]" in formatted
    assert "info-msg" in formatted


def test_setup_logging_uses_json_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production environment should configure JSON formatting."""
    monkeypatch.setattr(logger_module.shared_settings, "app_name", "test-service")
    monkeypatch.setattr(logger_module.shared_settings, "log_level", LogLevelOption.DEBUG)
    monkeypatch.setattr(
        logger_module.shared_settings,
        "environment",
        EnvironmentOption.PRODUCTION.value,
    )

    service_logger = setup_logging("test-service")

    assert service_logger.name == "test-service"
    assert service_logger.level == logging.DEBUG
    assert len(service_logger.handlers) == 1
    assert isinstance(service_logger.handlers[0].formatter, CustomJSONFormatter)


def test_setup_logging_uses_text_outside_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-production environment should configure text formatting."""
    monkeypatch.setattr(logger_module.shared_settings, "app_name", "test-service")
    monkeypatch.setattr(logger_module.shared_settings, "log_level", LogLevelOption.INFO)
    monkeypatch.setattr(
        logger_module.shared_settings,
        "environment",
        EnvironmentOption.LOCAL,
    )

    service_logger = setup_logging("test-service")

    assert isinstance(service_logger.handlers[0].formatter, CustomTextFormatter)


def test_get_logger_returns_service_logger_without_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_logger without a name should return the service logger."""
    monkeypatch.setattr(logger_module.shared_settings, "app_name", "child-test-service")
    setup_logging("child-test-service")

    assert get_logger() is logging.getLogger("child-test-service")


def test_get_logger_child_inherits_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Named child loggers should inherit service logger handlers."""
    monkeypatch.setattr(logger_module.shared_settings, "app_name", "child-test-service")
    setup_logging("child-test-service")

    child = get_logger("module.child")

    service_logger = logging.getLogger("child-test-service")
    assert child.level == service_logger.level
    assert child.handlers
    assert child.propagate is False
