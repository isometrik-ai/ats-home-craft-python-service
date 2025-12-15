"""Exception middleware for the API service."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    maybe_log_audit_on_error,
)
from apps.user_service.app.dependencies.logger import get_logger
from libs.shared_utils.fastapi_exception_handlers import (
    FastAPIExceptionHandlers,
)
from libs.shared_utils.fastapi_exception_handlers import (
    register_exception_handlers as register_shared_exception_handlers,
)
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

# Use the shared application logger
logger = get_logger()

# Initialize logger for exception middleware
exception_logger = get_logger("exception-middleware")


class CacheRequestBodyMiddleware(BaseHTTPMiddleware):
    """Middleware to cache request body for potential reuse.

    This middleware caches the request body in request.state.cached_body
    to allow multiple reads of the request body, which is useful for
    audit logging and other middleware that need to access the body.

    Note: Using a cached_body attribute is an accepted pattern in FastAPI
    middleware for caching request bodies.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]):
        """Process the request and cache its body for reuse.

        Args:
            request (Request): The incoming FastAPI request
            call_next (Callable[[Request], Any]): The next middleware or endpoint handler

        Returns:
            Any: The response from the next handler
        """
        if request.method == "OPTIONS":
            return await call_next(request)

        request_id = str(uuid.uuid4())

        if not hasattr(request.state, "cached_body"):
            try:
                body_bytes = await request.body()
                request.state.cached_body = body_bytes

                log_msg = (
                    "Request body cached successfully - Request ID: %s, "
                    "Method: %s, URL: %s, Body Size: %s bytes"
                )
                exception_logger.debug(
                    log_msg,
                    request_id,
                    request.method,
                    str(request.url),
                    len(body_bytes),
                )

            except (OSError, ValueError) as e:
                request.state.cached_body = b""

                log_msg = (
                    "Failed to cache request body - Request ID: %s, Method: %s, URL: %s, Error: %s"
                )
                exception_logger.warning(
                    log_msg,
                    request_id,
                    request.method,
                    str(request.url),
                    str(e),
                )

        response = await call_next(request)
        return response


async def _handle_audit_logging(
    request: Request, error_message: str, status_code: int, context: str = "general"
) -> None:
    """Handle audit logging with proper error handling.

    Args:
        request (Request): FastAPI request object
        error_message (str): Error message to log
        status_code (int): HTTP status code
        context (str): Context for the error (default: "general")
    """

    try:
        await maybe_log_audit_on_error(request, error_message, status_code=status_code)
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        exception_logger.warning(
            "Audit logging failed during %s handling - data error: %s",
            context,
            str(e),
        )
    except (OSError, IOError) as e:
        exception_logger.warning(
            "Audit logging failed during %s handling - I/O error: %s",
            context,
            str(e),
        )
    except (RuntimeError, asyncio.CancelledError) as e:
        exception_logger.warning(
            "Audit logging failed during %s handling - runtime error: %s",
            context,
            str(e),
        )


def _handle_http_exception(request: Request, exc):
    """Handle Starlette/FastAPI HTTP exceptions with localized responses."""

    status_map = {
        404: ("errors.not_found", CustomStatusCode.NOT_FOUND),
        403: ("errors.forbidden", CustomStatusCode.FORBIDDEN),
        401: ("errors.unauthorized", CustomStatusCode.UNAUTHORIZED),
        405: ("errors.method_not_allowed", CustomStatusCode.BAD_REQUEST),
        429: ("errors.rate_limit_exceeded", CustomStatusCode.RATE_LIMIT_EXCEEDED),
        500: ("errors.internal_server_error", CustomStatusCode.INTERNAL_SERVER_ERROR),
    }

    message_key, custom_code = status_map.get(
        exc.status_code,
        (f"errors.status_{exc.status_code}", CustomStatusCode.BAD_REQUEST),
    )

    params = {}
    if exc.status_code == 405:
        params = {"method": request.method, "path": request.url.path}
    elif exc.status_code == 429 and getattr(exc, "headers", None):
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            params = {"retry_after": retry_after}

    return error_response(
        request=request,
        message_key=message_key,
        status_code=exc.status_code,
        custom_code=custom_code,
        params=params or None,
        headers=exc.headers if hasattr(exc, "headers") else None,
    )


def _handle_validation_exception(request: Request, exc):
    """Handle RequestValidationError with detailed errors."""

    detailed_errors = []
    first_error = exc.errors()[0] if exc.errors() else None
    first_error_msg = (
        first_error.get("msg", "Unknown validation error")
        if first_error
        else "Unknown validation error"
    )

    if first_error and first_error.get("type") == "missing":
        param_name = first_error.get("loc", ["unknown"])[-1]
        first_error_msg = f"Missing required parameter: {param_name}"
        message_key = "errors.missing_required_param"
        params = {"param_name": param_name}
    else:
        message_key = "errors.validation"
        params = {"message": first_error_msg}

    for error in exc.errors():
        location = ".".join(str(loc) for loc in error.get("loc", []))
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


def _error_from_custom_exception(request: Request, exc: CustomHTTPException):
    """Render response for custom HTTP exceptions."""

    return error_response(
        request=request,
        message_key=exc.message_key,
        status_code=exc.status_code,
        custom_code=exc.custom_code,
        params=getattr(exc, "params", None),
        errors=getattr(exc, "errors", None),
        headers=exc.headers if hasattr(exc, "headers") else None,
    )


def _register_service_exception_handlers(app: FastAPI) -> None:
    """Register service-specific exception handlers."""

    @app.exception_handler(BadRequestException)
    async def bad_request_exception_handler(request: Request, exc: BadRequestException):
        return _error_from_custom_exception(request, exc)

    @app.exception_handler(ValidationException)
    async def custom_validation_exception_handler(request: Request, exc: ValidationException):
        return _error_from_custom_exception(request, exc)

    @app.exception_handler(NotFoundException)
    async def not_found_exception_handler(request: Request, exc: NotFoundException):
        return _error_from_custom_exception(request, exc)

    @app.exception_handler(UnauthorizedException)
    async def unauthorized_exception_handler(request: Request, exc: UnauthorizedException):
        return _error_from_custom_exception(request, exc)

    @app.exception_handler(ForbiddenException)
    async def forbidden_exception_handler(request: Request, exc: ForbiddenException):
        return _error_from_custom_exception(request, exc)

    @app.exception_handler(DuplicateValueException)
    async def duplicate_value_exception_handler(request: Request, exc: DuplicateValueException):
        return _error_from_custom_exception(request, exc)

    @app.exception_handler(RateLimitExceededException)
    async def rate_limit_exception_handler(request: Request, exc: RateLimitExceededException):
        return _error_from_custom_exception(request, exc)

    @app.exception_handler(CustomHTTPException)
    async def generic_custom_http_exception_handler(request: Request, exc: CustomHTTPException):
        return _error_from_custom_exception(request, exc)

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
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


def register_exception_handlers(app: FastAPI):
    """Register shared exception handlers configured for the user service."""

    handlers = FastAPIExceptionHandlers(
        logger=logger,
        exception_logger=exception_logger,
        audit_logger=_handle_audit_logging,
    )
    register_shared_exception_handlers(app, handlers=handlers)

    # Override specific exception classes with service-specific responses
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        return _handle_http_exception(request, exc)

    @app.exception_handler(FastAPIHTTPException)
    async def fastapi_http_exception_handler(request: Request, exc: FastAPIHTTPException):
        return _handle_http_exception(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return _handle_validation_exception(request, exc)

    _register_service_exception_handlers(app)
