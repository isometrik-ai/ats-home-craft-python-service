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
    CompleteOnboardingRequest,
    CompleteProfileRequest,
    ConfirmPhoneChangeRequest,
    CreateHouseholdMemberRequest,
    SendPhoneChangeRequest,
    UpdateHouseholdMemberRequest,
)
from apps.user_service.app.schemas.enums import (
    ContactOnboardingStep,
    ContactType,
    ContactUnitRelationship,
    ContactUnitStatus,
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
    BadRequestException,
    ConflictException,
    NotFoundException,
    ValidationException,
)

CONTACT_UNIT_ID = "550e8400-e29b-41d4-a716-446655440002"
CONTACT_UNIT_ID_2 = "660e8400-e29b-41d4-a716-446655440003"


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
        step_key = kwargs["step_key"]
        self.complete_step_calls.append(step_key)
        for row in self.steps:
            if row.get("step_key") == step_key:
                row["status"] = SetupStepStatus.COMPLETED.value
        if self.profile_steps is not None:
            for row in self.profile_steps:
                if row.get("step_key") == step_key:
                    row["status"] = SetupStepStatus.COMPLETED.value
        return {"step_key": step_key, "status": SetupStepStatus.COMPLETED.value}

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

    async def all_unit_steps_terminal_for_units(self, **_kwargs):
        """Return configured terminal flag for selected units."""
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
        active_unit_ids: list[str] | None = None,
        has_default: bool = True,
        default_contact_unit_id: str | None = None,
        confirm_result: list[dict[str, Any]] | None = None,
        owned_unit: dict[str, Any] | None = None,
        household_rows: list[dict[str, Any]] | None = None,
        household_member: dict[str, Any] | None = None,
        household_link: dict[str, Any] | None = None,
        link_count: int = 0,
        contact_unit_links: list[dict[str, Any]] | None = None,
    ):
        self.active_count = active_count
        self.active_unit_ids = active_unit_ids or [
            f"cu-{index + 1}" for index in range(active_count)
        ]
        self.has_default = has_default
        self.default_contact_unit_id = default_contact_unit_id or (
            self.active_unit_ids[0] if self.has_default and self.active_unit_ids else None
        )
        self.confirm_result = confirm_result or [{"id": "cu-1", "status": "active"}]
        self.owned_unit = owned_unit or {"id": "cu-1"}
        self.activate_called = False
        self.activated_unit_ids: list[str] = []
        self.deferred_unit_ids: list[str] = []
        self.household_rows = household_rows or []
        self.household_member = household_member
        self.household_link = household_link
        self.link_count = link_count
        self.contact_unit_links = contact_unit_links or []

    async def count_active_units(self, **_kwargs):
        """Return configured active unit count."""
        return self.active_count

    async def list_active_contact_unit_ids(self, **_kwargs):
        """Return configured active contact_unit ids."""
        return list(self.active_unit_ids)

    async def get_default_login_contact_unit_id(self, **_kwargs):
        """Return configured default login contact_unit id."""
        return self.default_contact_unit_id if self.has_default else None

    async def has_default_login(self, **_kwargs):
        """Return configured default-login flag."""
        return self.has_default

    async def activate_for_contact(self, **_kwargs):
        """Record activate_for_contact call."""
        self.activate_called = True

    async def activate_units_by_ids(self, **kwargs):
        """Record activate_units_by_ids call."""
        self.activate_called = True
        self.activated_unit_ids = list(kwargs.get("contact_unit_ids") or [])

    async def defer_active_units_to_pending(self, **kwargs):
        """Record deferred units."""
        self.deferred_unit_ids = list(kwargs.get("contact_unit_ids") or [])
        return self.deferred_unit_ids

    async def set_default_login(self, **kwargs):
        """Record default login assignment."""
        self.default_contact_unit_id = kwargs.get("contact_unit_id")
        self.has_default = True
        return {"id": self.default_contact_unit_id, "is_default_login": True}

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

    async def list_household_for_contact(self, **_kwargs):
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

    async def owner_has_active_unit(self, **_kwargs):
        """Return configured primary-occupant flag."""
        return getattr(self, "is_primary_occupant", True)

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

    async def list_by_contact(self, **kwargs):
        """Return configured unit links for onboarding mode detection."""
        statuses = kwargs.get("statuses")
        links = list(getattr(self, "contact_unit_links", []))
        if not statuses:
            return links
        return [link for link in links if str(link.get("status") or "") in statuses]


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
        return_value={"roles": [{"role_type": ContactType.OWNER.value}]},
    )
    svc.contact_roles_repo = AsyncMock()
    svc.contact_roles_repo.insert_family_role = AsyncMock(return_value={"id": "role-1"})
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
async def test_get_status_household_contact_profile_only():
    """Household-only contacts only see the complete_profile onboarding step."""
    repo = _FakeOnboardingRepo(
        profile_steps=[
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.NOT_STARTED.value,
                "completed_at": None,
            }
        ]
    )
    units_repo = _FakeContactUnitsRepo(
        contact_unit_links=[
            {
                "relationship": ContactUnitRelationship.SPOUSE.value,
                "status": ContactUnitStatus.ACTIVE.value,
            }
        ],
    )
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["prompts"] == [{"type": "complete_profile"}]
    assert result["is_completed"] is False


