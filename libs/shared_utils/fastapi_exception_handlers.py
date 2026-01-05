"""FastAPI exception handlers."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from libs.shared_utils.http_exceptions import (
    BadRequestException,
    CustomHTTPException,
    DuplicateValueException,
    ForbiddenException,
    NotFoundException,
    RateLimitExceededException,
    UnauthorizedException,
    ValidationException,
)
from libs.shared_utils.response_factory import error_response
from libs.shared_utils.status_codes import CustomStatusCode

logger = logging.getLogger(__name__)


def _handle_http_exception(request: Request, exc: StarletteHTTPException):
    """Handle StarletteHTTPException."""
    status_map = {
        404: ("errors.not_found", CustomStatusCode.NOT_FOUND),
        403: ("errors.forbidden", CustomStatusCode.FORBIDDEN),
        401: ("errors.unauthorized", CustomStatusCode.UNAUTHORIZED),
        405: ("errors.method_not_allowed", CustomStatusCode.BAD_REQUEST),
        429: ("errors.rate_limit_exceeded", CustomStatusCode.RATE_LIMIT_EXCEEDED),
        500: ("errors.internal_server_error", CustomStatusCode.INTERNAL_SERVER_ERROR),
    }

    key, custom_code = status_map.get(
        exc.status_code,
        (f"errors.status_{exc.status_code}", CustomStatusCode.BAD_REQUEST),
    )

    params = {}
    if exc.status_code == 405:
        params = {"method": request.method, "path": request.url.path}
    elif exc.status_code == 429 and "Retry-After" in exc.headers:
        params = {"retry_after": exc.headers["Retry-After"]}

    return error_response(
        request=request,
        message_key=key,
        status_code=exc.status_code,
        custom_code=custom_code,
        params=params if params else None,
        headers=exc.headers if hasattr(exc, "headers") else None,
    )


def _handle_validation_exception(request: Request, exc: RequestValidationError | ValidationError):
    """Handle RequestValidationError."""
    detailed_errors = []
    first_error = exc.errors()[0] if exc.errors() else None
    first_error_msg = (
        first_error.get("msg", "Unknown error") if first_error else "Unknown validation error"
    )

    if first_error and first_error.get("type") == "missing":
        param_name = first_error.get("loc", ["unknown"])[-1]
        message_key = "errors.missing_required_param"
        params = {"param_name": param_name}
    else:
        message_key = "errors.validation"
        params = {"message": first_error_msg}

    # Known header fields to improve location hints for header/dependency validation
    header_fields = {
        "authorization",
        "lan",
        "x-tenant-id",
        "x-project-id",
        "city",
        "state",
        "country",
        "ipaddress",
        "latitude",
        "longitude",
        "platform",
        "version",
    }

    for error in exc.errors():
        loc_parts = [str(loc) for loc in error.get("loc", [])]

        # If location lacks a section prefix and looks like one of our headers, prefix it
        if loc_parts and loc_parts[0] not in {"body", "query", "path", "header", "cookie"}:
            if loc_parts[0] in header_fields:
                loc_parts.insert(0, "headers")

        location = ".".join(loc_parts)
        detailed_errors.append(
            {
                "field": location,
                "type": error.get("type", ""),
                "msg": error.get("msg", ""),
            },
        )

    return error_response(
        request=request,
        message_key=message_key,
        status_code=422,
        custom_code=CustomStatusCode.VALIDATION_ERROR,
        errors=detailed_errors,
        params=params,
    )


def _register_http_exception_handler(app: FastAPI) -> None:
    """Register HTTP exception handler."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return _handle_http_exception(request, exc)


def _register_validation_exception_handler(app: FastAPI) -> None:
    """Register validation exception handler."""

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return _handle_validation_exception(request, exc)


def _register_pydantic_validation_exception_handler(app: FastAPI) -> None:
    """Register raw Pydantic validation exception handler (e.g., dependency models)."""

    @app.exception_handler(ValidationError)
    async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
        # Reuse the same shape as RequestValidationError
        return _handle_validation_exception(request, exc)


def _register_value_error_exception_handler(app: FastAPI) -> None:
    """Register ValueError handler to return uniform bad request responses."""

    @app.exception_handler(ValueError)
    async def value_error_exception_handler(request: Request, exc: ValueError):
        msg = str(exc) or "Invalid value"
        errors = [{"field": None, "type": "value_error", "msg": msg}]
        return error_response(
            request=request,
            message_key="errors.bad_request",
            status_code=400,
            custom_code=CustomStatusCode.BAD_REQUEST,
            errors=errors,
            params={"message": msg},
        )


def _register_not_found_exception_handler(app: FastAPI) -> None:
    """Register not found exception handler."""

    @app.exception_handler(NotFoundException)
    async def not_found_exception_handler(request: Request, exc: NotFoundException):
        return error_response(
            request=request,
            message_key=exc.message_key or "errors.not_found",
            status_code=404,
            custom_code=exc.custom_code or CustomStatusCode.NOT_FOUND,
            params=exc.params or None,
            errors=exc.errors or None,
            headers=exc.headers or None,
        )


