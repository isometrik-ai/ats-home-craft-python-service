"""Shared helpers for populating request.state audit fields."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from apps.user_service.app.dependencies.audit_logs.audit_logs_utils import (
    extract_project_id_from_data,
)
from apps.user_service.app.utils.common_utils import UserContext


def set_audit_context(
    request: Request,
    user_context: UserContext,
    *,
    table: str,
    description: str,
    requested_id: str = "",
    project_id: str | None = None,
    risk_level: str = "low",
    old_data: Any | None = None,
    new_data: Any | None = None,
) -> None:
    """Populate request.state audit fields consumed by the audit decorator."""
    request.state.audit_table = table
    request.state.audit_requested_id = requested_id
    request.state.audit_description = description
    request.state.audit_risk_level = risk_level
    resolved_project_id = (
        project_id
        or extract_project_id_from_data(new_data)
        or extract_project_id_from_data(old_data)
    )
    if resolved_project_id:
        request.state.audit_project_id = resolved_project_id
    request.state.audit_user_context = {
        "user_id": user_context.user_id,
        "user_email": user_context.email,
        "organization_id": user_context.organization_id,
    }
    if old_data is not None:
        request.state.raw_audit_old_data = old_data
    if new_data is not None:
        request.state.raw_audit_new_data = new_data
