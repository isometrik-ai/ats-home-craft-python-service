"""Entity lists (grouping) schemas.

These schemas back the Lists feature used to group contacts, companies, or leads into
named lists within an organization.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums import EntityListStatus, EntityType


class CreateEntityListRequest(BaseModel):
    """Request body to create a list."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    entity_type: EntityType
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=50)
    ids: list[str] | None = Field(
        default=None,
        description="Optional initial member entity IDs to add to the list.",
        max_length=1000,
    )


class UpdateEntityListRequest(BaseModel):
    """Request body to update list metadata and membership."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=50)
    status: EntityListStatus | None = Field(
        default=None,
        description="List status (active/inactive). Cannot be set to deleted via this endpoint.",
    )
    add_ids: list[str] = Field(default_factory=list, max_length=1000)
    remove_ids: list[str] = Field(default_factory=list, max_length=1000)


class BulkMembershipResult(BaseModel):
    """Result for bulk membership mutations."""

    model_config = ConfigDict(extra="ignore")

    requested: int
    added: int
    removed: int = 0
    already_present: int
    invalid_ids: list[str] = Field(default_factory=list)


class EntityListSummary(BaseModel):
    """List summary item for the list index UI."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    name: str
    entity_type: EntityType
    status: EntityListStatus
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    total_items: int = 0
    enriched: int = 0
    pending: int = 0
    failed: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class EntityListDetails(EntityListSummary):
    """List details including member entities."""

    items: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "BulkMembershipResult",
    "CreateEntityListRequest",
    "EntityListDetails",
    "EntityListSummary",
    "EntityListStatus",
    "EntityType",
    "UpdateEntityListRequest",
]