@pytest.mark.asyncio
async def test_get_status_household_contact_completed():
    """Household onboarding is complete once profile is done."""
    repo = _FakeOnboardingRepo(
        profile_steps=[
            {
                "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
                "status": SetupStepStatus.COMPLETED.value,
                "completed_at": None,
            }
        ]
    )
    units_repo = _FakeContactUnitsRepo(
        contact_unit_links=[
            {
                "relationship": ContactUnitRelationship.CHILD.value,
                "status": ContactUnitStatus.ACTIVE.value,
            }
        ],
    )
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["is_completed"] is True
    assert result["prompts"] == []


@pytest.mark.asyncio
async def test_get_status_returns_profile_first():
    """Current step is complete_profile when profile is not terminal."""
    repo = _FakeOnboardingRepo()
    svc = _service(onboarding_repo=repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["prompts"] == [{"type": "complete_profile"}]
    assert result["is_completed"] is False
    assert repo.ensure_calls == 1


@pytest.mark.asyncio
async def test_get_status_shows_accept_unit_prompt():
    """Pending self units surface an accept_unit prompt instead of wizard unit steps."""
    repo = _FakeOnboardingRepo(
        _contact_steps(
            overrides={
                ContactOnboardingStep.COMPLETE_PROFILE.value: SetupStepStatus.COMPLETED.value,
            }
        )
    )
    units_repo = _FakeContactUnitsRepo(
        active_count=0,
        has_default=True,
        contact_unit_links=[
            {
                "id": "cu-2",
                "unit_id": "unit-2",
                "relationship": ContactUnitRelationship.SELF.value,
                "status": ContactUnitStatus.PENDING.value,
            }
        ],
    )
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["profile_complete"] is True
    assert result["pending_unit_count"] == 1
    assert result["prompts"][0]["type"] == "accept_unit"
    assert result["is_completed"] is False


@pytest.mark.asyncio
async def test_get_status_not_completed_until_finalize():
    """Profile + accepted unit stays incomplete until POST /complete (review step)."""
    repo = _FakeOnboardingRepo(
        _contact_steps(
            overrides={
                ContactOnboardingStep.COMPLETE_PROFILE.value: SetupStepStatus.COMPLETED.value,
                ContactOnboardingStep.SELECT_PROPERTIES.value: SetupStepStatus.COMPLETED.value,
                ContactOnboardingStep.CHOOSE_UNIT.value: SetupStepStatus.COMPLETED.value,
            }
        )
    )
    units_repo = _FakeContactUnitsRepo(active_count=1, has_default=True, contact_unit_links=[])
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["profile_complete"] is True
    assert result["active_unit_count"] == 1
    assert result["is_completed"] is False
    assert result["prompts"] == [{"type": "complete_onboarding"}]


@pytest.mark.asyncio
async def test_get_status_completed_when_no_prompts():
    """Owners with an active unit are complete only after the review/finalize step."""
    repo = _FakeOnboardingRepo(
        _contact_steps(
            overrides={
                ContactOnboardingStep.COMPLETE_PROFILE.value: SetupStepStatus.COMPLETED.value,
                ContactOnboardingStep.REVIEW.value: SetupStepStatus.COMPLETED.value,
            }
        )
    )
    units_repo = _FakeContactUnitsRepo(active_count=1, has_default=True, contact_unit_links=[])
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

    result = await svc.get_status(contact_id="contact-1")

    assert result["is_completed"] is True
    assert result["prompts"] == []


@pytest.mark.asyncio
async def test_get_status_completed_wizard():
    """Alias for completed when no prompts remain."""
    await test_get_status_completed_when_no_prompts()


@pytest.mark.asyncio
async def test_confirm_properties_validates_selection_count():
    """Confirm rejects when not all selected units are found."""
    svc = ContactUnitsService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.repo = MagicMock()
    svc.repo.find_active_primary_conflicts = AsyncMock(return_value=[])
    svc.repo.confirm_selection = AsyncMock(
        return_value=[{"id": CONTACT_UNIT_ID, "status": "active"}]
    )
    svc.onboarding_repo = MagicMock()
    svc.onboarding_repo.list_steps = AsyncMock(return_value=_completed_profile_steps())
    svc.unit_onboarding_repo = MagicMock()

    with pytest.raises(ValidationException):
        await svc.confirm_properties(
            contact_id="contact-1",
            contact_unit_ids=[CONTACT_UNIT_ID, CONTACT_UNIT_ID_2],
        )


@pytest.mark.asyncio
async def test_confirm_properties_works_without_profile_step():
    """Accepting units is not blocked by an incomplete profile step."""
    svc = ContactUnitsService(
        db_connection=MagicMock(),
        user_context=_user_context(),
    )
    svc.repo = MagicMock()
    svc.repo.find_active_primary_conflicts = AsyncMock(return_value=[])
    svc.repo.confirm_selection = AsyncMock(
        return_value=[{"id": CONTACT_UNIT_ID, "status": "active"}]
    )
    svc.repo.set_default_login = AsyncMock()
    svc.repo.activate_units_by_ids = AsyncMock()
    svc.repo.count_active_units = AsyncMock(return_value=1)
    svc.repo.has_default_login = AsyncMock(return_value=True)
    svc.onboarding_repo = MagicMock()
    svc.onboarding_repo.complete_step = AsyncMock()

    result = await svc.confirm_properties(
        contact_id="contact-1",
        contact_unit_ids=[CONTACT_UNIT_ID],
    )

    assert result["items"] == [{"id": CONTACT_UNIT_ID, "status": "active"}]
    svc.repo.activate_units_by_ids.assert_awaited_once()


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


def test_format_household_member_pending_invite_with_provisioned_user():
    """Pending invite stays invited when portal auth user was pre-provisioned."""
    item = ContactOnboardingService._format_household_member(
        _household_row(
            portal_access=True,
            unit_link_status="active",
            user_id="auth-user-1",
            invitation_status=HouseholdInvitationStatus.PENDING.value,
            invitation_token="secret-token",
        )
    )

    assert item["member_status"] == HouseholdMemberStatus.INVITED.value
    assert item["can_resend_invitation"] is True
    assert item["invite_url"]


def test_can_resend_household_invitation_has_user():
    """Resend stays enabled for pending or re-issuable invites even when auth user exists."""
    assert (
        ContactOnboardingService._can_resend_household_invitation(
            invitation_status=HouseholdInvitationStatus.PENDING.value,
        )
        is True
    )
    assert (
        ContactOnboardingService._can_resend_household_invitation(
            invitation_status=HouseholdInvitationStatus.CANCELLED.value,
        )
        is True
    )
    assert (
        ContactOnboardingService._can_resend_household_invitation(
            invitation_status=HouseholdInvitationStatus.ACCEPTED.value,
        )
        is False
    )


def test_can_resend_household_invitation_cancelled_with_portal_access():
    """Cancelled invites can be re-issued regardless of portal_access flag."""
    assert (
        ContactOnboardingService._can_resend_household_invitation(
            invitation_status=HouseholdInvitationStatus.CANCELLED.value,
        )
        is True
    )


def test_can_resend_household_invitation_expired():
    """Expired revoked invites can be resent."""
    assert (
        ContactOnboardingService._can_resend_household_invitation(
            invitation_status=HouseholdInvitationStatus.EXPIRED.value,
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


def test_step_status_reads_status_by_key():
    """Step helper reads status by key."""
    normalized = {
        "step_key": ContactOnboardingStep.COMPLETE_PROFILE.value,
        "status": SetupStepStatus.COMPLETED.value,
    }
    status = ContactOnboardingService._step_status([normalized], normalized["step_key"])
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
async def test_add_household_member_requires_primary_occupant():
    """Only the primary occupant can add household members."""
    units_repo = _FakeContactUnitsRepo()
    units_repo.is_primary_occupant = False
    svc = _service(contact_units_repo=units_repo)

    with pytest.raises(ValidationException) as exc_info:
        await svc.add_household_member(
            primary_contact_id="contact-1",
            body=_household_create_body(),
        )

    assert exc_info.value.message_key == "contact_onboarding.errors.primary_occupant_required"


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
    update_data = svc.contacts_repo.update_contact.await_args.kwargs["update_data"]
    assert update_data == {"first_name": "New"}


@pytest.mark.asyncio
async def test_update_household_member_patches_optional_name_parts():
    """update_household_member writes prefix and middle name, including a clear."""
    units_repo = _FakeContactUnitsRepo(
        household_link={"contact_id": "family-1"},
        household_member=_household_row(prefix="Mr.", middle_name="K"),
    )
    svc = _service(contact_units_repo=units_repo)
    svc.contacts_repo.update_contact = AsyncMock(return_value={"id": "family-1"})

    result = await svc.update_household_member(
        primary_contact_id="contact-1",
        contact_unit_id="cu-1",
        body=UpdateHouseholdMemberRequest(prefix="  ", middle_name="Kumar"),
    )

    assert result["prefix"] == "Mr."
    assert result["middle_name"] == "K"
    update_data = svc.contacts_repo.update_contact.await_args.kwargs["update_data"]
    assert update_data == {"prefix": None, "middle_name": "Kumar"}


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
    body = CompleteProfileRequest(first_name="Jane", middle_name="Q", last_name="Doe")

    result = await svc.complete_profile(contact_id="contact-1", body=body)

    assert result["id"] == "contact-1"
    update_kwargs = contacts_service.update_contact.await_args.kwargs
    assert update_kwargs["body"].middle_name == "Q"
    assert update_kwargs["sync_login_phone"] is False


def test_complete_profile_rejects_primary_phone():
    """Profile edits cannot mark a phone as the login number."""
    with pytest.raises(ValidationException) as exc_info:
        CompleteProfileRequest(
            first_name="Jane",
            phones=[Phone(phone_number="9893085852", phone_isd_code="+91", is_primary=True)],
        )

    assert exc_info.value.message_key == "contact_onboarding.errors.primary_phone_not_allowed"


@pytest.mark.asyncio
async def test_complete_profile_keeps_existing_primary_phone(monkeypatch):
    """Extra numbers are saved, and the stored primary phone stays in place."""
    repo = _FakeOnboardingRepo()
    svc = _service(onboarding_repo=repo)
    svc.contacts_repo.get_contact_phones_for_update = AsyncMock(
        return_value=[
            {"phone_number": "9000000000", "phone_isd_code": "+91", "is_primary": True},
        ]
    )
    contacts_service = MagicMock()
    contacts_service.update_contact = AsyncMock()
    contacts_service.get_contact_details = AsyncMock(return_value={"id": "contact-1"})
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.ContactsService",
        lambda **kwargs: contacts_service,
    )

    await svc.complete_profile(
        contact_id="contact-1",
        body=CompleteProfileRequest(
            first_name="Jane",
            phones=[
                Phone(phone_number="9000000000", phone_isd_code="+91", is_primary=False),
                Phone(phone_number="9893085852", phone_isd_code="+91", is_primary=False),
            ],
        ),
    )

    saved = contacts_service.update_contact.await_args.kwargs["body"].phones
    assert [(phone.phone_number, phone.is_primary) for phone in saved] == [
        ("9000000000", True),
        ("9893085852", False),
    ]
    assert ContactOnboardingStep.COMPLETE_PROFILE.value in repo.complete_step_calls


@pytest.mark.asyncio
async def test_complete_profile_can_clear_gender(monkeypatch):
    """complete_profile clears gender when the client explicitly sends null."""
    repo = _FakeOnboardingRepo()
    svc = _service(onboarding_repo=repo)
    contacts_service = MagicMock()
    contacts_service.update_contact = AsyncMock()
    contacts_service.get_contact_details = AsyncMock(
        return_value={"id": "contact-1", "gender": None}
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.ContactsService",
        lambda **kwargs: contacts_service,
    )
    body = CompleteProfileRequest.model_validate(
        {"first_name": "Jane", "gender": None},
    )

    await svc.complete_profile(contact_id="contact-1", body=body)

    update_body = contacts_service.update_contact.await_args.kwargs["body"]
    assert "gender" in update_body.model_fields_set
    assert update_body.gender is None


@pytest.mark.asyncio
async def test_get_review_aggregates_sections(monkeypatch):
    """get_review combines contact, units, vehicles, and household."""
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

    result = await svc.get_review(contact_id="contact-1")

    assert result["contact"]["id"] == "contact-1"
    assert result["units"] == [{"id": "cu-1"}]
    assert result["vehicles"] == []
    assert result["household"] == []
    svc.list_household.assert_awaited_once_with(
        contact_id="contact-1",
        include_caller_family_links=True,
    )


@pytest.mark.asyncio
async def test_complete_onboarding_rejects_already_completed():
    """Already completed onboarding raises ConflictException."""
    repo = _FakeOnboardingRepo(
        _contact_steps(
            overrides={
                ContactOnboardingStep.COMPLETE_PROFILE.value: SetupStepStatus.COMPLETED.value,
                ContactOnboardingStep.REVIEW.value: SetupStepStatus.COMPLETED.value,
            }
        )
    )
    units_repo = _FakeContactUnitsRepo(active_count=1, has_default=True, contact_unit_links=[])
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

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
async def test_complete_onboarding_auto_sets_default_unit():
    """Finalize auto-picks a default unit when missing."""
    repo = _FakeOnboardingRepo(_contact_steps())
    units_repo = _FakeContactUnitsRepo(
        active_count=2, has_default=False, default_contact_unit_id=None
    )
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

    result = await svc.complete_onboarding(contact_id="contact-1")

    assert units_repo.default_contact_unit_id == "cu-1"
    assert result["is_completed"] is True
    assert result["completed_contact_unit_ids"] == ["cu-1", "cu-2"]


@pytest.mark.asyncio
async def test_complete_onboarding_succeeds_without_unit_steps():
    """Finalize no longer requires vehicles/household wizard steps."""
    repo = _FakeOnboardingRepo(_contact_steps())
    units_repo = _FakeContactUnitsRepo(
        active_count=2, has_default=False, default_contact_unit_id=None
    )
    svc = _service(onboarding_repo=repo, contact_units_repo=units_repo)

    result = await svc.complete_onboarding(contact_id="contact-1")

    assert units_repo.activate_called is True
    assert result["is_completed"] is True


@pytest.mark.asyncio
async def test_complete_onboarding_partial_finalize():
    """Partial finalize activates selected units and defers the rest to pending."""
    repo = _FakeOnboardingRepo(_contact_steps())
    units_repo = _FakeContactUnitsRepo(
        active_count=3,
        active_unit_ids=["cu-1", "cu-2", "cu-3"],
        has_default=False,
        default_contact_unit_id=None,
    )
    unit_repo = _FakeUnitOnboardingRepo(all_terminal=True)
    svc = _service(
        onboarding_repo=repo,
        contact_units_repo=units_repo,
        unit_onboarding_repo=unit_repo,
    )

    result = await svc.complete_onboarding(
        contact_id="contact-1",
        body=CompleteOnboardingRequest(contact_unit_ids=["cu-1"]),
    )

    assert units_repo.activated_unit_ids == ["cu-1"]
    assert units_repo.deferred_unit_ids == ["cu-2", "cu-3"]
    assert units_repo.default_contact_unit_id == "cu-1"
    assert result["completed_contact_unit_ids"] == ["cu-1"]
    assert result["deferred_contact_unit_ids"] == ["cu-2", "cu-3"]
    assert result["is_completed"] is True


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
    created = contacts_service.create_contact.await_args.args[0]
    assert created.prefix is None
    assert created.middle_name is None
    assert created.first_name == "Alex"


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
async def test_add_household_member_persists_optional_name_parts(monkeypatch):
    """Optional prefix and middle name are stored on the family contact."""
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

    await svc.add_household_member(
        primary_contact_id="contact-1",
        body=_household_create_body().model_copy(update={"prefix": "Mr.", "middle_name": "K"}),
    )

    created = contacts_service.create_contact.await_args.args[0]
    assert created.prefix == "Mr."
    assert created.first_name == "Alex"
    assert created.middle_name == "K"
    assert created.last_name == "Smith"


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


def test_format_household_member_revoked_with_existing_user():
    """Revoked invites stay re-issuable even when the member already has portal auth."""
    item = ContactOnboardingService._format_household_member(
        _household_row(
            portal_access=True,
            user_id="auth-user-1",
            invitation_status=HouseholdInvitationStatus.CANCELLED.value,
        )
    )

    assert item["member_status"] == HouseholdMemberStatus.REVOKED.value
    assert item["can_resend_invitation"] is True


@pytest.mark.asyncio
async def test_resend_household_invitation_after_revoke_with_existing_user(monkeypatch):
    """resend re-issues a cancelled invite even when portal_access and user_id are set."""
    units_repo = _FakeContactUnitsRepo(
        household_link={"contact_id": "family-1"},
        household_member=_household_row(
            portal_access=True,
            user_id="auth-user-1",
            unit_link_status="active",
            invitation_status=HouseholdInvitationStatus.CANCELLED.value,
        ),
    )
    svc = _service(contact_units_repo=units_repo)
    svc.household_invitation_service.invitations_repo = MagicMock()
    svc.household_invitation_service.invitations_repo.get_pending_by_contact_unit = AsyncMock(
        return_value=None,
    )
    svc.household_invitation_service.create_and_send = AsyncMock(
        return_value={"invitation_id": "inv-2", "invite_url": "https://example.com/invite"}
    )
    svc.contacts_repo.get_contact_details = AsyncMock(
        return_value={
            "first_name": "Ashe",
            "last_name": "S",
            "phones": [
                {
                    "phone_number": "9112233711",
                    "phone_isd_code": "+91",
                    "is_primary": True,
                }
            ],
        }
    )
    svc.contacts_repo.update_contact = AsyncMock()
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

    assert result["invitation_id"] == "inv-2"
    svc.household_invitation_service.create_and_send.assert_awaited_once()
    svc.contacts_repo.update_contact.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_get_household_summary_returns_dashboard_counts():
    """Household summary aggregates family, daily help, vehicles, and tenant counts."""
    svc = _service()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=True)
    svc.contact_units_repo.count_family_members_for_unit = AsyncMock(return_value=2)
    svc.daily_help_repo = MagicMock()
    svc.daily_help_repo.count_active_links_for_unit = AsyncMock(return_value=2)
    svc.units_repo = MagicMock()
    svc.units_repo.count_unit_vehicles = AsyncMock(return_value=(2, 1))
    svc.contact_roles_repo.count_active_tenants_for_unit = AsyncMock(return_value=1)
    svc.pets_repo = MagicMock()
    svc.pets_repo.count_active_for_unit = AsyncMock(return_value=2)

    result = await svc.get_household_summary(
        contact_id="contact-1",
        unit_id="unit-1",
    )

    assert result.unit_id == "unit-1"
    assert result.family_count == 2
    assert result.daily_help_count == 2
    assert result.vehicles_count == 2
    assert result.tenant_count == 1
    assert result.pets_count == 2


@pytest.mark.asyncio
async def test_get_household_summary_requires_unit_access():
    """Household summary rejects units the contact does not occupy."""
    svc = _service()
    svc.contact_units_repo.contact_has_active_unit = AsyncMock(return_value=False)

    with pytest.raises(ValidationException):
        await svc.get_household_summary(contact_id="contact-1", unit_id="unit-1")


@pytest.mark.asyncio
async def test_get_status_accepts_legacy_contact_type_argument():
    """get_status accepts optional contact_type for API compatibility."""
    svc = _service()
    svc._ensure_onboarding = AsyncMock()
    svc._is_profile_complete = AsyncMock(return_value=True)
    svc._list_pending_self_units = AsyncMock(return_value=[])
    svc.contact_units_repo.count_active_units = AsyncMock(return_value=1)
    svc.contact_units_repo.has_default_login = AsyncMock(return_value=True)
    svc._is_household_only_contact = AsyncMock(return_value=False)
    svc._is_review_complete = AsyncMock(return_value=True)

    result = await svc.get_status(contact_id="contact-1", contact_type="owner")

    assert result["profile_complete"] is True
    svc._ensure_onboarding.assert_awaited_once()


def _phone_service(
    monkeypatch,
    *,
    owner_id: str | None,
    current_phone: str,
) -> ContactOnboardingService:
    """Service whose auth phone lookup and ownership check are stubbed."""
    svc = _service()
    svc.supabase_client = MagicMock()

    class _Users:
        async def get_auth_user_by_phone(self, _phone: str):
            if owner_id is None:
                return None
            return {"id": owner_id}

    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.UserRepository",
        lambda _db: _Users(),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contact_onboarding_service.get_user_by_id",
        AsyncMock(return_value={"phone": current_phone, "user_metadata": {}}),
    )
    return svc


