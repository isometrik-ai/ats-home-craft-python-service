"""Exception middleware for the API service."""
import sys
import traceback
import uuid
import asyncio

# Standard library imports
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException as FastAPIHTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    maybe_log_audit_on_error,
)
from apps.user_service.app.dependencies.logger import get_logger

# Use the shared application logger
logger = get_logger()

# Initialize logger for exception middleware
exception_logger = get_logger("exception-middleware")


def extract_request_context(request: Request) -> dict:
    """
    Extract comprehensive context information from the request.

    Args:
        request: FastAPI Request object

    Returns:
        dict: Context information including headers, query params, etc.
    """
    context = {
        "method": request.method,
        "url": str(request.url),
        "path": request.url.path,
        "query_params": dict(request.query_params),
        "headers": dict(request.headers),
        "client_ip": request.client.host if request.client else "unknown",
        "user_agent": request.headers.get("user-agent", "unknown"),
    }

    # Add request body if available (truncated for security)
    # pylint: disable=protected-access
    if hasattr(request.state, "_cached_body"):
        try:
            body_str = request.state._cached_body.decode("utf-8", errors="ignore")
            context["body_preview"] = (
                body_str[:200] + "..." if len(body_str) > 200 else body_str
            )
        except UnicodeError:
            context["body_preview"] = "[Unable to decode body - Unicode error]"
        except (ValueError, AttributeError) as e:
            context["body_preview"] = f"[Unable to decode body - {str(e)}]"

    return context


def extract_exception_context(exc: Exception) -> dict:
    """
    Extract context information from the exception.

    Args:
        exc: Exception object

    Returns:
        dict: Exception context including file path, line number, etc.
    """

    # Get the current exception info
    _, _, exc_traceback = sys.exc_info()

    context = {
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
    }

    if exc_traceback:
        # Get the most recent frame (where the exception occurred)
        tb = exc_traceback
        while tb.tb_next:
            tb = tb.tb_next

        frame = tb.tb_frame
        context.update(
            {
                "file_path": frame.f_code.co_filename,
                "line_number": tb.tb_lineno,
                "function_name": frame.f_code.co_name,
                "module_name": frame.f_globals.get("__name__", "unknown"),
            }
        )

    return context


class CacheRequestBodyMiddleware(BaseHTTPMiddleware):
    """
    Middleware to cache request body for potential reuse.

    This middleware caches the request body in request.state._cached_body
    to allow multiple reads of the request body, which is useful for
    audit logging and other middleware that need to access the body.

    Note: Using _cached_body is an accepted pattern in FastAPI middleware
    for caching request bodies. The protected access warning is suppressed
    as this is the intended usage.
    """

    async def dispatch(self, request: Request, call_next):
        """
        Process the request and cache its body for reuse.

        Args:
            request (Request): The incoming FastAPI request
            call_next: The next middleware or endpoint handler

        Returns:
            Response: The response from the next handler
        """
        # Generate request ID for tracking
        request_id = str(uuid.uuid4())

        if not hasattr(request.state, "_cached_body"):
            try:
                body_bytes = await request.body()
                # pylint: disable=protected-access
                request.state._cached_body = body_bytes

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
                # pylint: disable=protected-access
                request.state._cached_body = b""

                log_msg = (
                    "Failed to cache request body - Request ID: %s, "
                    "Method: %s, URL: %s, Error: %s"
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
    request: Request,
    error_message: str,
    status_code: int,
    context: str = "general"
) -> None:
    """
    Handle audit logging with proper error handling.

    Args:
        request: The FastAPI request object
        error_message: The error message to log
        status_code: The HTTP status code
        context: Context string for the log message
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


async def unified_exception_handler(request: Request, exc: Exception):
    """
    Middleware to handle ALL Exceptions with comprehensive logging.
    """
    print("unified_exception_handler")
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())

    # Extract request and exception information
    operation_name = request.scope.get("endpoint", str(request.url.path))
    request_context = extract_request_context(request)
    exception_context = extract_exception_context(exc)

    # Log exception start
    log_msg = (
        "Exception handler triggered - Request ID: %s, Operation: %s, "
        "Type: %s, Method: %s, URL: %s, File: %s:%s"
    )
    exception_logger.info(
        log_msg,
        request_id,
        operation_name,
        exception_context['exception_type'],
        request_context['method'],
        request_context['url'],
        exception_context.get('file_path', 'unknown'),
        exception_context.get('line_number', 'unknown'),
    )

    # Extract route information
    route = request.scope.get("route")
    if route and hasattr(route.endpoint, "__audit_api_call_params__"):
        audit_params = route.endpoint.__audit_api_call_params__
        request.state.audit_metadata = audit_params
        exception_logger.debug(
            "Audit metadata extracted from route - Request ID: %s, Params: %s",
            request_id,
            str(audit_params),
        )

    # Handle HTTP Exceptions (4xx, 5xx)
    if isinstance(exc, (FastAPIHTTPException, StarletteHTTPException)):
        status_code = getattr(exc, "status_code", 500)
        detail = getattr(exc, "detail", str(exc))

        log_msg = (
            "HTTP Exception occurred - Status Code: %s, Detail: %s, Request ID: %s, "
            "Method: %s, URL: %s, File: %s:%s"
        )
        exception_logger.warning(
            log_msg,
            status_code,
            detail,
            request_id,
            request_context['method'],
            request_context['url'],
            exception_context.get('file_path', 'unknown'),
            exception_context.get('line_number', 'unknown'),
        )

        await _handle_audit_logging(request, str(detail), status_code, "HTTP exception")
        return JSONResponse(status_code=status_code, content={"detail": detail})

    # Handle Request Validation Errors (422)
    if isinstance(exc, RequestValidationError):
        validation_errors = exc.errors()

        log_msg = (
            "Request validation error occurred - Status Code: 422, Request ID: %s, "
            "Method: %s, URL: %s, File: %s:%s, Validation Errors: %s"
        )
        exception_logger.warning(
            log_msg,
            request_id,
            request_context['method'],
            request_context['url'],
            exception_context.get('file_path', 'unknown'),
            exception_context.get('line_number', 'unknown'),
            str(validation_errors),
        )

        await _handle_audit_logging(request, str(exc), 422, "validation error")
        return JSONResponse(
            status_code=422,
            content={"detail": "Validation error", "errors": validation_errors},
        )

    # Handle Unhandled/Generic Exceptions
    error_message = str(exc)
    full_traceback = traceback.format_exc()

    log_msg = (
        "Unexpected exception occurred - Error: %s, Exception Type: %s, "
        "Request ID: %s, Method: %s, URL: %s, Operation: %s, File: %s:%s, "
        "Function: %s"
    )
    exception_logger.error(
        log_msg,
        error_message,
        exception_context['exception_type'],
        request_id,
        request_context['method'],
        request_context['url'],
        operation_name,
        exception_context.get('file_path', 'unknown'),
        exception_context.get('line_number', 'unknown'),
        exception_context.get('function_name', 'unknown'),
    )

    # Also log to the audit logger for backward compatibility
    logger.error("Error in %s: %s", operation_name, error_message)
    logger.debug(full_traceback)

    await _handle_audit_logging(request, error_message, 500, "unexpected error")
    print("JSONResponse 500")
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error during {str(exc)}"},
    )
