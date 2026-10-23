"""Helpers for recurring notice persistence and labels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from apps.user_service.app.db.repositories.notices_repository import NoticesRepository
from apps.user_service.app.schemas.enums import (
    NoticeRecurrenceFrequency as NoticeRecurrenceFrequencyEnum,
)
from apps.user_service.app.schemas.enums import (
    NoticeSeriesEndType,
)
from apps.user_service.app.schemas.notices import NoticeRepeatInput
from apps.user_service.app.services.notice_recurrence_calculator import (
    NoticeRecurrenceFrequency,
)
from apps.user_service.app.services.notice_recurrence_calculator import (
    NoticeSeriesEndType as RecurrenceEndType,
)
from apps.user_service.app.services.notice_recurrence_calculator import (
    compute_first_send_at,
    compute_next_send_after,
    format_repeats_label,
    parse_org_timezone,
    preview_sends,
    series_has_more_sends,
    should_end_before_send,
)


def _calculator_frequency_from_str(value: str) -> NoticeRecurrenceFrequency:
    """Map persisted/API frequency string to calculator literal."""
    if value == "weekly":
        return "weekly"
    if value == "monthly":
        return "monthly"
    raise ValueError(f"invalid recurrence frequency: {value}")


def _calculator_end_type_from_str(value: str) -> RecurrenceEndType:
    """Map persisted/API end_type string to calculator literal."""
    if value == "on_date":
        return "on_date"
    if value == "after_count":
        return "after_count"
    raise ValueError(f"invalid recurrence end_type: {value}")


def _calculator_frequency(
    frequency: NoticeRecurrenceFrequencyEnum | None,
) -> NoticeRecurrenceFrequency:
    """Map schema enum to calculator literal."""
    if frequency is None:
        return "weekly"
    return _calculator_frequency_from_str(frequency.value)


def _calculator_end_type(end_type: NoticeSeriesEndType | None) -> RecurrenceEndType:
    """Map schema enum to calculator literal."""
    if end_type is None:
        return "on_date"
    return _calculator_end_type_from_str(end_type.value)


def _required_repeat_schedule(repeat: NoticeRepeatInput) -> tuple[date, time]:
    """Return schedule fields required when repeat is enabled."""
    if repeat.starts_on is None or repeat.time_of_day is None:
        raise ValueError("starts_on and time_of_day are required when repeat is enabled")
    return repeat.starts_on, repeat.time_of_day


@dataclass(frozen=True)
class _StoredSeriesSchedule:
    """Typed notice_series columns joined onto a notice row."""

    frequency: NoticeRecurrenceFrequency
    monthly_day: int
    starts_on: date
    time_of_day: time
    end_type: RecurrenceEndType
    end_on: date | None
    end_after_sends: int | None


def _stored_series_schedule(row: dict[str, Any]) -> _StoredSeriesSchedule:
    """Parse joined series fields from a repository notice row."""
    starts_on = row.get("series_starts_on")
    time_of_day = row.get("series_time_of_day")
    if not isinstance(starts_on, date):
        raise ValueError("series_starts_on must be a date")
    if not isinstance(time_of_day, time):
        raise ValueError("series_time_of_day must be a time")
    end_on = row.get("series_end_on")
    if end_on is not None and not isinstance(end_on, date):
        raise ValueError("series_end_on must be a date when set")
    end_after = row.get("series_end_after_sends")
    end_after_sends = int(end_after) if end_after is not None else None
    return _StoredSeriesSchedule(
        frequency=_calculator_frequency_from_str(str(row["series_frequency"])),
        monthly_day=int(row["series_monthly_day"]),
        starts_on=starts_on,
        time_of_day=time_of_day,
        end_type=_calculator_end_type_from_str(str(row["series_end_type"])),
        end_on=end_on,
        end_after_sends=end_after_sends,
    )


def _series_end_type_for_insert(repeat: NoticeRepeatInput) -> str:
    """Default end_type when creating notice_series."""
    if repeat.end_type is not None:
        return repeat.end_type.value
    return "on_date"


def series_active(row: dict[str, Any]) -> bool:
    """True when notice has an active recurrence config."""
    return bool(row.get("series_frequency")) and row.get("series_repeat_ended_at") is None


def format_series_ends_label(row: dict[str, Any]) -> str | None:
    """Human label for series end condition."""
    end_type = row.get("series_end_type")
    if not end_type:
        return None
    if end_type == NoticeSeriesEndType.ON_DATE.value and row.get("series_end_on"):
        return f"Ends {row['series_end_on']}"
    if end_type == NoticeSeriesEndType.AFTER_COUNT.value and row.get("series_end_after_sends"):
        completed = int(row.get("series_sends_completed") or 0)
        total = int(row["series_end_after_sends"])
        return f"After {total} sends ({completed} completed)"
    return None


def recurrence_footer(row: dict[str, Any], repeats_label: str | None) -> str | None:
    """Compact rule line for list cards."""
    if not repeats_label:
        return None
    ends = format_series_ends_label(row)
    if ends:
        return f"{repeats_label} • {ends}"
    return repeats_label


async def load_org_tz(repo: NoticesRepository, organization_id: str) -> ZoneInfo:
    """Resolve organization timezone."""
    tz_name = await repo.fetch_organization_timezone(organization_id=organization_id)
    return parse_org_timezone(tz_name)


async def repeat_to_storage(
    repo: NoticesRepository,
    *,
    organization_id: str,
    notice_id: str,
    repeat: NoticeRepeatInput,
    start_series: bool,
) -> datetime | None:
    """Insert or replace notice_series + weekly days."""
    org_tz = await load_org_tz(repo, organization_id)
    frequency_lit = _calculator_frequency(repeat.frequency)
    frequency_str = frequency_lit
    monthly_day = repeat.monthly_day if repeat.monthly_day is not None else 1
    weekly = repeat.weekly_days or []

    next_send_at = None
    if start_series and repeat.frequency is not None:
        starts_on, time_of_day = _required_repeat_schedule(repeat)
        next_send_at = compute_first_send_at(
            starts_on=starts_on,
            time_of_day=time_of_day,
            frequency=frequency_lit,
            weekly_days=frozenset(weekly) if frequency_lit == "weekly" else None,
            monthly_day=monthly_day,
            org_tz=org_tz,
        )

    if await repo.notice_has_series(organization_id=organization_id, notice_id=notice_id):
        patch: dict[str, Any] = {
            "frequency": frequency_str,
            "monthly_day": monthly_day,
            "starts_on": repeat.starts_on,
            "time_of_day": repeat.time_of_day,
            "end_type": repeat.end_type.value if repeat.end_type else None,
            "end_on": repeat.end_on,
            "end_after_sends": repeat.end_after_sends,
        }
        if start_series:
            patch["next_send_at"] = next_send_at
            patch["repeat_ended_at"] = None
        await repo.update_notice_series(
            organization_id=organization_id,
            notice_id=notice_id,
            fields=patch,
        )
    else:
        starts_on, time_of_day = _required_repeat_schedule(repeat)
        await repo.insert_notice_series(
            organization_id=organization_id,
            notice_id=notice_id,
            frequency=frequency_str,
            monthly_day=monthly_day,
            starts_on=starts_on,
            time_of_day=time_of_day,
            end_type=_series_end_type_for_insert(repeat),
            end_on=repeat.end_on,
            end_after_sends=repeat.end_after_sends,
            next_send_at=next_send_at,
        )

    if frequency_lit == "weekly":
        await repo.replace_series_weekly_days(
            organization_id=organization_id,
            notice_id=notice_id,
            weekly_days=weekly,
        )
    else:
        await repo.replace_series_weekly_days(
            organization_id=organization_id,
            notice_id=notice_id,
            weekly_days=[],
        )
    return next_send_at


async def build_preview(
    repo: NoticesRepository,
    *,
    organization_id: str,
    repeat: NoticeRepeatInput,
    limit: int = 4,
) -> list[datetime]:
    """Next send preview for API responses."""
    if not repeat.enabled or not repeat.frequency or not repeat.starts_on or not repeat.time_of_day:
        return []
    org_tz = await load_org_tz(repo, organization_id)
    frequency_lit = _calculator_frequency(repeat.frequency)
    starts_on, time_of_day = _required_repeat_schedule(repeat)
    monthly_day = repeat.monthly_day if repeat.monthly_day is not None else 1
    weekly = repeat.weekly_days or []
    end_type_lit = _calculator_end_type(repeat.end_type)
    return preview_sends(
        starts_on=starts_on,
        time_of_day=time_of_day,
        frequency=frequency_lit,
        weekly_days=frozenset(weekly) if frequency_lit == "weekly" else None,
        monthly_day=monthly_day,
        end_type=end_type_lit,
        end_on=repeat.end_on,
        end_after_sends=repeat.end_after_sends,
        org_tz=org_tz,
        limit=limit,
    )


async def compute_next_after_delivery(
    repo: NoticesRepository,
    *,
    organization_id: str,
    notice_id: str,
    row: dict[str, Any],
    after_planned_at: datetime,
    sends_completed: int,
) -> tuple[datetime | None, bool]:
    """Return next send time and whether the series has ended."""
    org_tz = await load_org_tz(repo, organization_id)
    schedule = _stored_series_schedule(row)

    if (
        schedule.end_type == "after_count"
        and schedule.end_after_sends is not None
        and sends_completed >= schedule.end_after_sends
    ):
        return None, True

    weekly = await repo.list_series_weekly_days(
        organization_id=organization_id,
        notice_id=notice_id,
    )
    try:
        candidate = compute_next_send_after(
            after_planned_at=after_planned_at,
            time_of_day=schedule.time_of_day,
            frequency=schedule.frequency,
            weekly_days=frozenset(weekly) if schedule.frequency == "weekly" else None,
            monthly_day=schedule.monthly_day,
            org_tz=org_tz,
        )
    except ValueError:
        return None, True

    if should_end_before_send(
        end_type=schedule.end_type,
        end_on=schedule.end_on,
        end_after_sends=schedule.end_after_sends,
        sends_completed=sends_completed,
        candidate_planned_at=candidate,
        org_tz=org_tz,
    ):
        return None, True

    if not series_has_more_sends(
        end_type=schedule.end_type,
        end_on=schedule.end_on,
        end_after_sends=schedule.end_after_sends,
        sends_completed=sends_completed,
        next_send_at=candidate,
        org_tz=org_tz,
    ):
        return None, True
    return candidate, False


async def repeats_label_for_row(
    repo: NoticesRepository,
    *,
    organization_id: str,
    notice_id: str,
    row: dict[str, Any],
) -> str | None:
    """Build repeats label from stored series row."""
    if not row.get("series_frequency"):
        return None
    org_tz = await load_org_tz(repo, organization_id)
    schedule = _stored_series_schedule(row)
    weekly = await repo.list_series_weekly_days(
        organization_id=organization_id,
        notice_id=notice_id,
    )
    return format_repeats_label(
        frequency=schedule.frequency,
        weekly_days=weekly if schedule.frequency == "weekly" else None,
        monthly_day=schedule.monthly_day,
        time_of_day=schedule.time_of_day,
        org_tz=org_tz,
    )


async def recompute_next_send_from_row(
    repo: NoticesRepository,
    *,
    organization_id: str,
    notice_id: str,
    row: dict[str, Any],
) -> datetime | None:
    """Recompute next_send_at after PATCH (no sends yet or after last)."""
    org_tz = await load_org_tz(repo, organization_id)
    schedule = _stored_series_schedule(row)
    weekly = await repo.list_series_weekly_days(
        organization_id=organization_id,
        notice_id=notice_id,
    )
    sends = int(row.get("series_sends_completed") or 0)
    if sends == 0:
        return compute_first_send_at(
            starts_on=schedule.starts_on,
            time_of_day=schedule.time_of_day,
            frequency=schedule.frequency,
            weekly_days=frozenset(weekly) if schedule.frequency == "weekly" else None,
            monthly_day=schedule.monthly_day,
            org_tz=org_tz,
        )
    last = row.get("series_last_sent_at") or row.get("series_next_send_at")
    if last is None:
        return None
    next_at, _ = await compute_next_after_delivery(
        repo,
        organization_id=organization_id,
        notice_id=notice_id,
        row=row,
        after_planned_at=last,
        sends_completed=sends,
    )
    return next_at
