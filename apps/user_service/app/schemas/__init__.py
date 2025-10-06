# pylint: disable=invalid-name,E0213,C0301
"""
Schemas Module

This module contains all Pydantic models and schemas related to user management.
These schemas are used for request/response validation and API documentation.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""
from fastapi import HTTPException, status

def _bad_request(detail: str) -> None:
    """Raise a standardized HTTP 400 error with the given detail.

    Centralizing this avoids repetition across validation branches.
    """
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
