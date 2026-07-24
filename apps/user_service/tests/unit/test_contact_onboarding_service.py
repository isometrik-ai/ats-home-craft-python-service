"""Unit tests for ContactOnboardingService and related services."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.db.repositories.contact_onboarding_repository import (
    CONTACT_LEVEL_STEP_KEYS,
)
from apps.user_service.app.schemas.common import Phone
from apps.user_service.app.schemas.contact_onboarding import (
    CompleteProfileRequest,
    CreateHouseholdMemberRequest,
    UpdateHouseholdMemberRequest,
)
from apps.user_service.app.schemas.enums import (
    ContactOnboardingStep,
    ContactType,
    ContactUnitRelationship,
    HouseholdInvitationStatus,
    HouseholdMemberStatus,
    SetupStepStatus,
)
from apps.user_service.app.services.contact_onboarding_service import (
    ContactOnboardingService,
)
from apps.user_service.app.services.contact_units_service import ContactUnitsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)


def _user_context() -> UserContext:
    """Build a minimal UserContext for service tests."""
    return UserContext(
        user_id="user-1",
        email="owner@example.com",
        organization_id="org-1",
    )


def _contact_steps(
    *,
    overrides: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build contact-level onboarding step rows with optional status overrides."""
    overrides = overrides or {}
    return [
        {
            "step_key": step_key,
            "status": overrides.get(step_key, SetupStepStatus.NOT_STARTED.value),
            "completed_at": None,
        }
        for step_key in CONTACT_LEVEL_STEP_KEYS
    ]


class _FakeOnboardingRepo:
    """In-memory fake ContactOnboardingRepository."""

    def __init__(
        self,
        steps: list[dict[str, Any]] | None = None,
        *,
        profile_steps: list[dict[str, Any]] | None = None,
        completed: bool = False,
    ):
        self.steps = steps or _contact_steps()
        self.profile_steps = profile_steps
        self.completed = completed
        self.ensure_calls = 0
        self.complete_step_calls: list[str] = []
        self.skip_step_calls: list[str] = []

    async def ensure_steps(self, **_kwargs):
        """Record ensure_steps call."""
        self.ensure_calls += 1

    async def ensure_profile_step(self, **_kwargs):
        """Record ensure_profile_step call."""
        self.ensure_calls += 1

    async def list_steps(self, **_kwargs):
        """Return configured step rows."""
        return self.steps

    async def list_profile_step(self, **_kwargs):
        """Return configured profile step rows for family contacts."""
        if self.profile_steps is not None:
            return self.profile_steps
        matches = [
            row
            for row in self.steps
            if row.get("step_key") == ContactOnboardingStep.COMPLETE_PROFILE.value
        ]
        if matches:
            return matches
        return [
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.NOT_STARTED.value,
                "completed_at": None,
            }
        ]

    async def is_wizard_completed(self, **_kwargs):
        """Return configured completion flag."""
        return self.completed

    async def complete_step(self, **kwargs):
        """Record complete_step call."""
        self.complete_step_calls.append(kwargs["step_key"])
        return {"step_key": kwargs["step_key"], "status": SetupStepStatus.COMPLETED.value}

    async def skip_step(self, **kwargs):
        """Record skip_step call."""
        self.skip_step_calls.append(kwargs["step_key"])
        return {"step_key": kwargs["step_key"], "status": SetupStepStatus.SKIPPED.value}


class _FakeUnitOnboardingRepo:
    """In-memory fake ContactUnitOnboardingRepository."""

    def __init__(
        self,
        *,
        unit_step_rows: list[dict[str, Any]] | None = None,
        all_terminal: bool = True,
    ):
        self.unit_step_rows = unit_step_rows or []
        self.all_terminal = all_terminal
        self.skip_step_calls: list[tuple[str, str]] = []
        self.complete_step_calls: list[tuple[str, str]] = []

    async def list_steps_for_contact(self, **_kwargs):
        """Return configured unit step rows."""
        return self.unit_step_rows

    async def all_unit_steps_terminal(self, **_kwargs):
        """Return configured terminal flag."""
        return self.all_terminal

    async def skip_step(self, **kwargs):
        """Record skip_step call."""
        self.skip_step_calls.append((kwargs["contact_unit_id"], kwargs["step_key"]))
        return {"step_key": kwargs["step_key"], "status": SetupStepStatus.SKIPPED.value}

    async def complete_step(self, **kwargs):
        """Record complete_step call."""
        self.complete_step_calls.append((kwargs["contact_unit_id"], kwargs["step_key"]))
        return {"step_key": kwargs["step_key"], "status": SetupStepStatus.COMPLETED.value}


