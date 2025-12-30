"""Centralized logging utilities for the monorepo.

Provides:
 - Request ID context handling
 - JSON/text formatters with IST timestamps
 - Safe logging helpers with extra context
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

from libs.shared_config.app_settings import EnvironmentOption, shared_settings

# Context variable for request ID tracking
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class RequestIdFilter(logging.Filter):
    """Filter to add request ID to log records."""

    def filter(self, record):
        record.request_id = request_id_var.get() or "no-request-id"
        return True


class CustomJSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""

    def format(self, record):
        # Get current time in IST
        utc_time = datetime.now(UTC)

        log_entry = {
            "timestamp": utc_time.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "no-request-id"),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields if present
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        return json.dumps(log_entry)


class CustomTextFormatter(logging.Formatter):
    """Custom text formatter for human-readable logging."""

    def format(self, record):
        # Add request ID to the format
        record.request_id = getattr(record, "request_id", "no-request-id")

        # Create a more readable format with IST time
        utc_time = datetime.fromtimestamp(record.created, UTC)
        timestamp = utc_time.strftime("%Y-%m-%d %H:%M:%S")

        # Base format
        log_format = (
            f"[{timestamp}] [{record.levelname:8}] "
            f"[{record.request_id}] {record.name}: {record.getMessage()}"
        )
        # Add exception info if present
        if record.exc_info:
            log_format += f"\n{self.formatException(record.exc_info)}"

        return log_format


def setup_logging(
    service_name: str | None = None,
) -> logging.Logger:
    """Set up centralized logging configuration."""
    # Resolve configuration from parameters or environment/config
    service_name = service_name or shared_settings.app_name

    # Create logger
    logger = logging.getLogger(service_name)
    logger.setLevel(getattr(logging, shared_settings.log_level.value))

    # Clear any existing handlers
    logger.handlers.clear()

    # Create formatter based on environment
    formatter = (
        CustomJSONFormatter()
        if shared_settings.environment == EnvironmentOption.PRODUCTION.value
        else CustomTextFormatter()
    )

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, shared_settings.log_level.value))
    console_handler.setFormatter(formatter)

    # Add request ID filter
    request_filter = RequestIdFilter()
    console_handler.addFilter(request_filter)

    # Add handler to logger
    logger.addHandler(console_handler)

    # Change propagation to allow child loggers
    logger.propagate = True

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Get a logger instance with the specified name."""
    # Retrieve the primary application logger
    service_logger = logging.getLogger(shared_settings.app_name)
    # If no specific name, return the service logger
    if not name:
        return service_logger

    # Create or retrieve a child logger
    child = logging.getLogger(name)
    # Ensure child has same level
    child.setLevel(service_logger.level)
    # Attach same handlers as service logger
    for handler in service_logger.handlers:
        if handler not in child.handlers:
            child.addHandler(handler)
    # Prevent propagation to root to avoid duplicate logs
    child.propagate = False
    return child


# Initialize the main application logger
app_logger = setup_logging()

__all__ = [
    "RequestIdFilter",
    "CustomJSONFormatter",
    "CustomTextFormatter",
    "setup_logging",
    "get_logger",
    "app_logger",
    "request_id_var",
]
