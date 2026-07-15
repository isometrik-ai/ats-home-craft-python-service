"""Pure helpers for CRM dashboard (no DB / Kafka imports)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from apps.user_service.app.schemas.dashboard import MyProjectDueSummary, MyProjectItem
from apps.user_service.app.schemas.enums import ProjectStatus

ProjectHealth = Literal["on_track", "at_risk"]


def project_health(
    status: str,
    priority: str,
    target_end_date: date | None,
    today: date,
) -> ProjectHealth:
    """Calculate project health based on status, priority, and target end date."""
    if status in (ProjectStatus.COMPLETED.value, ProjectStatus.CANCELLED.value):
        return "on_track"
    if priority == "urgent":
        return "at_risk"
    if target_end_date is not None and status not in (
        ProjectStatus.COMPLETED.value,
        ProjectStatus.CANCELLED.value,
        ProjectStatus.ARCHIVED.value,
    ):
        if target_end_date <= today + timedelta(days=7):
            return "at_risk"
    return "on_track"


def build_due_summary(target_end_date: date | None, today: date) -> MyProjectDueSummary:
    """Non-negative, UX-oriented deadline shape for localized labels."""
    if target_end_date is None:
        return MyProjectDueSummary(kind="none")
    offset = (target_end_date - today).days
    if offset < 0:
        return MyProjectDueSummary(kind="overdue", days=-offset)
    if offset == 0:
        return MyProjectDueSummary(kind="today")
    if offset == 1:
        return MyProjectDueSummary(kind="tomorrow")
    return MyProjectDueSummary(kind="in_days", days=offset)


def _as_optional_date(val: Any) -> date | None:
    """Convert ISO-string/date/None to date/None."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val[:10])
    return None


def serialize_my_project_row(row: dict[str, Any], today: date) -> MyProjectItem:
    """Serialize a single project row to a MyProjectItem."""
    status = ProjectStatus(str(row["status"]))
    priority = str(row["priority"] or "medium")
    target_date = _as_optional_date(row.get("target_end_date"))
    start_date = _as_optional_date(row.get("start_date"))

    return MyProjectItem(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        project_title=str(row["project_title"]),
        status=status,
        priority=priority,
        target_end_date=target_date,
        start_date=start_date,
        client_name=None,
        progress_percent=None,
        health=project_health(status.value, priority, target_date, today),
        due_summary=build_due_summary(target_date, today),
    )
