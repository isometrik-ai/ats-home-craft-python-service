"""Teams Management Schemas Module

This module contains all Pydantic models and schemas related to team management.
These schemas are used for request/response validation and API documentation.
"""

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TeamRoles(str, Enum):
    """Team member roles"""

    LEAD = "LEAD"
    MEMBER = "MEMBER"


SAFE_NAME_REGEX = re.compile(r"^[A-Za-z0-9 _-]+$")

# example strings
EXAMPLE_TEAM_NAME = "Legal Team"
EXAMPLE_TEAM_DESCRIPTION = "Core legal practitioners handling cases"
EXAMPLE_TEAM_DESC_SHORT = "Team description"
EXAMPLE_DEFAULT_TIMESTAMP = "2024-01-15T10:30:00Z"


class CreateTeamRequest(BaseModel):
    """Request model for creating a new team"""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Team name (must be unique within organization)",
    )
    description: str | None = Field(None, max_length=1000, description=EXAMPLE_TEAM_DESC_SHORT)
    member_ids: list[str] | None = Field(
        None, description="List of user IDs to add as team members"
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        """Validates name of team"""
        name = name.strip()
        if not SAFE_NAME_REGEX.match(name):
            raise ValueError(
                "Name contains invalid characters. Allowed: letters, numbers, spaces, -, _"
            )
        return name

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": EXAMPLE_TEAM_NAME,
                "description": EXAMPLE_TEAM_DESCRIPTION,
                "member_ids": [
                    "550e8400-e29b-41d4-a716-446655440000",
                    "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                ],
            }
        }
    )


class UpdateTeamRequest(BaseModel):
    """Request model for updating an existing team"""

    name: str | None = Field(None, min_length=1, max_length=255, description="Updated team name")
    description: str | None = Field(None, max_length=1000, description="Updated team description")
    member_ids: list[str] | None = Field(
        None,
        description=(
            "Updated list of user IDs. If provided, replaces all existing members. "
            "Empty array removes all members."
        ),
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str | None) -> str | None:
        """Validate team name format if provided."""
        if name is None:
            return name
        if not name or not name.strip():
            raise ValueError("Team name cannot be empty")
        return name.strip()

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated Legal Team",
                "description": "Updated description",
                "member_ids": [
                    "550e8400-e29b-41d4-a716-446655440000",
                    "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "7ca8c920-0ebe-22e2-91c5-557766551111",
                ],
            }
        }
    )


class TeamMemberItem(BaseModel):
    """Model for team member information"""

    user_id: str = Field(..., description="User ID")
    name: str | None = Field(None, description="User's full name")
    email: str | None = Field(None, description="User's email address")
    role: TeamRoles = Field(TeamRoles.MEMBER, description="User's role in Team")
    added_at: str = Field(..., description="ISO timestamp when member was added to team")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "550e8400-e29b-41d4-a716-446655440000",
                "name": "Rahul Sharma",
                "email": "rahul.sharma@ross.ai",
                "role": TeamRoles.LEAD,
                "added_at": EXAMPLE_DEFAULT_TIMESTAMP,
            }
        }
    )


class TeamItem(BaseModel):
    """Model for team information in lists"""

    id: str = Field(..., description="Unique identifier for the team")
    name: str = Field(..., description="Team name")
    description: str | None = Field(None, description=EXAMPLE_TEAM_DESC_SHORT)
    member_count: int = Field(..., description="Number of members in the team")
    created_at: str = Field(..., description="ISO timestamp when team was created")
    updated_at: str = Field(..., description="ISO timestamp when team was last updated")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": EXAMPLE_TEAM_NAME,
                "description": EXAMPLE_TEAM_DESCRIPTION,
                "member_count": 3,
                "created_at": EXAMPLE_DEFAULT_TIMESTAMP,
                "updated_at": EXAMPLE_DEFAULT_TIMESTAMP,
            }
        }
    )


class TeamDetailItem(BaseModel):
    """Model for detailed team information"""

    id: str = Field(..., description="Unique identifier for the team")
    name: str = Field(..., description="Team name")
    description: str | None = Field(None, description=EXAMPLE_TEAM_DESC_SHORT)
    members: list[TeamMemberItem] = Field(default_factory=list, description="List of team members")
    created_at: str = Field(..., description="ISO timestamp when team was created")
    updated_at: str = Field(..., description="ISO timestamp when team was last updated")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "name": EXAMPLE_TEAM_NAME,
                "description": EXAMPLE_TEAM_DESCRIPTION,
                "members": [
                    {
                        "user_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                        "name": "Rahul Sharma",
                        "email": "rahul.sharma@ross.ai",
                        "role": TeamRoles.LEAD,
                        "added_at": EXAMPLE_DEFAULT_TIMESTAMP,
                    }
                ],
                "created_at": EXAMPLE_DEFAULT_TIMESTAMP,
                "updated_at": EXAMPLE_DEFAULT_TIMESTAMP,
            }
        }
    )


class TeamsListResponse(BaseModel):
    """Response model for teams list operations"""

    data: list[TeamItem] = Field(..., description="List of teams")
    total_count: int = Field(..., description="Total number of teams")
    page: int = Field(..., description="Current page number")
    page_size: int = Field(..., description="Number of items per page")

    model_config = ConfigDict(
        json_schema_extra={"example": {"data": [], "total_count": 0, "page": 1, "page_size": 50}}
    )


class TeamDetailResponse(BaseModel):
    """Response model for team detail operations"""

    data: TeamDetailItem = Field(..., description="Detailed team information")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "data": {
                    "id": "550e8400-e29b-41d4-a716-446655440000",
                    "name": EXAMPLE_TEAM_NAME,
                    "description": EXAMPLE_TEAM_DESCRIPTION,
                    "members": [],
                    "created_at": EXAMPLE_DEFAULT_TIMESTAMP,
                    "updated_at": EXAMPLE_DEFAULT_TIMESTAMP,
                }
            }
        }
    )
