"""Custom HTTP exception classes with metadata for response factories."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from libs.shared_utils.status_codes import CustomStatusCode


class CustomHTTPException(HTTPException):
    """Base exception carrying response metadata."""

    def __init__(
        self,
        *,
        message_key: str,
        status_code: int,
        custom_code: CustomStatusCode,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(status_code=status_code, detail=message_key, headers=headers)
        self.message_key = message_key
        self.custom_code = custom_code
        self.params = params or {}
        self.errors = errors or None


class InternalServerErrorException(CustomHTTPException):
    """500 Internal Server Error."""

    def __init__(
        self,
        message_key: str = "errors.internal_server_error",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.INTERNAL_SERVER_ERROR,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


class BadRequestException(CustomHTTPException):
    """400 Bad Request."""

    def __init__(
        self,
        message_key: str = "errors.bad_request",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.BAD_REQUEST,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_400_BAD_REQUEST,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


class ValidationException(CustomHTTPException):
    """422 Validation error."""

    def __init__(
        self,
        message_key: str = "errors.validation",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.VALIDATION_ERROR,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


class UnauthorizedException(CustomHTTPException):
    """401 Unauthorized."""

    def __init__(
        self,
        message_key: str = "errors.unauthorized",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.UNAUTHORIZED,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_401_UNAUTHORIZED,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


class ForbiddenException(CustomHTTPException):
    """403 Forbidden."""

    def __init__(
        self,
        message_key: str = "errors.forbidden",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.FORBIDDEN,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_403_FORBIDDEN,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


class NotFoundException(CustomHTTPException):
    """404 Not Found."""

    def __init__(
        self,
        message_key: str = "errors.not_found",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.NOT_FOUND,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_404_NOT_FOUND,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


class ConflictException(CustomHTTPException):
    """409 Conflict."""

    def __init__(
        self,
        message_key: str = "errors.conflict",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.CONFLICT,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_409_CONFLICT,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


class DuplicateValueException(CustomHTTPException):
    """409 Conflict / duplicate."""

    def __init__(
        self,
        message_key: str = "errors.duplicate_value",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.DUPLICATE_ENTRY,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_409_CONFLICT,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


class RateLimitExceededException(CustomHTTPException):
    """429 rate limit with optional retry header."""

    def __init__(
        self,
        *,
        retry_after: int | None = None,
        message_key: str = "errors.rate_limit_exceeded",
        custom_code: CustomStatusCode = CustomStatusCode.RATE_LIMIT_EXCEEDED,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        retry_headers = headers or {}
        if retry_after is not None:
            retry_headers.setdefault("Retry-After", str(retry_after))
            params = {**(params or {}), "retry_after": retry_after}
        self.retry_after = retry_after
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            custom_code=custom_code,
            params=params,
            headers=retry_headers or None,
        )


class ServiceUnavailableException(CustomHTTPException):
    """503 Service Unavailable."""

    def __init__(
        self,
        message_key: str = "errors.service_unavailable",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.SERVICE_UNAVAILABLE,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


class GoneException(CustomHTTPException):
    """410 Gone."""

    def __init__(
        self,
        message_key: str = "errors.gone",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.GONE,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_410_GONE,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


class TooManyRequestsException(CustomHTTPException):
    """429 Too Many Requests."""

    def __init__(
        self,
        message_key: str = "errors.too_many_requests",
        *,
        custom_code: CustomStatusCode = CustomStatusCode.RATE_LIMIT_EXCEEDED,
        params: dict[str, Any] | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ):
        super().__init__(
            message_key=message_key,
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            custom_code=custom_code,
            params=params,
            errors=errors,
            headers=headers,
        )


__all__ = [
    "CustomHTTPException",
    "InternalServerErrorException",
    "BadRequestException",
    "ValidationException",
    "UnauthorizedException",
    "ForbiddenException",
    "NotFoundException",
    "DuplicateValueException",
    "RateLimitExceededException",
    "ConflictException",
    "GoneException",
    "ServiceUnavailableException",
    "TooManyRequestsException",
]
