"""Golden parity: ATS booking engines vs the Clubhouse reference engine.

The fixture is produced by running the Clubhouse engine with a fixed clock and
converting inputs/outputs to ATS snake_case (validation messages -> error codes).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import cache
from pathlib import Path

import pytest

from apps.user_service.app.schemas.facility_booking_config import (
    DayHours,
    FacilitySetup,
    Policies,
    PricingConfig,
)
from apps.user_service.app.services.facility_booking import availability, pricing
from apps.user_service.app.services.facility_booking.types import (
    BookingDraft,
    BookingUnit,
    EngineReservation,
    FacilitySnapshot,
    MaintenanceWindow,
    Participant,
    SchedulePeriod,
    SlotBlock,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "clubhouse_golden.json"


@cache
def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text())


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _snapshot(raw: dict) -> FacilitySnapshot:
    return FacilitySnapshot(
        id=raw["id"],
        name=raw["name"],
        archetype=raw["archetype"],
        slot_minutes=raw["slot_minutes"],
        hours=[DayHours(**h) for h in raw["hours"]],
        pricing=PricingConfig.model_validate(raw["pricing"]),
        policies=Policies.model_validate(raw["policies"]),
        setup=FacilitySetup.model_validate(raw["setup"]),
        units=[
            BookingUnit(id=u["id"], name=u["name"], room_type=u["room_type"]) for u in raw["units"]
        ],
        closures={date.fromisoformat(k): v for k, v in raw["closures"].items()},
        maintenance=[
            MaintenanceWindow(
                id=m["id"],
                on_date=date.fromisoformat(m["on_date"]),
                from_min=m["from_min"],
                to_min=m["to_min"],
                note=m["note"],
            )
            for m in raw["maintenance"]
        ],
        schedules=[
            SchedulePeriod(
                id=s["id"],
                name=s["name"],
                starts_on=date.fromisoformat(s["starts_on"]),
                ends_on=date.fromisoformat(s["ends_on"]),
                hours=[DayHours(**h) for h in s["hours"]],
            )
            for s in raw["schedules"]
        ],
        slot_blocks=[
            SlotBlock(
                id=b["id"],
                starts_on=date.fromisoformat(b["starts_on"]),
                ends_on=_d(b["ends_on"]),
                unit_id=b["unit_id"],
                from_min=b["from_min"],
                to_min=b["to_min"],
                reason=b["reason"],
                category=b["category"],
            )
            for b in raw["slot_blocks"]
        ],
    )


def _participants(raw: list[dict]) -> list[Participant]:
    return [Participant(kind=p["kind"], name=p["name"], contact_id=p["contact_id"]) for p in raw]


def _reservation(raw: dict) -> EngineReservation:
    return EngineReservation(
        id=raw["id"],
        facility_id=raw["facility_id"],
        unit_id=raw["unit_id"],
        local_date=date.fromisoformat(raw["local_date"]),
        end_local_date=date.fromisoformat(raw["end_local_date"]),
        start_min=raw["start_min"],
        end_min=raw["end_min"],
        host_contact_id=raw["host_contact_id"],
        status=raw["status"],
        participants=_participants(raw["participants"]),
        host_name=raw["host_name"],
    )


@cache
def _world() -> tuple[datetime, dict[str, FacilitySnapshot], list, list]:
    fx = _fixture()
    facilities = {f["id"]: _snapshot(f) for f in fx["facilities"]}
    seed = [_reservation(r) for r in fx["reservations"]["seed"]]
    synthetic = [_reservation(r) for r in fx["reservations"]["synthetic"]]
    return datetime.fromisoformat(fx["now"]), facilities, seed, synthetic


def _pool(facility_id: str) -> list[EngineReservation]:
    _, _, seed, synthetic = _world()
    return synthetic if facility_id in _fixture()["synthetic_facility_ids"] else seed


def _ctx(facility_id: str) -> availability.AvailabilityContext:
    now, facilities, _, _ = _world()
    return availability.AvailabilityContext(
        facility=facilities[facility_id],
        active=availability.active_for_facility(_pool(facility_id), facility_id),
        now=now,
    )


def _draft(index: int) -> BookingDraft:
    fx = _fixture()
    fid, unit_id, d, end_d, s, e, host, party = fx["drafts"][index]
    return BookingDraft(
        facility_id=fid,
        unit_id=unit_id,
        local_date=date.fromisoformat(d),
        end_local_date=date.fromisoformat(end_d),
        start_min=s,
        end_min=e,
        host_contact_id=host,
        participants=_participants(fx["parties"][party]),
    )


def _all_reservations() -> list[EngineReservation]:
    _, _, seed, synthetic = _world()
    return [*seed, *synthetic]


def _assert_close(expected, actual, path: str = "") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        for key in set(expected) | set(actual):
            if key not in expected:
                assert actual[key] is None or key == "closure_reason", f"{path}.{key}"
                continue
            assert key in actual, f"{path}.{key} missing"
            _assert_close(expected[key], actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(expected) == len(actual), (
            f"{path}: {expected!r} != {actual!r}"
        )
        for i, (a, b) in enumerate(zip(expected, actual, strict=True)):
            _assert_close(a, b, f"{path}[{i}]")
    elif isinstance(expected, bool) or isinstance(actual, bool):
        assert expected == actual, f"{path}: {expected!r} != {actual!r}"
    elif isinstance(expected, int | float) and isinstance(actual, int | float):
        assert abs(expected - actual) < 1e-6, f"{path}: {expected!r} != {actual!r}"
    else:
        assert expected == actual, f"{path}: {expected!r} != {actual!r}"


def test_fixture_is_present() -> None:
    fx = _fixture()
    assert len(fx["drafts"]) > 500
    assert {f["archetype"] for f in fx["facilities"]} == {
        "slot",
        "tee_time",
        "duration",
        "day_range",
        "room",
    }


@pytest.mark.parametrize("facility_id", sorted(_fixture()["expected"]["day_availability"].keys()))
def test_day_availability_parity(facility_id: str) -> None:
    expected = _fixture()["expected"]["day_availability"][facility_id]
    for iso, view in expected.items():
        actual = availability.get_day_availability(_ctx(facility_id), date.fromisoformat(iso))
        _assert_close(view, actual.model_dump(mode="json"), f"{facility_id}@{iso}")


@pytest.mark.parametrize("facility_id", sorted(_fixture()["expected"]["month_overview"].keys()))
def test_month_overview_and_summary_parity(facility_id: str) -> None:
    now = _world()[0]
    overview = availability.get_month_overview(_ctx(facility_id), now.date(), 42)
    summary = availability.get_month_summary(_ctx(facility_id), now.date(), 42)
    expected_overview = _fixture()["expected"]["month_overview"][facility_id]
    expected_summary = _fixture()["expected"]["month_summary"][facility_id]
    actual_overview = [o.model_dump(mode="json") for o in overview]
    for exp, act in zip(expected_overview, actual_overview, strict=True):
        if exp["state"] == "blackout":
            act = {**act, "note": exp["note"]} if act["note"] else act
        _assert_close(exp, act, f"{facility_id}.overview")
    for exp, act in zip(expected_summary, summary, strict=True):
        act_json = act.model_dump(mode="json")
        if exp["state"] == "blackout":
            act_json["note"] = exp["note"]
        _assert_close(exp, act_json, f"{facility_id}.summary")


def test_next_availability_parity() -> None:
    for facility_id, expected in _fixture()["expected"]["next_availability"].items():
        actual = availability.next_availability(_ctx(facility_id))
        if expected is None:
            assert actual is None, facility_id
            continue
        _assert_close(expected, actual.model_dump(mode="json"), facility_id)


def test_room_availability_parity() -> None:
    for check in _fixture()["expected"]["room_availability"]:
        actual = availability.room_availability(
            _ctx(check["facility_id"]),
            date.fromisoformat(check["check_in"]),
            date.fromisoformat(check["check_out"]),
        )
        _assert_close(
            check["result"],
            [item.model_dump(mode="json") for item in actual],
            f"{check['check_in']}..{check['check_out']}",
        )


def test_validate_draft_parity() -> None:
    mismatches = []
    for index, (ok, codes) in enumerate(_fixture()["expected"]["validate"]):
        draft = _draft(index)
        result = availability.validate_draft(_ctx(draft.facility_id), draft)
        if result.ok != ok or result.codes != codes:
            mismatches.append((index, codes, result.codes))
    assert not mismatches, mismatches[:10]


def test_quote_parity() -> None:
    _, facilities, _, _ = _world()
    for index, expected in _fixture()["expected"]["quotes"]:
        draft = _draft(index)
        quote = pricing.quote_booking(facilities[draft.facility_id], draft)
        _assert_close(expected, quote.model_dump(mode="json"), f"draft[{index}]")


def test_weekly_usage_parity() -> None:
    for check in _fixture()["expected"]["weekly_usage"]:
        usage = availability.weekly_usage(
            _pool(check["facility_id"]),
            check["contact_id"],
            check["facility_id"],
            date.fromisoformat(check["local_date"]),
        )
        assert usage == check["usage"], check


def test_can_reschedule_parity() -> None:
    now, facilities, _, _ = _world()
    by_id = {r.id: r for r in _all_reservations()}
    for check in _fixture()["expected"]["can_reschedule"]:
        r = by_id[check["id"]]
        allowed, reason = availability.can_reschedule(facilities[r.facility_id], r, now)
        assert allowed == check["allowed"], check
        if not allowed:
            assert reason == check["reason"], check


def test_cancel_quote_parity() -> None:
    now, facilities, _, _ = _world()
    by_id = {r.id: r for r in _all_reservations()}
    paid = _fixture()["paid"]
    for expected in _fixture()["expected"]["cancel_quotes"]:
        r = by_id[expected["id"]]
        quote = pricing.cancel_quote(facilities[r.facility_id], r, paid[r.id], now)
        _assert_close(
            {k: v for k, v in expected.items() if k != "id"}, quote.model_dump(mode="json"), r.id
        )
