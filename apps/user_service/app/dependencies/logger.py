"""
Centralized Logger Module

This module provides a centralized logging configuration for the entire application.
It sets up structured logging with proper formatting, log levels, and handlers.

Features:
- Structured JSON logging for production
- Human-readable logging for development
- Configurable log levels via environment variables
- Request ID tracking
- Performance logging capabilities
- Error tracking and monitoring

Author: AI Assistant
Date: 2024-12-19
"""

import logging
import logging.config
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
import json
from contextvars import ContextVar

# Context variable for request ID tracking
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

# IST timezone (UTC+5:30)
IST_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


class RequestIdFilter(logging.Filter):
    """Filter to add request ID to log records."""

    def filter(self, record):
        record.request_id = request_id_var.get() or "no-request-id"
        return True


class CustomJSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""

    def format(self, record):
        # Get current time in IST
        ist_time = datetime.now(IST_TIMEZONE)

        log_entry = {
            "timestamp": ist_time.isoformat(),
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
        # Convert the timestamp to IST
        utc_time = datetime.fromtimestamp(record.created, timezone.utc)
        ist_time = utc_time.astimezone(IST_TIMEZONE)
        timestamp = ist_time.strftime("%Y-%m-%d %H:%M:%S")

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
    log_level: Optional[str] = None,
    environment: Optional[str] = None,
    service_name: str = "user-service",
) -> logging.Logger:
    """
    Set up centralized logging configuration.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        environment: Environment name (development, staging, production)
        service_name: Name of the service for logging identification

    Returns:
        logging.Logger: Configured logger instance
    """
    # Get configuration from environment variables
    log_level = log_level or os.getenv("LOG_LEVEL", "INFO").upper()
    environment = environment or os.getenv("ENVIRONMENT", "development").lower()

    # Validate log level
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if log_level not in valid_levels:
        log_level = "INFO"

    # Create logger
    logger = logging.getLogger(service_name)
    logger.setLevel(getattr(logging, log_level))

    # Clear any existing handlers
    logger.handlers.clear()

    # Create formatter based on environment
    if environment == "production":
        formatter = CustomJSONFormatter()
    else:
        formatter = CustomTextFormatter()

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, log_level))
    console_handler.setFormatter(formatter)

    # Add request ID filter
    request_filter = RequestIdFilter()
    console_handler.addFilter(request_filter)

    # Add handler to logger
    logger.addHandler(console_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


def get_logger(name: str = None) -> logging.Logger:
    """
    Get a logger instance with the specified name.

    Args:
        name: Logger name (optional, defaults to 'user-service')

    Returns:
        logging.Logger: Logger instance
    """
    # Always return the main configured logger for now
    # This ensures all loggers use the same configuration

    return logging.getLogger(name) if name else logging.getLogger("user-service")


def set_request_id(request_id: str):
    """
    Set the request ID for the current context.

    Args:
        request_id: Unique identifier for the request
    """
    request_id_var.set(request_id)


def get_request_id() -> Optional[str]:
    """
    Get the current request ID.

    Returns:
        Optional[str]: Current request ID or None
    """
    return request_id_var.get()


def log_with_context(
    logger: logging.Logger,
    level: str,
    message: str,
    extra_fields: Optional[Dict[str, Any]] = None,
    **kwargs,
):
    """
    Log a message with additional context fields.

    Args:
        logger: Logger instance
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        message: Log message
        extra_fields: Additional fields to include in the log
        **kwargs: Additional keyword arguments to include
    """
    # Combine extra_fields and kwargs
    all_extra = {}
    if extra_fields:
        all_extra.update(extra_fields)
    if kwargs:
        all_extra.update(kwargs)

    # Create a log record with extra fields
    if all_extra:
        record = logger.makeRecord(
            logger.name,
            getattr(logging, level.upper()),
            "",
            0,
            message,
            (),
            None,
            func="log_with_context",
        )
        record.extra_fields = all_extra
        logger.handle(record)
    else:
        getattr(logger, level.lower())(message)


# Initialize the main application logger
app_logger = setup_logging()
