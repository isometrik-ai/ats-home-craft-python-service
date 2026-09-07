"""Shared API helpers for work_order_service."""

from fastapi import status as http_status

COMMON_ERROR_RESPONSES: dict[int | str, dict] = {
    401: {"description": "Unauthorized."},
    403: {"description": "Forbidden."},
    404: {"description": "Not found."},
    422: {"description": "Validation error."},
    500: {"description": "Internal server error."},
}


def ok_response(
    model: type,
    description: str,
    *,
    status_code: int = http_status.HTTP_200_OK,
) -> dict[int | str, dict]:
    """Build OpenAPI responses for a successful JSON envelope."""
    return {
        **COMMON_ERROR_RESPONSES,
        status_code: {"model": model, "description": description},
    }


def created_response(model: type, description: str) -> dict[int | str, dict]:
    """OpenAPI responses for HTTP 201 create endpoints."""
    return ok_response(model, description, status_code=http_status.HTTP_201_CREATED)
