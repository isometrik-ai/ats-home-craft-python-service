"""Activity feed schemas.

These schemas represent a derived, UI-friendly view of audit logs.
One audit log entry can map to multiple `ActivityItem`s when multiple fields were changed.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActivityActor(BaseModel):
    """Who performed the action."""

    user_id: str | None = Field(None, description="Actor user id")
    name: str = Field(..., description="Actor display name")
    email: str | None = Field(None, description="Actor email")


class ActivityItem(BaseModel):
    """Single UI-friendly activity entry (flattened)."""

    id: str = Field(..., description="Stable id for this activity row")
    audit_log_id: str = Field(..., description="Source audit_log id")
    timestamp: str = Field(..., description="ISO timestamp")
    table_name: str = Field(..., description="Audit table name (module)")
    record_id: str = Field(..., description="Requested record id (e.g. lead id)")
    action_type: str = Field(..., description="CREATE / UPDATE / DELETE / ...")
    actor: ActivityActor = Field(..., description="Actor details")

    field: str | None = Field(None, description="Field that changed (flattened)")
    old_value: Any | None = Field(None, description="Old value for the field")
    new_value: Any | None = Field(None, description="New value for the field")

    old_display_value: str | None = Field(
        None,
        description="Optional human-friendly value (e.g., stage/company/owner name)",
    )
    new_display_value: str | None = Field(
        None,
        description="Optional human-friendly value (e.g., stage/company/owner name)",
    )

    # Frontend can build messages using actor/field/values;
    message: str | None = Field(None, description="Optional user-friendly message for UI")

    model_config = ConfigDict(extra="ignore")
