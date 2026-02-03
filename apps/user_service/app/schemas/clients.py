"""Client Management Schemas Module.

This module contains Pydantic models for client management operations.
"""

from pydantic import BaseModel, Field


class CreateClientFromUserRequest(BaseModel):
    """Request schema for creating a client from user ID."""

    user_id: str = Field(..., description="User ID from auth.users table")
    organization_id: str = Field(..., description="Organization ID")
