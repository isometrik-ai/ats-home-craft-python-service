"""Unit tests for custom HTTP exception classes."""

from __future__ import annotations

from fastapi import status

from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


def test_validation_exception_defaults() -> None:
    """ValidationException should expose standard metadata."""
    exc = ValidationException(message_key="errors.validation")
    assert exc.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exc.custom_code == CustomStatusCode.VALIDATION_ERROR


def test_not_found_exception() -> None:
    """NotFoundException should map to HTTP 404."""
    exc = NotFoundException(message_key="errors.not_found")
    assert exc.status_code == status.HTTP_404_NOT_FOUND


def test_auth_exceptions() -> None:
    """Auth-related exceptions should carry expected status codes."""
    assert UnauthorizedException().status_code == status.HTTP_401_UNAUTHORIZED
    assert ForbiddenException().status_code == status.HTTP_403_FORBIDDEN


def test_server_and_bad_request_exceptions() -> None:
    """Generic client/server exceptions should preserve custom codes."""
    assert InternalServerErrorException().custom_code == CustomStatusCode.INTERNAL_SERVER_ERROR
    assert BadRequestException().custom_code == CustomStatusCode.BAD_REQUEST