class _FakeContactUnitsRepo:
    """In-memory fake ContactUnitsRepository."""

    def __init__(
        self,
        *,
        active_count: int = 1,
        has_default: bool = True,
        confirm_result: list[dict[str, Any]] | None = None,
        owned_unit: dict[str, Any] | None = None,
        household_rows: list[dict[str, Any]] | None = None,
        household_member: dict[str, Any] | None = None,
        household_link: dict[str, Any] | None = None,
        link_count: int = 0,
    ):
        self.active_count = active_count
        self.has_default = has_default
        self.confirm_result = confirm_result or [{"id": "cu-1", "status": "active"}]
        self.owned_unit = owned_unit or {"id": "cu-1"}
        self.activate_called = False
        self.household_rows = household_rows or []
        self.household_member = household_member
        self.household_link = household_link
        self.link_count = link_count

    async def count_active_units(self, **_kwargs):
        """Return configured active unit count."""
        return self.active_count

    async def has_default_login(self, **_kwargs):
        """Return configured default-login flag."""
        return self.has_default

    async def activate_for_contact(self, **_kwargs):
        """Record activate_for_contact call."""
        self.activate_called = True

    async def get_owned_by_contact(self, **_kwargs):
        """Return configured owned unit row."""
        return self.owned_unit

    async def confirm_selection(self, **_kwargs):
        """Return configured confirm result."""
        return self.confirm_result

    async def find_active_primary_conflicts(self, **_kwargs):
        """Return no primary conflicts by default."""
        return []

    async def list_household_by_primary(self, **_kwargs):
        """Return configured household rows."""
        return getattr(self, "household_rows", [])

    async def get_household_member(self, **_kwargs):
        """Return configured household member row."""
        return getattr(self, "household_member", None)

    async def get_household_link(self, **_kwargs):
        """Return configured household link row."""
        return getattr(self, "household_link", None)

    async def update_household_relationship(self, **_kwargs):
        """Record relationship update."""
        return None

    async def contact_has_active_unit(self, **_kwargs):
        """Return configured active-unit flag."""
        return getattr(self, "has_active_unit", True)

    async def get_unit_project(self, **_kwargs):
        """Return configured unit project row."""
        return getattr(self, "unit_project", {"project_id": "proj-1"})

    async def insert_household_link(self, **_kwargs):
        """Return configured household link insert row."""
        return getattr(
            self,
            "inserted_link",
            {"contact_unit_id": "cu-family-1", "contact_id": "family-1"},
        )

    async def update_household_link_status(self, **_kwargs):
        """Record household link status update."""
        return None

    async def delete_link(self, **_kwargs):
        """Record household link deletion."""
        return None

    async def count_links_for_contact(self, **_kwargs):
        """Return configured link count for orphan detection."""
        return getattr(self, "link_count", 0)


def _service(
    onboarding_repo: _FakeOnboardingRepo | None = None,
    contact_units_repo: _FakeContactUnitsRepo | None = None,
    unit_onboarding_repo: _FakeUnitOnboardingRepo | None = None,
) -> ContactOnboardingService:
    """Build ContactOnboardingService with fake repositories."""
    svc = ContactOnboardingService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.onboarding_repo = onboarding_repo or _FakeOnboardingRepo()
    svc.unit_onboarding_repo = unit_onboarding_repo or _FakeUnitOnboardingRepo()
    svc.contact_units_repo = contact_units_repo or _FakeContactUnitsRepo()
    svc.contacts_repo = MagicMock()
    svc.contacts_repo.get_contact_details = AsyncMock(
        return_value={"contact_type": ContactType.OWNER.value},
    )
    svc.contact_units_service = MagicMock()
    svc.vehicles_service = MagicMock()
    return svc