def _register_duplicate_value_exception_handler(app: FastAPI) -> None:
    """Register duplicate value exception handler."""

    @app.exception_handler(DuplicateValueException)
    async def duplicate_value_exception_handler(request: Request, exc: DuplicateValueException):
        return error_response(
            request=request,
            message_key=exc.message_key or "errors.duplicate_value",
            status_code=409,
            custom_code=exc.custom_code or CustomStatusCode.DUPLICATE_ENTRY,
            params=exc.params or None,
            errors=exc.errors or None,
            headers=exc.headers or None,
        )


def _register_rate_limit_exception_handler(app: FastAPI) -> None:
    """Register rate limit exception handler."""

    @app.exception_handler(RateLimitExceededException)
    async def rate_limit_exceeded_exception_handler(
        request: Request, exc: RateLimitExceededException
    ):
        headers = {"Retry-After": str(exc.retry_after)} if hasattr(exc, "retry_after") else None
        return error_response(
            request=request,
            message_key="errors.rate_limit_exceeded",
            status_code=429,
            custom_code=CustomStatusCode.RATE_LIMIT_EXCEEDED,
            params={"retry_after": exc.retry_after} if hasattr(exc, "retry_after") else None,
            headers=headers,
        )


def _register_slowapi_rate_limit_exception_handler(app: FastAPI) -> None:
    """Register slowapi rate limit exception handler."""

    @app.exception_handler(RateLimitExceeded)
    async def slowapi_rate_limit_exceeded_exception_handler(
        request: Request, exc: RateLimitExceeded
    ):
        # Extract retry_after from the exception if available
        retry_after = getattr(exc, "retry_after", 60)
        headers = {"Retry-After": str(retry_after)}

        return error_response(
            request=request,
            message_key="errors.rate_limit_exceeded",
            status_code=429,
            custom_code=CustomStatusCode.RATE_LIMIT_EXCEEDED,
            params={"retry_after": retry_after},
            headers=headers,
        )


def _register_validation_custom_exception_handler(app: FastAPI) -> None:
    """Register custom validation exception handler."""

    @app.exception_handler(ValidationException)
    async def custom_validation_exception_handler(request: Request, exc: ValidationException):
        return error_response(
            request=request,
            message_key=exc.message_key,
            status_code=exc.status_code,
            custom_code=exc.custom_code,
            params=exc.params,
            errors=exc.errors,
            headers=exc.headers if hasattr(exc, "headers") else None,
        )


def _register_custom_http_exception_handler(app: FastAPI) -> None:
    """Register custom HTTP exception handler."""

    @app.exception_handler(CustomHTTPException)
    async def custom_http_exception_handler(request: Request, exc: CustomHTTPException):
        return error_response(
            request=request,
            message_key=exc.message_key,
            status_code=exc.status_code,
            custom_code=exc.custom_code,
            params=exc.params,
            errors=exc.errors,
            headers=exc.headers if hasattr(exc, "headers") else None,
        )


def _register_unauthorized_exception_handler(app: FastAPI) -> None:
    """Register unauthorized exception handler."""

    @app.exception_handler(UnauthorizedException)
    async def unauthorized_exception_handler(request: Request, exc: UnauthorizedException):
        return error_response(
            request=request,
            message_key=exc.message_key,
            status_code=exc.status_code,
            custom_code=exc.custom_code,
            params=exc.params,
            errors=exc.errors,
            headers=exc.headers,
        )


def _register_forbidden_exception_handler(app: FastAPI) -> None:
    """Register forbidden exception handler."""

    @app.exception_handler(ForbiddenException)
    async def forbidden_exception_handler(request: Request, exc: ForbiddenException):
        return error_response(
            request=request,
            message_key=exc.message_key,
            status_code=exc.status_code,
            custom_code=exc.custom_code,
            params=exc.params,
            errors=exc.errors,
            headers=exc.headers,
        )


def _register_bad_request_exception_handler(app: FastAPI) -> None:
    """Register bad request exception handler."""

    @app.exception_handler(BadRequestException)
    async def bad_request_exception_handler(request: Request, exc: BadRequestException):
        return error_response(
            request=request,
            message_key=exc.message_key,
            status_code=exc.status_code,
            custom_code=exc.custom_code,
            params=exc.params,
            errors=exc.errors,
            headers=exc.headers,
        )


def _register_unhandled_exception_handler(app: FastAPI) -> None:
    """Register catch-all handler to keep error responses formatted."""

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return error_response(
            request=request,
            message_key="errors.internal_server_error",
            status_code=500,
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers with the FastAPI app."""
    handlers = [
        _register_http_exception_handler,
        _register_validation_exception_handler,
        _register_pydantic_validation_exception_handler,
        _register_value_error_exception_handler,
        _register_not_found_exception_handler,
        _register_duplicate_value_exception_handler,
        _register_rate_limit_exception_handler,
        _register_slowapi_rate_limit_exception_handler,
        _register_validation_custom_exception_handler,
        _register_custom_http_exception_handler,
        _register_unauthorized_exception_handler,
        _register_forbidden_exception_handler,
        _register_bad_request_exception_handler,
        _register_unhandled_exception_handler,
    ]

    for handler in handlers:
        handler(app)
