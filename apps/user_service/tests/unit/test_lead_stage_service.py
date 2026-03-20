"""Unit tests for LeadStageService business logic."""

from datetime import datetime, timezone

import pytest

from apps.user_service.app.schemas.lead_stages import (
    CreateLeadStageRequest,
    LeadStageColor,
)
from apps.user_service.app.services.lead_stage_service import LeadStageService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)


class _FakeLeadStageRepo:
    """Lightweight fake lead stage repository."""

    def __init__(self, db_connection=None):
        self.db_connection = db_connection
        self.calls = {}
        self.max_sort_order = 0
        self.stage_key_exists = False
        self.count = 0
        self.create_result = {"id": "stage-1"}
        self.create_error = None
        self.stages_result = []
        self.stage_by_id_result = None

    async def get_max_sort_order(self, organization_id):
        """Get the maximum sort order for a given organization."""
        self.calls["get_max_sort_order"] = organization_id
        return self.max_sort_order

    async def shift_sort_orders_for_insert(self, organization_id, target_position):
        """Shift the sort orders for insert."""
        self.calls["shift_sort_orders_for_insert"] = (organization_id, target_position)
        return None

    async def check_stage_key_exists(self, organization_id, stage_key):
        """Check if the stage key exists for a given organization."""
        self.calls["check_stage_key_exists"] = (organization_id, stage_key)
        return self.stage_key_exists

    async def count_stages(self, organization_id):
        """Count the number of stages for a given organization."""
        self.calls["count_stages"] = organization_id
        return self.count

    async def create_stage(self, stage_data):
        """Create a stage."""
        self.calls["create_stage"] = stage_data
        if self.create_error:
            raise self.create_error
        return self.create_result

    async def get_stages_by_organization(self, organization_id):
        """Get all stages by organization."""
        self.calls["get_stages_by_organization"] = organization_id
        return self.stages_result

    async def get_stage_by_id(self, organization_id, stage_id):
        """Get stage by id."""
        self.calls["get_stage_by_id"] = (organization_id, stage_id)
        return self.stage_by_id_result