def _completed_profile_steps() -> list[dict[str, str]]:
    """Onboarding step rows with profile completed (for confirm_properties tests)."""
    return [
        {
            "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
            "status": SetupStepStatus.COMPLETED.value,
        }
    ]


def _terminal_contact_steps() -> list[dict[str, Any]]:
    """All contact-level steps completed."""
    return _contact_steps(
        overrides={
            step_key: SetupStepStatus.COMPLETED.value for step_key in CONTACT_LEVEL_STEP_KEYS
        }
    )


@pytest.mark.asyncio
async def test_get_status_family_contact_profile_only():
    """Family contacts only see the complete_profile onboarding step."""
    repo = _FakeOnboardingRepo(
        profile_steps=[
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.NOT_STARTED.value,
                "completed_at": None,
            }
        ]
    )
    svc = _service(onboarding_repo=repo)

    result = await svc.get_status(
        contact_id="contact-1",
        contact_type=ContactType.FAMILY.value,
    )

    assert result["setup_current_step"] == ContactOnboardingStep.COMPLETE_PROFILE.value
    assert result["steps"] == [
        {
            "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
            "status": SetupStepStatus.NOT_STARTED.value,
            "completed_at": None,
        }
    ]
    assert result["unit_onboarding"] == []
    assert result["current_contact_unit_id"] is None
    assert result["is_completed"] is False


@pytest.mark.asyncio
async def test_get_status_family_contact_completed():
    """Family onboarding is complete once profile is done."""
    repo = _FakeOnboardingRepo(
        profile_steps=[
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.COMPLETED.value,
                "completed_at": None,
            }
        ]
    )
    svc = _service(onboarding_repo=repo)

    result = await svc.get_status(
        contact_id="contact-1",
        contact_type=ContactType.FAMILY.value,
    )

    assert result["setup_current_step"] is None
    assert result["is_completed"] is True


