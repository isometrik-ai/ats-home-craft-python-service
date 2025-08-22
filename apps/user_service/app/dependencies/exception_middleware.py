# pylint: disable=logging-fstring-interpolation
# pylint: disable=broad-exception-caught
# pylint: disable=protected-access
"""Exception middleware for the API service."""
import sys
import traceback
import uuid

# Standard library imports
from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException as FastAPIHTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    maybe_log_audit_on_error,
    logger,
)
from apps.user_service.app.dependencies.logger import get_logger

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
    if hasattr(request.state, "_cached_body"):
        try:
            body_str = request.state._cached_body.decode("utf-8", errors="ignore")
            context["body_preview"] = (
                body_str[:200] + "..." if len(body_str) > 200 else body_str
            )
        except Exception:
            context["body_preview"] = "[Unable to decode body]"

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


class CacheRequestBodyMiddleware(
    BaseHTTPMiddleware
):  # pylint: disable=too-few-public-methods
    """
    Middleware to cache request body for potential reuse.

    This middleware caches the request body in request.state._cached_body
    to allow multiple reads of the request body, which is useful for
    audit logging and other middleware that need to access the body.
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
                exception_logger.debug(  # pylint: disable=logging-fstring-interpolation
                    f"Request body cached successfully - "
                    f"Request ID: {request_id}, "
                    f"Method: {request.method}, "
                    f"URL: {str(request.url)}, Body Size: {len(body_bytes)} bytes"
                )
            except (OSError, ValueError) as e:  # pylint: disable=broad-exception-caught
                # pylint: disable=protected-access
                request.state._cached_body = b""
                exception_logger.warning(  # pylint: disable=logging-fstring-interpolation
                    f"Failed to cache request body - "
                    f"Request ID: {request_id}, "
                    f"Method: {request.method}, "
                    f"URL: {str(request.url)}, Error: {str(e)}"
                )

        response = await call_next(request)
        return response


async def unified_exception_handler(request: Request, exc: Exception):
    """
    Middleware to handle ALL Exceptions with comprehensive logging.
    """
    print("unified_exception_handler")
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    # timestamp = datetime.utcnow().isoformat()

    # Extract request and exception information
    operation_name = request.scope.get("endpoint", str(request.url.path))
    request_context = extract_request_context(request)
    exception_context = extract_exception_context(exc)

    # Log exception start
    exception_logger.info(
        f"Exception handler triggered - Request ID: {request_id}, "
        f"Operation: {operation_name}, "
        f"Exception Type: {exception_context['exception_type']}, "
        f"Method: {request_context['method']}, "
        f"URL: {request_context['url']}, "
        f"File: {exception_context.get('file_path', 'unknown')}:"
        f"{exception_context.get('line_number', 'unknown')}"
    )

    # Extract route information
    route = request.scope.get("route")
    if route and hasattr(route.endpoint, "__audit_api_call_params__"):
        audit_params = route.endpoint.__audit_api_call_params__
        request.state.audit_metadata = audit_params
        exception_logger.debug(  # pylint: disable=logging-fstring-interpolation
            f"Audit metadata extracted from route - Request ID: {request_id}, "
            f"Audit Params: {str(audit_params)}"
        )

    # Handle HTTP Exceptions (4xx, 5xx)
    if isinstance(exc, (FastAPIHTTPException, StarletteHTTPException)):
        status_code = getattr(exc, "status_code", 500)
        detail = getattr(exc, "detail", str(exc))

        exception_logger.warning(  # pylint: disable=logging-fstring-interpolation
            f"HTTP Exception occurred - Status Code: {status_code}, "
            f"Detail: {detail}, Request ID: {request_id}, "
            f"Method: {request_context['method']}, URL: "
            f"{request_context['url']}, "
            f"File: {exception_context.get('file_path', 'unknown')}:"
            f"{exception_context.get('line_number', 'unknown')}"
        )

        try:
            await maybe_log_audit_on_error(
                request, str(detail), status_code=status_code
            )
        except Exception as audit_error:
            exception_logger.warning(
                f"Audit logging failed during exception handling: {audit_error}"
            )

        return JSONResponse(status_code=status_code, content={"detail": detail})

    # Handle Request Validation Errors (422)
    if isinstance(exc, RequestValidationError):
        validation_errors = exc.errors()

        exception_logger.warning(  # pylint: disable=logging-fstring-interpolation
            f"Request validation error occurred - Status Code: 422, "
            f"Request ID: {request_id}, "
            f"Method: {request_context['method']}, "
            f"URL: {request_context['url']}, "
            f"File: {exception_context.get('file_path', 'unknown')}:"
            f"{exception_context.get('line_number', 'unknown')}, "
            f"Validation Errors: {str(validation_errors)}"
        )

        try:
            await maybe_log_audit_on_error(request, str(exc), status_code=422)
        except Exception as audit_error:
            exception_logger.warning(
                f"Audit logging failed during validation error: {audit_error}"
            )

        return JSONResponse(
            status_code=422,
            content={"detail": "Validation error", "errors": validation_errors},
        )

    # Handle Unhandled/Generic Exceptions
    error_message = str(exc)
    full_traceback = traceback.format_exc()

    exception_logger.error(  # pylint: disable=logging-fstring-interpolation
        f"Unexpected exception occurred - Error: {error_message}, "
        f"Exception Type: {exception_context['exception_type']}, "
        f"Request ID: {request_id}, Method: {request_context['method']}, "
        f"URL: {request_context['url']}, Operation: {operation_name}, "
        f"File: {exception_context.get('file_path', 'unknown')}:"
        f"{exception_context.get('line_number', 'unknown')}, "
        f"Function: {exception_context.get('function_name', 'unknown')}"
    )

    # Also log to the audit logger for backward compatibility
    logger.error("Error in %s: %s", operation_name, error_message)
    logger.debug(full_traceback)

    try:
        await maybe_log_audit_on_error(request, error_message, status_code=500)
    except Exception as audit_error:
        exception_logger.warning(
            f"Audit logging failed during unexpected error: {audit_error}"
        )
    print("JSONResponse 500")
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error during {str(exc)}"},
    )
