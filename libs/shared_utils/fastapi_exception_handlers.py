"""Shared FastAPI exception handler utilities."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import traceback
import uuid
from collections.abc import Awaitable, Callable, Iterable

from fastapi import FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

AuditLoggerType = Callable[[Request, str, int, str], Awaitable[None]]
EnvironmentResolver = Callable[[], str]


class FastAPIExceptionHandlers:
    """Configurable exception handlers for FastAPI apps."""

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        exception_logger: logging.Logger | None = None,
        audit_logger: AuditLoggerType | None = None,
        audit_metadata_attribute: str = "__audit_api_call_params__",
        internal_envs: Iterable[str] | None = None,
        environment_resolver: EnvironmentResolver | None = None,
    ):
        self.logger = logger or logging.getLogger("fastapi-app")
        self.exception_logger = exception_logger or logging.getLogger("exception-middleware")
        self.audit_logger = audit_logger
        self.audit_metadata_attribute = audit_metadata_attribute
        self.internal_envs = tuple(internal_envs or ("development", "dev", "local"))
        self.environment_resolver = environment_resolver or (
            lambda: os.getenv("ENVIRONMENT", "production")
        )

    def register(self, app: FastAPI) -> None:
        """Register handlers on the provided FastAPI instance."""

        @app.exception_handler(StarletteHTTPException)
        async def http_exception_handler(request: Request, exc: StarletteHTTPException):
            return await self._handle_http_exception(request, exc)

        @app.exception_handler(FastAPIHTTPException)
        async def fastapi_http_exception_handler(request: Request, exc: FastAPIHTTPException):
            return await self._handle_http_exception(request, exc)

        @app.exception_handler(RequestValidationError)
        async def validation_exception_handler(request: Request, exc: RequestValidationError):
            return await self._handle_validation_exception(request, exc)

        @app.exception_handler(Exception)
        async def unexpected_exception_handler(request: Request, exc: Exception):
            return await self._handle_unexpected_exception(request, exc)

    async def _handle_http_exception(
        self,
        request: Request,
        exc: StarletteHTTPException | FastAPIHTTPException,
    ):
        """Handle HTTP exceptions (both Starlette and FastAPI)."""
        request_id, _, request_context, exception_context = self._prepare_exception_contexts(
            request, exc
        )

        status_code = getattr(exc, "status_code", 500)
        detail = getattr(exc, "detail", str(exc))

        log_msg = (
            "HTTP Exception occurred - Status Code: %s, Detail: %s, Request ID: %s, "
            "Method: %s, URL: %s, File: %s:%s"
        )
        self.exception_logger.warning(
            log_msg,
            status_code,
            detail,
            request_id,
            request_context["method"],
            request_context["url"],
            exception_context.get("file_path", "unknown"),
            exception_context.get("line_number", "unknown"),
        )

        await self._handle_audit_logging(request, str(detail), status_code, "HTTP exception")

        error_payload = {
            "detail": detail,
            "status_code": status_code,
            "request_id": request_id,
            "error_type": exception_context["exception_type"],
        }

        if 400 <= status_code < 500:
            error_payload["path"] = request_context["path"]
            error_payload["method"] = request_context["method"]

        headers = getattr(exc, "headers", None) if hasattr(exc, "headers") else None

        return JSONResponse(status_code=status_code, content=error_payload, headers=headers)

    async def _handle_validation_exception(self, request: Request, exc: RequestValidationError):
        """Handle request validation errors."""
        request_id, _, request_context, exception_context = self._prepare_exception_contexts(
            request, exc
        )

        validation_errors = exc.errors()
        sanitized_errors = []
        for error in validation_errors:
            sanitized_error = error.copy()
            if "ctx" in sanitized_error and sanitized_error["ctx"]:
                sanitized_ctx = {}
                for key, value in sanitized_error["ctx"].items():
                    sanitized_ctx[key] = str(value) if isinstance(value, Exception) else value
                sanitized_error["ctx"] = sanitized_ctx
            sanitized_errors.append(sanitized_error)

        log_msg = (
            "Request validation error occurred - Status Code: 422, Request ID: %s, "
            "Method: %s, URL: %s, File: %s:%s, Validation Errors: %s"
        )
        self.exception_logger.warning(
            log_msg,
            request_id,
            request_context["method"],
            request_context["url"],
            exception_context.get("file_path", "unknown"),
            exception_context.get("line_number", "unknown"),
            str(validation_errors),
        )

        await self._handle_audit_logging(request, str(exc), 422, "validation error")

        return JSONResponse(
            status_code=422,
            content={
                "detail": "Validation error",
                "errors": sanitized_errors,
                "status_code": 422,
                "request_id": request_id,
                "error_type": "RequestValidationError",
                "path": request_context["path"],
                "method": request_context["method"],
            },
        )

    async def _handle_unexpected_exception(self, request: Request, exc: Exception):
        """Handle unexpected exceptions."""
        (
            request_id,
            operation_name,
            request_context,
            exception_context,
        ) = self._prepare_exception_contexts(request, exc)

        error_message = str(exc)
        full_traceback = traceback.format_exc()

        log_msg = (
            "Unexpected exception occurred - Error: %s, Exception Type: %s, "
            "Request ID: %s, Method: %s, URL: %s, Operation: %s, File: %s:%s, "
            "Function: %s"
        )
        self.exception_logger.error(
            log_msg,
            error_message,
            exception_context["exception_type"],
            request_id,
            request_context["method"],
            request_context["url"],
            operation_name,
            exception_context.get("file_path", "unknown"),
            exception_context.get("line_number", "unknown"),
            exception_context.get("function_name", "unknown"),
        )

        self.exception_logger.debug(
            "Full traceback for request %s:\n%s", request_id, full_traceback
        )
        self.logger.error("Error in %s: %s", operation_name, error_message)

        await self._handle_audit_logging(request, error_message, 500, "unexpected error")

        error_response = {
            "detail": "Internal server error. Please contact support if this issue persists.",
            "status_code": 500,
            "request_id": request_id,
            "error_type": exception_context["exception_type"],
        }

        if self._should_expose_internal_details():
            error_response["error_message"] = error_message
            error_response["path"] = request_context["path"]
            error_response["method"] = request_context["method"]

        return JSONResponse(status_code=500, content=error_response)

    async def _handle_audit_logging(
        self,
        request: Request,
        error_message: str,
        status_code: int,
        context: str,
    ) -> None:
        """Handle audit logging for exceptions."""
        if not self.audit_logger:
            return
        try:
            await self.audit_logger(request, error_message, status_code, context)
        except (ValueError, TypeError, KeyError, AttributeError) as err:
            self.exception_logger.warning(
                "Audit logging failed during %s handling - data error: %s",
                context,
                str(err),
            )
        except (OSError, IOError) as err:
            self.exception_logger.warning(
                "Audit logging failed during %s handling - I/O error: %s",
                context,
                str(err),
            )
        except (RuntimeError, asyncio.CancelledError) as err:
            self.exception_logger.warning(
                "Audit logging failed during %s handling - runtime error: %s",
                context,
                str(err),
            )

    def _prepare_exception_contexts(self, request: Request, exc: Exception):
        """Prepare exception and request contexts for logging."""
        request_id = str(uuid.uuid4())
        endpoint = request.scope.get("endpoint")
        operation_name = getattr(endpoint, "__name__", str(request.url.path))
        request_context = self.extract_request_context(request)
        exception_context = self.extract_exception_context(exc)

        route = request.scope.get("route")
        metadata_attr = self.audit_metadata_attribute
        if route and hasattr(route.endpoint, metadata_attr):
            request.state.audit_metadata = getattr(route.endpoint, metadata_attr)

        log_msg = (
            "Exception handler triggered - Request ID: %s, Operation: %s, "
            "Type: %s, Method: %s, URL: %s, File: %s:%s"
        )
        self.exception_logger.info(
            log_msg,
            request_id,
            operation_name,
            exception_context["exception_type"],
            request_context["method"],
            request_context["url"],
            exception_context.get("file_path", "unknown"),
            exception_context.get("line_number", "unknown"),
        )

        return request_id, operation_name, request_context, exception_context

    def _should_expose_internal_details(self) -> bool:
        """Check if internal error details should be exposed based on environment."""
        try:
            current_env = self.environment_resolver()
        except Exception:
            current_env = "production"
        return current_env.lower() in {env.lower() for env in self.internal_envs}

    @staticmethod
    def extract_request_context(request: Request) -> dict:
        """Extract request context information for logging."""
        context = {
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "query_params": dict(request.query_params),
            "headers": dict(request.headers),
            "client_ip": request.client.host if request.client else "unknown",
            "user_agent": request.headers.get("user-agent", "unknown"),
        }

        if hasattr(request.state, "cached_body"):
            try:
                body_str = request.state.cached_body.decode("utf-8")
                context["body_preview"] = (
                    body_str[:200] + "..." if len(body_str) > 200 else body_str
                )
            except UnicodeError:
                context["body_preview"] = "[Unable to decode body - Unicode error]"
            except (ValueError, AttributeError) as err:
                context["body_preview"] = f"[Unable to decode body - {str(err)}]"

        return context

    @staticmethod
    def extract_exception_context(exc: Exception) -> dict:
        """Extract exception context information for logging."""
        _, _, exc_traceback = sys.exc_info()

        context = {
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
        }

        if exc_traceback:
            traceback_obj = exc_traceback
            while traceback_obj.tb_next:
                traceback_obj = traceback_obj.tb_next

            frame = traceback_obj.tb_frame
            context.update(
                {
                    "file_path": frame.f_code.co_filename,
                    "line_number": traceback_obj.tb_lineno,
                    "function_name": frame.f_code.co_name,
                    "module_name": frame.f_globals.get("__name__", "unknown"),
                }
            )

        return context


def register_exception_handlers(
    app: FastAPI,
    *,
    handlers: FastAPIExceptionHandlers | None = None,
) -> FastAPIExceptionHandlers:
    """Register exception handlers on the provided FastAPI app.

    Returns the handler instance for further customization if needed.
    """

    instance = handlers or FastAPIExceptionHandlers()
    instance.register(app)
    return instance


def extract_request_context(request: Request) -> dict:
    """Compatibility helper to access request context extraction."""

    return FastAPIExceptionHandlers.extract_request_context(request)


def extract_exception_context(exc: Exception) -> dict:
    """Compatibility helper to access exception context extraction."""

    return FastAPIExceptionHandlers.extract_exception_context(exc)


__all__ = [
    "FastAPIExceptionHandlers",
    "register_exception_handlers",
    "extract_request_context",
    "extract_exception_context",
]