@pytest.mark.asyncio
async def test_get_status_returns_profile_first():
    """Current step is complete_profile when profile is not terminal."""
    repo = _FakeOnboardingRepo()
    svc = _service(onboarding_repo=repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["setup_current_step"] == ContactOnboardingStep.COMPLETE_PROFILE.value
    assert result["current_contact_unit_id"] is None
    assert result["is_completed"] is False
    assert repo.ensure_calls == 1


@pytest.mark.asyncio
async def test_get_status_returns_unit_step_after_properties():
    """After profile and properties, navigation points at unit vehicles step."""
    repo = _FakeOnboardingRepo(
        _contact_steps(
            overrides={
                ContactOnboardingStep.COMPLETE_PROFILE.value: SetupStepStatus.COMPLETED.value,
                ContactOnboardingStep.SELECT_PROPERTIES.value: SetupStepStatus.COMPLETED.value,
            }
        )
    )
    unit_repo = _FakeUnitOnboardingRepo(
        unit_step_rows=[
            {
                "contact_unit_id": "cu-1",
                "unit_id": "unit-1",
                "unit_code": "A-101",
                "step_key": ContactOnboardingStep.VEHICLES.value,
                "status": SetupStepStatus.NOT_STARTED.value,
                "completed_at": None,
            },
            {
                "contact_unit_id": "cu-1",
                "unit_id": "unit-1",
                "unit_code": "A-101",
                "step_key": ContactOnboardingStep.HOUSEHOLD.value,
                "status": SetupStepStatus.NOT_STARTED.value,
                "completed_at": None,
            },
        ],
        all_terminal=False,
    )
    svc = _service(onboarding_repo=repo, unit_onboarding_repo=unit_repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["setup_current_step"] == ContactOnboardingStep.VEHICLES.value
    assert result["current_contact_unit_id"] == "cu-1"
    assert len(result["unit_onboarding"]) == 1


@pytest.mark.asyncio
async def test_get_status_completed_wizard():
    """Completed wizard returns no current step."""
    repo = _FakeOnboardingRepo(completed=True)
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=True)
    units_repo = _FakeContactUnitsRepo(active_count=1)
    svc = _service(
        onboarding_repo=repo,
        unit_onboarding_repo=unit_repo,
        contact_units_repo=units_repo,
    )

    result = await svc.get_status(contact_id="contact-1")

    assert result["setup_current_step"] is None
    assert result["is_completed"] is True


@pytest.mark.asyncio
async def test_skip_step_rejects_non_skippable():
    """Non-skippable steps raise ValidationException."""
    svc = _service()

    with pytest.raises(ValidationException):
        await svc.skip_step(
            contact_id="contact-1",
            step_key=ContactOnboardingStep.SELECT_PROPERTIES.value,
        )


@pytest.mark.asyncio
async def test_skip_step_requires_contact_unit_id():
    """Unit steps require contact_unit_id."""
    svc = _service()

    with pytest.raises(ValidationException):
        await svc.skip_step(
            contact_id="contact-1",
            step_key=ContactOnboardingStep.VEHICLES.value,
        )


@pytest.mark.asyncio
async def test_skip_step_allows_vehicles_for_unit():
    """Vehicles step may be skipped per unit."""
    unit_repo = _FakeUnitOnboardingRepo()
    units_repo = _FakeContactUnitsRepo()
    svc = _service(unit_onboarding_repo=unit_repo, contact_units_repo=units_repo)

    await svc.skip_step(
        contact_id="contact-1",
        step_key=ContactOnboardingStep.VEHICLES.value,
        contact_unit_id="cu-1",
    )

    assert unit_repo.skip_step_calls == [("cu-1", ContactOnboardingStep.VEHICLES.value)]


@pytest.mark.asyncio
async def test_complete_onboarding_rejects_already_completed():
    """Already completed onboarding raises ConflictException."""
    repo = _FakeOnboardingRepo(completed=True)
    svc = _service(onboarding_repo=repo)

    with pytest.raises(ConflictException):
        await svc.complete_onboarding(contact_id="contact-1")


@pytest.mark.asyncio
async def test_complete_onboarding_requires_active_units():
    """Completion requires at least one active unit."""
    repo = _FakeOnboardingRepo(_terminal_contact_steps())
    units_repo = _FakeContactUnitsRepo(active_count=0)
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=True)
    svc = _service(
        onboarding_repo=repo,
        contact_units_repo=units_repo,
        unit_onboarding_repo=unit_repo,
    )

    with pytest.raises(ValidationException):
        await svc.complete_onboarding(contact_id="contact-1")


@pytest.mark.asyncio
async def test_complete_onboarding_requires_default_unit():
    """Multi-unit contacts must set a default unit before completion."""
    repo = _FakeOnboardingRepo(_terminal_contact_steps())
    units_repo = _FakeContactUnitsRepo(active_count=2, has_default=False)
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=True)
    svc = _service(
        onboarding_repo=repo,
        contact_units_repo=units_repo,
        unit_onboarding_repo=unit_repo,
    )

    with pytest.raises(ValidationException):
        await svc.complete_onboarding(contact_id="contact-1")


@pytest.mark.asyncio
async def test_complete_onboarding_requires_unit_steps():
    """Completion requires all unit steps terminal."""
    repo = _FakeOnboardingRepo(_terminal_contact_steps())
    units_repo = _FakeContactUnitsRepo(active_count=1)
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=False)
    svc = _service(
        onboarding_repo=repo,
        contact_units_repo=units_repo,
        unit_onboarding_repo=unit_repo,
    )

    with pytest.raises(ValidationException):
        await svc.complete_onboarding(contact_id="contact-1")


@pytest.mark.asyncio
async def test_complete_onboarding_happy_path():
    """Successful completion activates units and completes review step."""
    repo = _FakeOnboardingRepo(_terminal_contact_steps())
    units_repo = _FakeContactUnitsRepo(active_count=1)
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=True)
    svc = _service(
        onboarding_repo=repo,
        contact_units_repo=units_repo,
        unit_onboarding_repo=unit_repo,
    )

    result = await svc.complete_onboarding(contact_id="contact-1")

    assert units_repo.activate_called is True
    assert ContactOnboardingStep.REVIEW.value in repo.complete_step_calls
    assert result["is_completed"] is False