def _ctx():
    """Reusable user context."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id="org-1",
        user_type="admin",
    )


def _service_with_fake_repo(monkeypatch):
    """Create service with monkeypatched repository."""
    fake_repo = _FakeLeadStageRepo()
    monkeypatch.setattr(
        "apps.user_service.app.services.lead_stage_service.LeadStageRepository",
        lambda db_connection=None: fake_repo,
    )
    service = LeadStageService(user_context=_ctx(), db_connection=None)
    return service, fake_repo


def test_generate_stage_key_normalizes_text():
    """generate_stage_key normalizes separators and symbols."""
    assert LeadStageService.generate_stage_key("  Qualified - Lead!  ") == "qualified_lead"


def test_generate_stage_key_raises_for_invalid_name():
    """generate_stage_key raises when slug becomes empty."""
    with pytest.raises(ValidationException) as exc_info:
        LeadStageService.generate_stage_key("!!!")
    assert exc_info.value.message_key == "lead_stages.errors.invalid_stage_name"


@pytest.mark.asyncio
async def test_resolve_sort_order_appends_when_missing(monkeypatch):
    """_resolve_sort_order_on_create appends when sort_order not provided."""
    service, fake_repo = _service_with_fake_repo(monkeypatch)
    fake_repo.max_sort_order = 4

    result = await service._resolve_sort_order_on_create("org-1", None)

    assert result == 5
    assert "shift_sort_orders_for_insert" not in fake_repo.calls


@pytest.mark.asyncio
async def test_resolve_sort_order_raises_for_out_of_range(monkeypatch):
    """_resolve_sort_order_on_create validates requested range."""
    service, fake_repo = _service_with_fake_repo(monkeypatch)
    fake_repo.max_sort_order = 2

    with pytest.raises(ValidationException) as exc_info:
        await service._resolve_sort_order_on_create("org-1", 5)

    assert exc_info.value.message_key == "lead_stages.errors.invalid_sort_order_range"


@pytest.mark.asyncio
async def test_resolve_sort_order_shifts_when_inserting(monkeypatch):
    """_resolve_sort_order_on_create shifts rows for in-between insert."""
    service, fake_repo = _service_with_fake_repo(monkeypatch)
    fake_repo.max_sort_order = 3

    result = await service._resolve_sort_order_on_create("org-1", 2)

    assert result == 2
    assert fake_repo.calls["shift_sort_orders_for_insert"] == ("org-1", 2)


@pytest.mark.asyncio
async def test_create_lead_stage_first_stage_forces_flags(monkeypatch):
    """create_lead_stage enforces first-stage bootstrap behavior."""
    service, fake_repo = _service_with_fake_repo(monkeypatch)
    fake_repo.count = 0
    request = CreateLeadStageRequest(
        stage_name=" New ",
        is_initial=False,
        is_final=False,
        sort_order=9,
        color=LeadStageColor.GREEN,
    )

    await service.create_lead_stage(request)

    payload = fake_repo.calls["create_stage"]
    assert payload["stage_name"] == "New"
    assert payload["stage_key"] == "new"
    assert payload["sort_order"] == 1
    assert payload["is_initial"] is True
    assert payload["is_final"] is True
    assert payload["color"] == "green"


@pytest.mark.asyncio
async def test_create_lead_stage_non_first_uses_body_flags(monkeypatch):
    """create_lead_stage keeps requested flags for non-first stage."""
    service, fake_repo = _service_with_fake_repo(monkeypatch)
    fake_repo.count = 2
    fake_repo.max_sort_order = 3
    request = CreateLeadStageRequest(
        stage_name="Qualified",
        is_initial=True,
        is_final=False,
        sort_order=2,
        description="Warm lead",
    )

    await service.create_lead_stage(request)

    payload = fake_repo.calls["create_stage"]
    assert payload["sort_order"] == 2
    assert payload["is_initial"] is True
    assert payload["is_final"] is False
    assert payload["description"] == "Warm lead"
    assert fake_repo.calls["shift_sort_orders_for_insert"] == ("org-1", 2)


@pytest.mark.asyncio
async def test_create_lead_stage_raises_when_stage_key_exists(monkeypatch):
    """create_lead_stage raises conflict on duplicate generated stage_key."""
    service, fake_repo = _service_with_fake_repo(monkeypatch)
    fake_repo.stage_key_exists = True
    request = CreateLeadStageRequest(stage_name="Qualified")

    with pytest.raises(ConflictException) as exc_info:
        await service.create_lead_stage(request)

    assert exc_info.value.message_key == "lead_stages.errors.stage_key_exists"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("constraint_name", "expected_message_key"),
    [
        ("uq_lsd_stage_key", "lead_stages.errors.stage_key_exists"),
        ("uq_lsd_stage_name", "lead_stages.errors.stage_name_exists"),
        ("uq_lsd_sort_order", "lead_stages.errors.sort_order_conflict"),
    ],
)
async def test_create_stage_maps_unique_constraint_conflicts(
    monkeypatch, constraint_name, expected_message_key
):
    """create_lead_stage translates DB unique violations into ConflictException."""
    service, fake_repo = _service_with_fake_repo(monkeypatch)
    fake_repo.count = 1
    fake_repo.max_sort_order = 1

    class _FakeUniqueViolationError(Exception):
        """Fake unique violation error."""

        def __init__(self, name):
            """Initialize the fake unique violation error."""
            super().__init__("duplicate")
            self.constraint_name = name

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_stage_service.UniqueViolationError",
        _FakeUniqueViolationError,
    )
    fake_repo.create_error = _FakeUniqueViolationError(constraint_name)
    request = CreateLeadStageRequest(stage_name="Qualified")

    with pytest.raises(ConflictException) as exc_info:
        await service.create_lead_stage(request)

    assert exc_info.value.message_key == expected_message_key


@pytest.mark.asyncio
async def test_list_lead_stages_returns_serialized_items(monkeypatch):
    """list_lead_stages returns serialized rows with total count."""
    service, fake_repo = _service_with_fake_repo(monkeypatch)
    now = datetime.now(timezone.utc)
    fake_repo.stages_result = [
        {
            "id": "stage-1",
            "stage_name": "New",
            "stage_key": "new",
            "description": None,
            "color": "blue",
            "sort_order": 1,
            "is_initial": True,
            "is_final": False,
            "created_at": now,
            "updated_at": now,
        }
    ]

    items, total = await service.list_lead_stages()

    assert total == 1
    assert items[0]["id"] == "stage-1"
    assert items[0]["stage_key"] == "new"
    assert items[0]["created_at"] == now.isoformat()
    assert fake_repo.calls["get_stages_by_organization"] == "org-1"


@pytest.mark.asyncio
async def test_get_lead_stage_returns_serialized_item(monkeypatch):
    """get_lead_stage returns stage details when stage exists."""
    service, fake_repo = _service_with_fake_repo(monkeypatch)
    now = datetime.now(timezone.utc)
    fake_repo.stage_by_id_result = {
        "id": "stage-1",
        "stage_name": "Qualified",
        "stage_key": "qualified",
        "description": "Warm lead",
        "color": "green",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
        "created_at": now,
        "updated_at": now,
    }

    item = await service.get_lead_stage("stage-1")

    assert item["id"] == "stage-1"
    assert item["stage_name"] == "Qualified"
    assert item["updated_at"] == now.isoformat()
    assert fake_repo.calls["get_stage_by_id"] == ("org-1", "stage-1")


@pytest.mark.asyncio
async def test_get_lead_stage_raises_not_found(monkeypatch):
    """get_lead_stage raises NotFoundException when stage missing."""
    service, fake_repo = _service_with_fake_repo(monkeypatch)
    fake_repo.stage_by_id_result = None

    with pytest.raises(NotFoundException) as exc_info:
        await service.get_lead_stage("missing-id")

    assert exc_info.value.message_key == "lead_stages.errors.stage_not_found"
