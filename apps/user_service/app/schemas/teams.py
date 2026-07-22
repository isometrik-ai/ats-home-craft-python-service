"""Teams Management Schemas Module

This module contains all Pydantic models and schemas related to team management.
These schemas are used for request/response validation and API documentation.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from apps.user_service.app.schemas.enums import TeamRoles

SAFE_NAME_REGEX = re.compile(r"^[A-Za-z0-9 _-]+$")

# example strings
EXAMPLE_TEAM_NAME = "Legal Team"
EXAMPLE_TEAM_DESCRIPTION = "Core legal practitioners handling cases"
EXAMPLE_TEAM_DESC_SHORT = "Team description"
EXAMPLE_DEFAULT_TIMESTAMP = "2024-01-15T10:30:00Z"
EXAMPLE_USER_ID = "550e8400-e29b-41d4-a716-446655440000"


class TeamMemberInput(BaseModel):
    """Team member input with optional per-member role."""

    user_id: str = Field(..., description="Organization member user UUID")
    role: TeamRoles = Field(
        default=TeamRoles.MEMBER,
        description="Team member role (defaults to MEMBER)",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": EXAMPLE_USER_ID,
                "role": TeamRoles.LEAD.value,
            }
        }
    )


class CreateTeamRequest(BaseModel):
    """Request model for creating a new team"""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Team name (must be unique within organization)",
    )
    description: str | None = Field(None, max_length=1000, description=EXAMPLE_TEAM_DESC_SHORT)
    members: list[TeamMemberInput] | None = Field(
        None,
        description="Team members with optional per-member role (defaults to MEMBER)",
    )

    @field_validator("members")
    @classmethod
    def validate_unique_members(
        cls, members: list[TeamMemberInput] | None
    ) -> list[TeamMemberInput] | None:
        """Reject duplicate user_id entries in members."""
        if not members:
            return members
        user_ids = [member.user_id for member in members]
        if len(user_ids) != len(set(user_ids)):
            raise ValueError("Duplicate user_id values are not allowed in members")
        return members

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
                "members": [
                    {
                        "user_id": EXAMPLE_USER_ID,
                        "role": TeamRoles.LEAD.value,
                    },
                    {
                        "user_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    },
                ],
            }
        }
    )


class UpdateTeamRequest(BaseModel):
    """Request model for updating an existing team"""

    name: str | None = Field(None, min_length=1, max_length=255, description="Updated team name")
    description: str | None = Field(None, max_length=1000, description="Updated team description")
    members: list[TeamMemberInput] | None = Field(
        None,
        description=(
            "Full member list with per-member roles. If provided, syncs membership "
            "(add/remove) and updates roles for all listed members. "
            "Empty array removes all members."
        ),
    )

    @field_validator("members")
    @classmethod
    def validate_unique_members(
        cls, members: list[TeamMemberInput] | None
    ) -> list[TeamMemberInput] | None:
        """Reject duplicate user_id entries in members."""
        if not members:
            return members
        user_ids = [member.user_id for member in members]
        if len(user_ids) != len(set(user_ids)):
            raise ValueError("Duplicate user_id values are not allowed in members")
        return members

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
                "members": [
                    {
                        "user_id": EXAMPLE_USER_ID,
                        "role": TeamRoles.PROJECT_LEAD.value,
                    },
                    {
                        "user_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                        "role": TeamRoles.MEMBER.value,
                    },
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


# ============================================================================
# DATABASE INPUT MODELS
# ============================================================================
class MemberData(BaseModel):
    """Model for team member data passed to the repository."""

    member_id: str = Field(..., description="Member user ID")
    role: TeamRoles = Field(
        default=TeamRoles.MEMBER,
        description="Team member role",
    )
    additional_data: dict[str, Any] | None = Field(
        None,
        description="Optional extra member metadata (allocation, hourly_rate, etc.)",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "member_id": "550e8400-e29b-41d4-a716-446655440002",
                "role": TeamRoles.MEMBER.value,
                "additional_data": {
                    "allocation_percentage": 60,
                    "hourly_rate": 150.00,
                },
            }
        }
    )


class TeamDbIn(BaseModel):
    """Input model for creating a new team in database"""

    organization_id: str = Field(..., description="Organization UUID")
    name: str = Field(..., min_length=1, max_length=255, description="Team name")
    description: str | None = Field(None, max_length=1000, description="Team description")
    created_by: str = Field(..., description="User ID creating the team")
    member_data: list[MemberData] | None = Field(
        None, description="Initial team member data with additional_data"
    )
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "organization_id": "550e8400-e29b-41d4-a716-446655440000",
                "name": EXAMPLE_TEAM_NAME,
                "description": EXAMPLE_TEAM_DESCRIPTION,
                "created_by": "550e8400-e29b-41d4-a716-446655440001",
                "member_data": [
                    {
                        "member_id": "550e8400-e29b-41d4-a716-446655440002",
                        "additional_data": None,
                    }
                ],
            }
        }
    )


class TeamDbUpdate(BaseModel):
    """Input model for updating an existing team in database"""

    team_id: str = Field(..., description="Team UUID to update")
    organization_id: str = Field(..., description="Organization UUID")
    added_by: str = Field(..., description="User ID making the changes")
    name: str | None = Field(None, min_length=1, max_length=255, description="Updated team name")
    description: str | None = Field(None, max_length=1000, description="Updated team description")
    members_to_add: list[MemberData] | None = Field(
        None, description="Members to add with per-member roles"
    )
    members_to_remove: list[str] | None = Field(None, description="Member IDs to remove from team")
    members_to_update: list[MemberData] | None = Field(
        None, description="Existing members whose roles should be updated"
    )
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "team_id": "550e8400-e29b-41d4-a716-446655440000",
                "organization_id": "550e8400-e29b-41d4-a716-446655440000",
                "added_by": "550e8400-e29b-41d4-a716-446655440001",
                "name": "Updated Team Name",
                "description": "Updated description",
                "members_to_add": ["550e8400-e29b-41d4-a716-446655440002"],
                "members_to_remove": ["550e8400-e29b-41d4-a716-446655440003"],
            }
        }
    )


class TeamDbDelete(BaseModel):
    """Input model for deleting a team in database"""

    team_id: str = Field(..., description="Team UUID to delete")
    organization_id: str = Field(..., description="Organization UUID")
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "team_id": "550e8400-e29b-41d4-a716-446655440000",
                "organization_id": "550e8400-e29b-41d4-a716-446655440000",
            }
        }
    )
