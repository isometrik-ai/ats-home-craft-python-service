"""Shared FastAPI exception handler utilities."""

from fastapi import FastAPI
from fastapi import HTTPException as FastAPIHTTPException
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from libs.shared_utils.response_factory import error_response
from libs.shared_utils.status_codes import CustomStatusCode


class FastAPIExceptionHandlers:
    """Configurable exception handlers for FastAPI apps."""

    def register(self, app: FastAPI) -> None:
        """Register handlers on the provided FastAPI instance."""

        @app.exception_handler(StarletteHTTPException)
        async def http_exception_handler(request: Request, exc: StarletteHTTPException):
            return self._handle_http_exception(request, exc)

        @app.exception_handler(FastAPIHTTPException)
        async def fastapi_http_exception_handler(request: Request, exc: FastAPIHTTPException):
            return self._handle_http_exception(request, exc)

        @app.exception_handler(RequestValidationError)
        async def validation_exception_handler(request: Request, exc: RequestValidationError):
            return self._handle_validation_exception(request, exc)

        @app.exception_handler(Exception)
        async def unexpected_exception_handler(request: Request, exc: Exception):
            return self._handle_unexpected_exception(request, exc)

        @app.exception_handler(ValidationError)
        async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
            return self._handle_validation_exception(request, exc)

        @app.exception_handler(ValueError)
        async def value_error_exception_handler(request: Request, exc: ValueError):
            return self._handle_value_error_exception(request, exc)

    def _handle_http_exception(
        self,
        request: Request,
        exc: StarletteHTTPException | FastAPIHTTPException,
    ):
        """Handle HTTP exceptions (both Starlette and FastAPI)."""
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

    def _handle_validation_exception(
        self, request: Request, exc: RequestValidationError | ValidationError
    ):
        """Handle request validation errors."""
        detailed_errors = []
        first_error = exc.errors()[0] if exc.errors() else None
        first_error_msg = (
            first_error.get("msg", "Unknown error") if first_error else "Unknown validation error"
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
            loc_parts = [str(loc) for loc in error.get("loc", [])]

            # If location lacks a section prefix and looks like one of our headers, prefix it
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

    def _handle_value_error_exception(self, request: Request, exc: ValueError):
        """Handle value error exceptions."""
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

    def _handle_unexpected_exception(self, request: Request, exc: Exception):
        """Handle unexpected exceptions."""

        return error_response(
            request=request,
            message_key="errors.internal_server_error",
            status_code=500,
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            errors=[{"field": None, "type": "unexpected_error", "msg": str(exc)}],
        )


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


__all__ = [
    "FastAPIExceptionHandlers",
    "register_exception_handlers",
]