@pytest.mark.asyncio
async def test_confirm_properties_validates_selection_count():
    """Confirm rejects when not all selected units are found."""
    svc = ContactUnitsService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.repo = MagicMock()
    svc.repo.find_active_primary_conflicts = AsyncMock(return_value=[])
    svc.repo.confirm_selection = AsyncMock(return_value=[{"id": "cu-1", "status": "active"}])
    svc.onboarding_repo = MagicMock()
    svc.onboarding_repo.list_steps = AsyncMock(return_value=_completed_profile_steps())
    svc.unit_onboarding_repo = MagicMock()

    with pytest.raises(ValidationException):
        await svc.confirm_properties(
            contact_id="contact-1",
            contact_unit_ids=["cu-1", "cu-2"],
        )


@pytest.mark.asyncio
async def test_confirm_properties_requires_profile_step():
    """Confirm rejects when profile step is not complete."""
    svc = ContactUnitsService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.repo = MagicMock()
    svc.onboarding_repo = MagicMock()
    svc.onboarding_repo.list_steps = AsyncMock(
        return_value=[
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.NOT_STARTED.value,
            }
        ]
    )
    svc.unit_onboarding_repo = MagicMock()

    with pytest.raises(ValidationException):
        await svc.confirm_properties(
            contact_id="contact-1",
            contact_unit_ids=["cu-1"],
        )


def _household_row(**overrides: Any) -> dict[str, Any]:
    """Build a minimal household member DB row for formatter tests."""
    row = {
        "contact_id": "family-1",
        "contact_unit_id": "cu-1",
        "unit_id": "unit-1",
        "first_name": "Minaxi",
        "last_name": "Chaudhari",
        "relationship": "parent",
        "portal_access": False,
        "unit_link_status": "active",
        "phones": [],
        "emails": [],
        "user_id": None,
        "invitation_status": None,
        "invitation_token": None,
        "invitation_expires_at": None,
        "invitation_sent_at": None,
    }
    row.update(overrides)
    return row


def test_format_household_member_revoked_invitation():
    """Revoked invites expose cancelled status and can_resend_invitation."""
    item = ContactOnboardingService._format_household_member(
        _household_row(invitation_status=HouseholdInvitationStatus.CANCELLED.value)
    )

    assert item["member_status"] == HouseholdMemberStatus.REVOKED.value
    assert item["portal_access"] is False
    assert item["invitation_status"] == HouseholdInvitationStatus.CANCELLED.value
    assert item["can_resend_invitation"] is True


def test_format_household_member_never_invited():
    """Members added without portal access have no invitation_status."""
    item = ContactOnboardingService._format_household_member(_household_row())

    assert item["invitation_status"] is None
    assert item["can_resend_invitation"] is False


def test_format_household_member_pending_invitation():
    """Pending invites remain invited and allow resend."""
    item = ContactOnboardingService._format_household_member(
        _household_row(
            portal_access=True,
            unit_link_status="pending",
            invitation_status=HouseholdInvitationStatus.PENDING.value,
            invitation_token="secret-token",
        )
    )

    assert item["member_status"] == HouseholdMemberStatus.INVITED.value
    assert item["can_resend_invitation"] is True
    assert item["invite_url"]


def test_can_resend_household_invitation_has_user():
    """Resend is disabled when member already has a portal user."""
    assert (
        ContactOnboardingService._can_resend_household_invitation(
            portal_access=True,
            invitation_status=HouseholdInvitationStatus.PENDING.value,
            has_user=True,
        )
        is False
    )


def test_can_resend_household_invitation_expired():
    """Expired revoked invites can be resent."""
    assert (
        ContactOnboardingService._can_resend_household_invitation(
            portal_access=False,
            invitation_status=HouseholdInvitationStatus.EXPIRED.value,
            has_user=False,
        )
        is True
    )


def test_primary_phone_from_contact():
    """_primary_phone_from_contact prefers primary flag."""
    phone = ContactOnboardingService._primary_phone_from_contact(
        {
            "phones": [
                {"phone_number": "111", "phone_isd_code": "+1", "is_primary": False},
                {"phone_number": "222", "phone_isd_code": "+1", "is_primary": True},
            ]
        }
    )
    assert phone["phone_number"] == "222"