@pytest.mark.asyncio
async def test_send_phone_change_rejects_number_owned_by_another_user(monkeypatch):
    """Send refuses a phone that already belongs to a different auth user."""
    svc = _phone_service(monkeypatch, owner_id="other-user", current_phone="911111111111")

    with pytest.raises(ConflictException) as exc_info:
        await svc.send_phone_change(
            user_id="user-1",
            body=SendPhoneChangeRequest(phone_isd_code="+91", phone_number="9893085852"),
            access_token="token",
        )

    assert exc_info.value.message_key == "clients.errors.phone_number_already_exists"


@pytest.mark.asyncio
async def test_send_phone_change_requests_otp(monkeypatch):
    """Send asks Auth to text the code when the number is free."""
    svc = _phone_service(monkeypatch, owner_id=None, current_phone="911111111111")
    requested = AsyncMock()
    monkeypatch.setattr(svc, "_request_auth_phone_change", requested)

    result = await svc.send_phone_change(
        user_id="user-1",
        body=SendPhoneChangeRequest(phone_isd_code="+91", phone_number="9893085852"),
        access_token="token",
    )

    assert result == {"otp_sent": True, "already_verified": False}
    requested.assert_awaited_once_with(access_token="token", e164="+919893085852")


@pytest.mark.asyncio
async def test_send_phone_change_skips_otp_when_already_current(monkeypatch):
    """Send does not text a number Auth already has for this user."""
    svc = _phone_service(monkeypatch, owner_id="user-1", current_phone="+919893085852")

    result = await svc.send_phone_change(
        user_id="user-1",
        body=SendPhoneChangeRequest(phone_isd_code="+91", phone_number="9893085852"),
        access_token="token",
    )

    assert result == {"otp_sent": False, "already_verified": True}


