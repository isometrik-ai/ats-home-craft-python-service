"""Unit tests for FastAPI exception handler helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.datastructures import URL
from starlette.exceptions import HTTPException as StarletteHTTPException

from libs.shared_utils.fastapi_exception_handlers import (
    _handle_http_exception,
    _handle_validation_exception,
    register_exception_handlers,
)
from libs.shared_utils.status_codes import CustomStatusCode


def _mock_request(*, method: str = "GET", path: str = "/v1/test") -> Request:
    """Build a minimal mock Request for handler tests."""
    request = MagicMock(spec=Request)
    request.method = method
    request.url = URL(f"http://testserver{path}")
    request.headers = MagicMock()
    request.headers.get = MagicMock(return_value="en")
    return request


def test_handle_http_exception_maps_known_status_codes() -> None:
    """Known HTTP status codes should map to standard message keys."""
    request = _mock_request()
    response = _handle_http_exception(
        request,
        StarletteHTTPException(status_code=404, detail="missing"),
    )

    assert response.status_code == 404
    body = response.body.decode()
    assert CustomStatusCode.NOT_FOUND.value in body
    assert '"statusCode":404' in body


def test_handle_http_exception_includes_method_not_allowed_params() -> None:
    """405 responses should include method and path params."""
    request = _mock_request(method="PATCH", path="/v1/items/1")
    response = _handle_http_exception(
        request,
        StarletteHTTPException(status_code=405, detail="method not allowed"),
    )

    assert response.status_code == 405
    body = response.body.decode()
    assert "PATCH" in body
    assert "/v1/items/1" in body


def test_handle_http_exception_includes_retry_after_for_429() -> None:
    """429 responses with Retry-After header should pass retry_after param."""
    request = _mock_request()
    response = _handle_http_exception(
        request,
        StarletteHTTPException(
            status_code=429,
            detail="rate limited",
            headers={"Retry-After": "30"},
        ),
    )

    assert response.status_code == 429
    assert response.headers.get("Retry-After") == "30"
    assert CustomStatusCode.RATE_LIMIT_EXCEEDED.value in response.body.decode()


def test_handle_http_exception_unknown_status_uses_dynamic_key() -> None:
    """Unmapped status codes should fall back to a dynamic message key."""
    request = _mock_request()
    response = _handle_http_exception(
        request,
        StarletteHTTPException(status_code=418, detail="teapot"),
    )

    assert response.status_code == 418
    assert CustomStatusCode.BAD_REQUEST.value in response.body.decode()


def test_handle_validation_exception_missing_field() -> None:
    """Missing required fields should use missing_required_param message key."""
    request = _mock_request()
    exc = RequestValidationError(
        [
            {
                "type": "missing",
                "loc": ("body", "email"),
                "msg": "Field required",
                "input": {},
            }
        ]
    )

    response = _handle_validation_exception(request, exc)

    assert response.status_code == 422
    body = response.body.decode()
    assert "missing_required_param" in body or "email" in body
    assert CustomStatusCode.VALIDATION_ERROR.value in body


def test_handle_validation_exception_prefixes_known_headers() -> None:
    """Header-like locations should be prefixed with headers section."""
    request = _mock_request()
    exc = RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": ("authorization",),
                "msg": "Invalid token",
                "input": "bad",
            }
        ]
    )

    response = _handle_validation_exception(request, exc)

    body = response.body.decode()
    assert "headers.authorization" in body


def test_handle_validation_exception_accepts_pydantic_validation_error() -> None:
    """Raw Pydantic ValidationError should reuse the same response shape."""
    from pydantic import BaseModel

    class SampleModel(BaseModel):
        required_field: str

    request = _mock_request()
    validation_error: ValidationError | None = None
    try:
        SampleModel.model_validate({})
    except ValidationError as exc:
        validation_error = exc
    assert validation_error is not None
    response = _handle_validation_exception(request, validation_error)

    assert response.status_code == 422
    assert CustomStatusCode.VALIDATION_ERROR.value in response.body.decode()


def test_register_exception_handlers_attaches_handlers() -> None:
    """register_exception_handlers should register all expected handlers."""
    app = MagicMock()
    app.exception_handler = MagicMock(side_effect=lambda exc: lambda fn: fn)

    register_exception_handlers(app)

    registered_types = {call.args[0] for call in app.exception_handler.call_args_list}
    assert StarletteHTTPException in registered_types
    assert RequestValidationError in registered_types
    assert ValidationError in registered_types
    assert ValueError in registered_types
    assert Exception in registered_types


async def _invoke_registered_handler(app: MagicMock, exc_type: type, exc: Exception):
    """Return response from a handler registered via register_exception_handlers."""
    handlers: dict[type, object] = {}

    def capture_handler(exc_cls: type):
        def decorator(fn):
            handlers[exc_cls] = fn
            return fn

        return decorator

    app.exception_handler = capture_handler
    register_exception_handlers(app)
    handler = handlers[exc_type]
    return await handler(_mock_request(), exc)


@pytest.mark.asyncio
async def test_registered_custom_exception_handlers() -> None:
    """Custom exception handlers should return uniform error envelopes."""
    from libs.shared_utils.http_exceptions import (
        BadRequestException,
        DuplicateValueException,
        ForbiddenException,
        NotFoundException,
        RateLimitExceededException,
        UnauthorizedException,
        ValidationException,
    )

    app = MagicMock()

    not_found = await _invoke_registered_handler(
        app,
        NotFoundException,
        NotFoundException(message_key="errors.not_found", custom_code=CustomStatusCode.NOT_FOUND),
    )
    assert not_found.status_code == 404

    duplicate = await _invoke_registered_handler(
        app,
        DuplicateValueException,
        DuplicateValueException(message_key="errors.duplicate_value"),
    )
    assert duplicate.status_code == 409

    rate_limit = await _invoke_registered_handler(
        app,
        RateLimitExceededException,
        RateLimitExceededException(retry_after=30),
    )
    assert rate_limit.status_code == 429

    validation = await _invoke_registered_handler(
        app,
        ValidationException,
        ValidationException(
            message_key="errors.validation", custom_code=CustomStatusCode.VALIDATION_ERROR
        ),
    )
    assert validation.status_code == 422

    unauthorized = await _invoke_registered_handler(
        app,
        UnauthorizedException,
        UnauthorizedException(
            message_key="errors.unauthorized", custom_code=CustomStatusCode.UNAUTHORIZED
        ),
    )
    assert unauthorized.status_code == 401

    forbidden = await _invoke_registered_handler(
        app,
        ForbiddenException,
        ForbiddenException(message_key="errors.forbidden", custom_code=CustomStatusCode.FORBIDDEN),
    )
    assert forbidden.status_code == 403

    bad_request = await _invoke_registered_handler(
        app,
        BadRequestException,
        BadRequestException(
            message_key="errors.bad_request", custom_code=CustomStatusCode.BAD_REQUEST
        ),
    )
    assert bad_request.status_code == 400


@pytest.mark.asyncio
async def test_registered_value_error_and_unhandled_handlers() -> None:
    """ValueError and generic Exception handlers should map to 400/500."""
    app = MagicMock()

    value_error = await _invoke_registered_handler(app, ValueError, ValueError("bad input"))
    assert value_error.status_code == 400

    unhandled = await _invoke_registered_handler(app, Exception, RuntimeError("boom"))
    assert unhandled.status_code == 500