def test_normalize_step_and_step_status():
    """Step helpers normalize rows and read status by key."""
    svc = _service()
    row = {
        "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
        "status": SetupStepStatus.COMPLETED.value,
        "completed_at": None,
    }
    normalized = svc._normalize_step(row)
    assert normalized["step_key"] == ContactOnboardingStep.COMPLETE_PROFILE.value
    status = ContactOnboardingService._step_status([normalized], row["step_key"])
    assert status == SetupStepStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_list_household_formats_members():
    """list_household maps repository rows through formatter."""
    units_repo = _FakeContactUnitsRepo(
        household_rows=[_household_row(first_name="Alex", last_name="Smith")],
    )
    svc = _service(contact_units_repo=units_repo)

    result = await svc.list_household(contact_id="contact-1")

    assert len(result) == 1
    assert result[0]["first_name"] == "Alex"


@pytest.mark.asyncio
async def test_update_household_member_updates_name():
    """update_household_member patches contact fields and returns formatted row."""
    units_repo = _FakeContactUnitsRepo(
        household_link={"contact_id": "family-1"},
        household_member=_household_row(first_name="Old", last_name="Name"),
    )
    svc = _service(contact_units_repo=units_repo)
    svc.contacts_repo.update_contact = AsyncMock(return_value={"id": "family-1"})

    result = await svc.update_household_member(
        primary_contact_id="contact-1",
        contact_unit_id="cu-1",
        body=UpdateHouseholdMemberRequest(first_name="New"),
    )

    assert result["first_name"] == "Old"
    svc.contacts_repo.update_contact.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_household_member_not_found():
    """update_household_member raises when link is missing."""
    svc = _service(contact_units_repo=_FakeContactUnitsRepo(household_link=None))
    with pytest.raises(NotFoundException):
        await svc.update_household_member(
            primary_contact_id="contact-1",
            contact_unit_id="cu-1",
            body=UpdateHouseholdMemberRequest(first_name="New"),
        )


@pytest.mark.asyncio
async def test_complete_profile_updates_contact_and_step(monkeypatch):
    """complete_profile updates contact details and completes profile step."""
    repo = _FakeOnboardingRepo()
    svc = _service(onboarding_repo=repo)
    contacts_service = MagicMock()
    contacts_service.update_contact = AsyncMock()
    contacts_service.get_contact_details = AsyncMock(return_value={"id": "contact-1"})
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.ContactsService",
        lambda **kwargs: contacts_service,
    )
    body = CompleteProfileRequest(first_name="Jane", last_name="Doe")

    result = await svc.complete_profile(contact_id="contact-1", body=body)

    assert result["id"] == "contact-1"
    assert ContactOnboardingStep.COMPLETE_PROFILE.value in repo.complete_step_calls


@pytest.mark.asyncio
async def test_get_review_aggregates_sections(monkeypatch):
    """get_review combines contact, units, vehicles, household, and status."""
    svc = _service()
    contacts_service = MagicMock()
    contacts_service.get_contact_details = AsyncMock(return_value={"id": "contact-1"})
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.ContactsService",
        lambda **kwargs: contacts_service,
    )
    svc.contact_units_service.list_my_properties = AsyncMock(return_value=[{"id": "cu-1"}])
    svc.vehicles_service.list_vehicles = AsyncMock(return_value=[])
    svc.list_household = AsyncMock(return_value=[])
    svc.get_status = AsyncMock(
        return_value={
            "steps": [],
            "unit_onboarding": [],
            "setup_current_step": None,
            "current_contact_unit_id": None,
            "is_completed": False,
        }
    )

    result = await svc.get_review(contact_id="contact-1")

    assert result["contact"]["id"] == "contact-1"
    assert result["units"] == [{"id": "cu-1"}]
    assert result["vehicles"] == []


@pytest.mark.asyncio
async def test_get_profile_delegates(monkeypatch):
    """get_profile returns contact details from ContactsService."""
    svc = _service()
    contacts_service = MagicMock()
    contacts_service.get_contact_details = AsyncMock(return_value={"id": "contact-1"})
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.ContactsService",
        lambda **kwargs: contacts_service,
    )

    result = await svc.get_profile(contact_id="contact-1")

    assert result["id"] == "contact-1"


