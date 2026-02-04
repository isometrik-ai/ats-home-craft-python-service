"""Shared response factory utilities for FastAPI services."""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.responses import Response

from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.translations import translator

T = TypeVar("T")


class ResponseStatus(str, Enum):
    """High-level api response status."""

    SUCCESS = "success"
    ERROR = "error"


class ApiResponse(BaseModel):
    """Base API response payload."""

    status: ResponseStatus = Field(..., description="Response status")
    message: str = Field(..., description="Response message")
    statusCode: int = Field(..., description="HTTP status code")
    code: CustomStatusCode = Field(..., description="Custom status code")


class DataResponse(ApiResponse):
    """Response with arbitrary payload."""

    data: T = Field(None, description="Response data")


class ErrorResponse(BaseModel):
    """Structured error payload."""

    status: ResponseStatus
    message: str
    statusCode: int
    code: CustomStatusCode
    errors: list[dict[str, Any]] | None = None


class ListResponse(ApiResponse):
    """Paginated list response."""

    data: list[T] = Field([], description="List of items")
    total: int = Field(0, description="Total number of items")
    page: int = Field(1, description="Current page number")
    page_size: int = Field(10, description="Number of items per page")
    total_pages: int = Field(0, description="Total number of pages")


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles datetime serialization."""

    def default(self, o):  # type: ignore[override]
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def _serialize_payload(payload: BaseModel) -> dict[str, Any]:
    """Serialize a pydantic payload with datetime support."""

    return json.loads(json.dumps(payload.model_dump(exclude_none=True), cls=DateTimeEncoder))


def success_response(
    request: Request,
    message_key: str,
    status_code: int = 200,
    custom_code: CustomStatusCode = CustomStatusCode.SUCCESS,
    data: Any = None,
    params: dict[str, Any] | None = None,
) -> Response | JSONResponse:
    """Create a standard success response."""

    # RFC-compliant: 204/304 responses MUST NOT include a message body.
    # Returning a JSONResponse with these status codes can lead to protocol-level
    # Content-Length mismatches under some ASGI server/middleware combinations.
    if status_code in (204, 304):
        return Response(status_code=status_code)

    language = request.headers.get("lan", "en")
    message = translator.get(message_key, language, **(params or {}))

    if data is not None:
        payload = DataResponse(
            status=ResponseStatus.SUCCESS,
            message=message,
            statusCode=status_code,
            code=custom_code,
            data=data,
        )
    else:
        payload = ApiResponse(
            status=ResponseStatus.SUCCESS,
            message=message,
            statusCode=status_code,
            code=custom_code,
        )

    return JSONResponse(status_code=status_code, content=_serialize_payload(payload))


def error_response(
    request: Request,
    message_key: str,
    status_code: int = 400,
    custom_code: CustomStatusCode = CustomStatusCode.BAD_REQUEST,
    errors: list[dict[str, Any]] | None = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Create a standard error response."""

    language = request.headers.get("lan", "en")
    message = translator.get(message_key, language, **(params or {}))

    payload = ErrorResponse(
        status=ResponseStatus.ERROR,
        message=message,
        statusCode=status_code,
        code=custom_code,
        errors=errors,
    )

    return JSONResponse(
        status_code=status_code,
        content=_serialize_payload(payload),
        headers=headers,
    )


def list_response(
    request: Request,
    items: list[Any],
    total: int,
    *,
    message_key: str = "success.retrieved",
    page: int = 1,
    page_size: int = 10,
    status_code: int = 200,
    custom_code: CustomStatusCode = CustomStatusCode.SUCCESS,
    params: dict[str, Any] | None = None,
) -> JSONResponse:
    """Create a standard paginated list response."""

    language = request.headers.get("lan", "en")
    message = translator.get(message_key, language, **(params or {}))

    page_size = max(page_size, 1)
    total_pages = (total + page_size - 1) // page_size if page_size else 0

    payload = ListResponse(
        status=ResponseStatus.SUCCESS,
        message=message,
        statusCode=status_code,
        code=custom_code,
        data=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )

    return JSONResponse(status_code=status_code, content=_serialize_payload(payload))


__all__ = [
    "ApiResponse",
    "DataResponse",
    "DateTimeEncoder",
    "ErrorResponse",
    "ListResponse",
    "ResponseStatus",
    "CustomStatusCode",
    "success_response",
    "error_response",
    "list_response",
    "translator",
]