@pytest.mark.asyncio
async def test_confirm_phone_change_requires_code_until_auth_matches(monkeypatch):
    """Confirm asks for the SMS code before Auth has the new number."""
    svc = _phone_service(monkeypatch, owner_id=None, current_phone="911111111111")

    with pytest.raises(BadRequestException) as exc_info:
        await svc.confirm_phone_change(
            contact_id="contact-1",
            user_id="user-1",
            body=ConfirmPhoneChangeRequest(phone_isd_code="+91", phone_number="9893085852"),
        )

    assert exc_info.value.message_key == "contact_onboarding.errors.phone_otp_required"


@pytest.mark.asyncio
async def test_confirm_phone_change_verifies_then_saves(monkeypatch):
    """Confirm verifies the code, then writes the contact and auth metadata."""
    svc = _phone_service(monkeypatch, owner_id=None, current_phone="911111111111")
    verify = AsyncMock()
    save = AsyncMock()
    sync = AsyncMock()
    monkeypatch.setattr(svc, "_verify_phone_change_otp", verify)
    monkeypatch.setattr(svc, "_save_contact_primary_phone", save)
    monkeypatch.setattr(svc, "_sync_auth_phone_metadata", sync)

    result = await svc.confirm_phone_change(
        contact_id="contact-1",
        user_id="user-1",
        body=ConfirmPhoneChangeRequest(
            phone_isd_code="+91",
            phone_number="9893085852",
            code="561166",
        ),
    )

    assert result == {"phone_isd_code": "+91", "phone_number": "9893085852"}
    verify.assert_awaited_once_with(e164="+919893085852", code="561166")
    save.assert_awaited_once()
    sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_phone_change_retries_without_code_after_auth_update(monkeypatch):
    """A retry after Auth already has the number updates the contact without a new OTP."""
    svc = _phone_service(monkeypatch, owner_id="user-1", current_phone="919893085852")
    verify = AsyncMock()
    save = AsyncMock()
    sync = AsyncMock()
    monkeypatch.setattr(svc, "_verify_phone_change_otp", verify)
    monkeypatch.setattr(svc, "_save_contact_primary_phone", save)
    monkeypatch.setattr(svc, "_sync_auth_phone_metadata", sync)

    await svc.confirm_phone_change(
        contact_id="contact-1",
        user_id="user-1",
        body=ConfirmPhoneChangeRequest(phone_isd_code="+91", phone_number="9893085852"),
    )

    verify.assert_not_awaited()
    save.assert_awaited_once_with(
        contact_id="contact-1",
        phone_isd_code="+91",
        phone_number="9893085852",
    )