@pytest.mark.asyncio
async def test_complete_household_step(monkeypatch):
    """complete_household_step marks household step complete for owned unit."""
    unit_repo = _FakeUnitOnboardingRepo()
    units_repo = _FakeContactUnitsRepo()
    svc = _service(unit_onboarding_repo=unit_repo, contact_units_repo=units_repo)

    await svc.complete_household_step(
        contact_id="contact-1",
        contact_unit_id="cu-1",
    )

    assert unit_repo.complete_step_calls == [
        ("cu-1", ContactOnboardingStep.HOUSEHOLD.value),
    ]


def _household_create_body(*, portal_access: bool = False) -> CreateHouseholdMemberRequest:
    """Build a minimal household member create request."""
    return CreateHouseholdMemberRequest(
        unit_id="unit-1",
        first_name="Alex",
        last_name="Smith",
        phones=[Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True)],
        relationship=ContactUnitRelationship.SPOUSE,
        portal_access=portal_access,
    )


@pytest.mark.asyncio
async def test_add_household_member_without_portal(monkeypatch):
    """add_household_member creates family contact and active link."""
    units_repo = _FakeContactUnitsRepo()
    units_repo.get_unit_project = AsyncMock(return_value={"project_id": "proj-1"})  # type: ignore[method-assign]
    units_repo.insert_household_link = AsyncMock(  # type: ignore[method-assign]
        return_value={"id": "cu-family-1", "contact_id": "family-1"},
    )
    svc = _service(contact_units_repo=units_repo)
    contacts_service = MagicMock()
    contacts_service.get_contact_details = AsyncMock(return_value={"first_name": "Owner"})
    contacts_service.create_contact = AsyncMock(
        return_value={"contact_id": "family-1", "new_data": {"id": "family-1"}},
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.ContactsService",
        lambda **kwargs: contacts_service,
    )

    result = await svc.add_household_member(
        primary_contact_id="contact-1",
        body=_household_create_body(),
    )

    assert result["contact_id"] == "family-1"
    assert result["member_status"] == HouseholdMemberStatus.JOINED.value
    contacts_service.create_contact.assert_awaited_once()


@pytest.mark.asyncio
async def test_add_household_member_with_portal(monkeypatch):
    """add_household_member sends invitation when portal access requested."""
    units_repo = _FakeContactUnitsRepo()
    units_repo.get_unit_project = AsyncMock(return_value={"project_id": "proj-1"})  # type: ignore[method-assign]
    units_repo.insert_household_link = AsyncMock(  # type: ignore[method-assign]
        return_value={"id": "cu-family-1", "contact_id": "family-1"},
    )
    svc = _service(contact_units_repo=units_repo)
    svc.household_invitation_service.create_and_send = AsyncMock(
        return_value={
            "member_status": HouseholdMemberStatus.INVITED.value,
            "invitation_id": "inv-1",
            "phone_masked": "***3210",
            "invite_url": "https://invite.example",
        }
    )
    contacts_service = MagicMock()
    contacts_service.get_contact_details = AsyncMock(return_value={"first_name": "Owner"})
    contacts_service.create_contact = AsyncMock(
        return_value={"contact_id": "family-1", "new_data": {"id": "family-1"}},
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.ContactsService",
        lambda **kwargs: contacts_service,
    )

    result = await svc.add_household_member(
        primary_contact_id="contact-1",
        body=_household_create_body(portal_access=True),
    )

    assert result["invitation_id"] == "inv-1"
    assert result["member_status"] == HouseholdMemberStatus.INVITED.value


@pytest.mark.asyncio
async def test_add_household_member_unit_not_assigned():
    """add_household_member rejects when primary does not own the unit."""
    units_repo = _FakeContactUnitsRepo()
    units_repo.contact_has_active_unit = AsyncMock(return_value=False)  # type: ignore[method-assign]
    svc = _service(contact_units_repo=units_repo)

    with pytest.raises(ValidationException):
        await svc.add_household_member(
            primary_contact_id="contact-1",
            body=_household_create_body(),
        )


