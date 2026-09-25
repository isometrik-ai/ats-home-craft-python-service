"""Build engine snapshots from persisted booking rows."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from apps.user_service.app.schemas.facility_booking_config import (
    DayHours,
    FacilitySetup,
    Policies,
    PricingConfig,
)
from apps.user_service.app.services.facility_booking.defaults import (
    ensure_archetype_setup,
    normalize_setup,
)
from apps.user_service.app.services.facility_booking.types import (
    BookingUnit,
    EngineReservation,
    FacilitySnapshot,
    MaintenanceWindow,
    Participant,
    SchedulePeriod,
    SlotBlock,
)


def _as_str(value: Any) -> str | None:
    """Coerce a DB value to string when present."""
    return str(value) if value is not None else None


def snapshot_from_rows(
    config: dict[str, Any], inventory: dict[str, list[dict[str, Any]]]
) -> FacilitySnapshot:
    """Assemble a ``FacilitySnapshot`` from config + inventory table rows."""
    setup = ensure_archetype_setup(
        config["archetype"],
        normalize_setup(FacilitySetup.model_validate(config["setup"])),
    )
    closures = {row["closed_on"]: row["reason"] for row in inventory.get("facility_closures", [])}
    return FacilitySnapshot(
        id=str(config["facility_id"]),
        name=str(config.get("facility_name") or ""),
        archetype=str(config["archetype"]),
        slot_minutes=int(config["slot_minutes"]),
        hours=[DayHours.model_validate(item) for item in config["default_hours"]],
        pricing=PricingConfig.model_validate(config["pricing"]),
        policies=Policies.model_validate(config["policies"]),
        setup=setup,
        units=[
            BookingUnit(
                id=str(row["id"]),
                name=row["name"],
                room_type=row.get("room_type"),
                features=tuple(row.get("features") or ()),
                tower_id=_as_str(row.get("tower_id")),
                floor_id=_as_str(row.get("floor_id")),
                sort_order=int(row.get("sort_order") or 0),
                active=bool(row.get("active", True)),
            )
            for row in inventory.get("facility_booking_units", [])
            if row.get("active", True)
        ],
        closures=closures,
        maintenance=[
            MaintenanceWindow(
                id=str(row["id"]),
                on_date=row["on_date"],
                from_min=int(row["from_min"]),
                to_min=int(row["to_min"]),
                note=row["note"],
            )
            for row in inventory.get("facility_maintenance_windows", [])
        ],
        schedules=[
            SchedulePeriod(
                id=str(row["id"]),
                name=row["name"],
                starts_on=row["starts_on"],
                ends_on=row["ends_on"],
                hours=[DayHours.model_validate(item) for item in row["hours"]],
            )
            for row in inventory.get("facility_schedule_periods", [])
        ],
        slot_blocks=[
            SlotBlock(
                id=str(row["id"]),
                starts_on=row["starts_on"],
                from_min=int(row["from_min"]),
                to_min=int(row["to_min"]),
                reason=row["reason"],
                ends_on=row.get("ends_on"),
                unit_id=_as_str(row.get("unit_id")),
                category=row.get("category"),
            )
            for row in inventory.get("facility_slot_blocks", [])
        ],
        description=str(config.get("description") or ""),
        accepting_bookings=bool(config.get("accepting_bookings", True)),
    )


def engine_reservation(
    row: dict[str, Any], participants: list[dict[str, Any]] | None = None
) -> EngineReservation:
    """Map a reservation row (plus optional participants) to the engine type."""
    quote = row.get("quote") or {}
    return EngineReservation(
        id=str(row["id"]),
        facility_id=str(row["facility_id"]),
        unit_id=_as_str(row.get("unit_id")),
        local_date=row["local_date"],
        end_local_date=row["end_local_date"],
        start_min=int(row["start_min"]),
        end_min=int(row["end_min"]),
        host_contact_id=str(row["host_contact_id"]),
        status=str(row["status"]),
        participants=[
            Participant(
                kind=str(item["kind"]),
                name=item["name"],
                contact_id=_as_str(item.get("contact_id")),
            )
            for item in (participants or [])
        ],
        quote_total=int(quote.get("total") or 0),
        host_name=str(row.get("host_name") or ""),
    )


def week_bounds(local_date: date) -> tuple[date, date]:
    """Monday-start week containing ``local_date``.

    Sunday-first JS week handling lives in the availability engine.
    """
    from apps.user_service.app.services.facility_booking.availability import week_start

    start = week_start(local_date)
    return start, start + timedelta(days=6)