@pytest.mark.asyncio
async def test_resend_household_invitation_pending(monkeypatch):
    """resend_household_invitation delegates to invitation service when pending."""
    units_repo = _FakeContactUnitsRepo(
        household_link={"contact_id": "family-1", "portal_access": True},
    )
    svc = _service(contact_units_repo=units_repo)
    svc.household_invitation_service.invitations_repo = MagicMock()
    svc.household_invitation_service.invitations_repo.get_pending_by_contact_unit = AsyncMock(
        return_value={"id": "inv-1"},
    )
    svc.household_invitation_service.resend = AsyncMock(return_value={"invitation_id": "inv-1"})
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.ContactsService",
        lambda **kwargs: MagicMock(
            get_contact_details=AsyncMock(return_value={"first_name": "Owner"}),
        ),
    )

    result = await svc.resend_household_invitation(
        primary_contact_id="contact-1",
        contact_unit_id="cu-1",
    )

    assert result["invitation_id"] == "inv-1"


@pytest.mark.asyncio
async def test_revoke_household_invitation(monkeypatch):
    """revoke_household_invitation cancels pending portal access."""
    units_repo = _FakeContactUnitsRepo(
        household_link={"contact_id": "family-1"},
        household_member=_household_row(
            portal_access=True,
            unit_link_status="pending",
        ),
    )
    svc = _service(contact_units_repo=units_repo)
    svc.contacts_repo.update_contact = AsyncMock(return_value={"id": "family-1"})
    svc.household_invitation_service.cancel_for_contact_unit = AsyncMock()
    svc.household_invitation_service.invitations_repo = MagicMock()
    svc.household_invitation_service.invitations_repo.get_pending_by_contact_unit = AsyncMock(
        return_value=None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.ContactsService",
        lambda **kwargs: MagicMock(
            get_contact_details=AsyncMock(return_value={"first_name": "Owner"}),
        ),
    )

    result = await svc.revoke_household_invitation(
        primary_contact_id="contact-1",
        contact_unit_id="cu-1",
    )

    assert result["invitation_status"] == HouseholdInvitationStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_remove_household_member():
    """remove_household_member soft-deletes contact when orphaned."""
    units_repo = _FakeContactUnitsRepo(
        household_link={"contact_id": "family-1"},
        link_count=0,
    )
    svc = _service(contact_units_repo=units_repo)
    svc.household_invitation_service.cancel_for_contact_unit = AsyncMock()
    svc.contacts_repo.soft_delete_contact = AsyncMock(return_value={"id": "family-1"})

    result = await svc.remove_household_member(
        primary_contact_id="contact-1",
        contact_unit_id="cu-1",
    )

    assert result["contact_deleted"] is True
    svc.contacts_repo.soft_delete_contact.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_household_member_enable_portal(monkeypatch):
    """update_household_member enabling portal access sends invitation."""
    units_repo = _FakeContactUnitsRepo(
        household_link={"contact_id": "family-1"},
        household_member=_household_row(portal_access=False, unit_link_status="active"),
    )
    svc = _service(contact_units_repo=units_repo)
    svc.contacts_repo.get_contact_details = AsyncMock(
        return_value={
            "phones": [{"phone_number": "9876543210", "phone_isd_code": "+91", "is_primary": True}],
            "first_name": "Alex",
            "last_name": "Smith",
        },
    )
    svc.contacts_repo.update_contact = AsyncMock(return_value={"id": "family-1"})
    svc.household_invitation_service.create_and_send = AsyncMock(
        return_value={"invitation_id": "inv-2"}
    )
    svc.household_invitation_service.invitations_repo = MagicMock()
    svc.household_invitation_service.invitations_repo.get_pending_by_contact_unit = AsyncMock(
        return_value=None,
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.ContactsService",
        lambda **kwargs: MagicMock(
            get_contact_details=AsyncMock(return_value={"first_name": "Owner"}),
        ),
    )

    await svc.update_household_member(
        primary_contact_id="contact-1",
        contact_unit_id="cu-1",
        body=UpdateHouseholdMemberRequest(portal_access=True),
    )

    svc.household_invitation_service.create_and_send.assert_awaited_once()
