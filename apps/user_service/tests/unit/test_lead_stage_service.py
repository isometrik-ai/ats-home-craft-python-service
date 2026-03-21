"""Unit tests for LeadStageService business logic."""

from datetime import datetime, timezone

import pytest

from apps.user_service.app.schemas.lead_stages import (
    CreateLeadStageRequest,
    LeadStageColor,
    UpdateLeadStageRequest,
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
        self.create_stage_result = {"id": "stage-1"}
        self.create_stage_error = None
        self.stages_result = []
        self.stage_by_id_result = None
        self.initial_count = 1
        self.final_count = 1
        self.update_fields_result = None
        self.update_stage_error = None
        self.delete_stage_result = None

    async def summarize_organization_for_new_stage(self, organization_id, stage_key):
        """Return org-level summary used before insert."""
        self.calls["summarize_organization_for_new_stage"] = (organization_id, stage_key)
        return {
            "total_stages": self.count,
            "max_sort_order": self.max_sort_order,
            "stage_key_exists": self.stage_key_exists,
        }

    async def adjust_sort_orders(self, organization_id, *, min_sort_order, max_sort_order, delta):
        """Generic sort_order bulk update."""
        self.calls.setdefault("adjust_sort_orders", []).append(
            (organization_id, min_sort_order, max_sort_order, delta)
        )
        return None

    async def create_stage(self, stage_data):
        """Insert a stage row."""
        self.calls["create_stage"] = stage_data
        if self.create_stage_error:
            raise self.create_stage_error
        return self.create_stage_result

    async def list_stages_by_organization(self, organization_id):
        """List stages for org."""
        self.calls["list_stages_by_organization"] = organization_id
        return self.stages_result

    async def get_stage_by_id(self, organization_id, stage_id):
        """Fetch one stage by id."""
        self.calls["get_stage_by_id"] = (organization_id, stage_id)
        return self.stage_by_id_result

    async def get_stage_by_id_with_organization_metrics(
        self, organization_id, stage_id, proposed_stage_key=None
    ):
        """Fetch stage row plus org metrics for PATCH validation."""
        self.calls["get_stage_by_id_with_organization_metrics"] = (
            organization_id,
            stage_id,
            proposed_stage_key,
        )
        base = self.stage_by_id_result
        if base is None:
            return None
        return {
            **base,
            "total_stages": self.count,
            "key_conflict_count": 1 if self.stage_key_exists else 0,
            "other_initial_count": self.initial_count,
            "other_final_count": self.final_count,
        }

    async def update_stage(self, organization_id, stage_id, update_data):
        """Update stage fields."""
        self.calls["update_stage"] = (organization_id, stage_id, update_data)
        if self.update_stage_error:
            raise self.update_stage_error
        return self.update_fields_result

    async def delete_stage(self, organization_id, stage_id):
        """Delete stage row."""
        self.calls["delete_stage"] = (organization_id, stage_id)
        return self.delete_stage_result


def _ctx():
    """Reusable user context."""
    return UserContext(
        user_id="u1",
        email="u1@example.com",
        organization_id="org-1",
        user_type="admin",
    )


def _service_with_fake_repo():
    """Create service with injected fake repository."""
    fake_repo = _FakeLeadStageRepo()
    service = LeadStageService(
        user_context=_ctx(),
        db_connection=None,
        lead_stage_repository=fake_repo,
    )
    return service, fake_repo


def test_generate_stage_key_normalizes_text():
    """generate_stage_key normalizes separators and symbols."""
    assert LeadStageService.generate_stage_key("  Qualified - Lead!  ") == "qualified_lead"


def test_generate_stage_key_raises_for_invalid_name():
    """generate_stage_key raises when slug becomes empty."""
    with pytest.raises(ValidationException) as exc_info:
        LeadStageService.generate_stage_key("!!!")
    assert exc_info.value.message_key == "lead_stages.errors.invalid_stage_name"


def test_reorder_intermediate_window_when_moving_later():
    """Rows between old and new positions shift down by one sort index."""
    assert LeadStageService._reorder_intermediate_window(1, 4) == (2, 4, -1)


def test_reorder_window_when_moving_earlier():
    """Rows between new and old positions shift up by one sort index."""
    assert LeadStageService._reorder_intermediate_window(5, 2) == (2, 4, 1)


def test_reorder_intermediate_window_none_when_no_move():
    """Same position implies no intermediate rows to adjust."""
    assert LeadStageService._reorder_intermediate_window(3, 3) is None


@pytest.mark.asyncio
async def test_resolve_sort_order_appends_when_missing():
    """_resolve_sort_order_on_create appends when sort_order not provided."""
    service, fake_repo = _service_with_fake_repo()
    fake_repo.max_sort_order = 4

    result = await service._resolve_sort_order_on_create("org-1", None, max_sort_order=4)

    assert result == 5
    assert "adjust_sort_orders" not in fake_repo.calls


@pytest.mark.asyncio
async def test_resolve_sort_order_raises_for_out_of_range():
    """_resolve_sort_order_on_create validates requested range."""
    service, fake_repo = _service_with_fake_repo()
    fake_repo.max_sort_order = 2

    with pytest.raises(ValidationException) as exc_info:
        await service._resolve_sort_order_on_create("org-1", 5, max_sort_order=2)

    assert exc_info.value.message_key == "lead_stages.errors.invalid_sort_order_range"


@pytest.mark.asyncio
async def test_resolve_sort_order_shifts_when_inserting():
    """_resolve_sort_order_on_create shifts rows for in-between insert."""
    service, fake_repo = _service_with_fake_repo()
    fake_repo.max_sort_order = 3

    result = await service._resolve_sort_order_on_create("org-1", 2, max_sort_order=3)

    assert result == 2
    assert fake_repo.calls["adjust_sort_orders"] == [("org-1", 2, None, 1)]


@pytest.mark.asyncio
async def test_create_lead_stage_first_stage_forces_flags():
    """create_lead_stage enforces first-stage bootstrap behavior."""
    service, fake_repo = _service_with_fake_repo()
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
async def test_create_lead_stage_non_first_uses_body_flags():
    """create_lead_stage keeps requested flags for non-first stage."""
    service, fake_repo = _service_with_fake_repo()
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
    assert fake_repo.calls["adjust_sort_orders"] == [("org-1", 2, None, 1)]


@pytest.mark.asyncio
async def test_create_lead_stage_raises_when_stage_key_exists():
    """create_lead_stage raises conflict on duplicate generated stage_key."""
    service, fake_repo = _service_with_fake_repo()
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
    service, fake_repo = _service_with_fake_repo()
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
    fake_repo.create_stage_error = _FakeUniqueViolationError(constraint_name)
    request = CreateLeadStageRequest(stage_name="Qualified")

    with pytest.raises(ConflictException) as exc_info:
        await service.create_lead_stage(request)

    assert exc_info.value.message_key == expected_message_key


@pytest.mark.asyncio
async def test_create_stage_reraises_unknown_unique_violation(monkeypatch):
    """create_lead_stage propagates UniqueViolationError for unrecognized constraints."""
    service, fake_repo = _service_with_fake_repo()
    fake_repo.count = 1
    fake_repo.max_sort_order = 1

    class _FakeUniqueViolationError(Exception):
        """Fake unique violation error."""

        def __init__(self, name):
            """Initialize with constraint name."""
            super().__init__("duplicate")
            self.constraint_name = name

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_stage_service.UniqueViolationError",
        _FakeUniqueViolationError,
    )
    fake_repo.create_stage_error = _FakeUniqueViolationError("other_constraint")
    request = CreateLeadStageRequest(stage_name="Qualified")

    with pytest.raises(_FakeUniqueViolationError):
        await service.create_lead_stage(request)


@pytest.mark.asyncio
async def test_list_lead_stages_returns_serialized_items():
    """list_lead_stages returns serialized rows with total count."""
    service, fake_repo = _service_with_fake_repo()
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
    assert fake_repo.calls["list_stages_by_organization"] == "org-1"


@pytest.mark.asyncio
async def test_get_lead_stage_returns_serialized_item():
    """get_lead_stage returns stage details when stage exists."""
    service, fake_repo = _service_with_fake_repo()
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
async def test_get_lead_stage_raises_not_found():
    """get_lead_stage raises NotFoundException when stage missing."""
    service, fake_repo = _service_with_fake_repo()
    fake_repo.stage_by_id_result = None

    with pytest.raises(NotFoundException) as exc_info:
        await service.get_lead_stage("missing-id")

    assert exc_info.value.message_key == "lead_stages.errors.stage_not_found"


@pytest.mark.asyncio
async def test_update_lead_stage_raises_not_found():
    """update_lead_stage raises when the stage id does not exist for the org."""
    service, fake_repo = _service_with_fake_repo()
    fake_repo.stage_by_id_result = None

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_lead_stage("missing-id", UpdateLeadStageRequest(stage_name="X"))

    assert exc_info.value.message_key == "lead_stages.errors.stage_not_found"


@pytest.mark.asyncio
async def test_update_lead_stage_reorders_and_updates_fields():
    """update_lead_stage reorders and updates mutable fields."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    fake_repo.count = 5
    fake_repo.stage_by_id_result = {
        "id": "stage-1",
        "stage_name": "New",
        "stage_key": "new",
        "description": None,
        "color": "blue",
        "sort_order": 1,
        "is_initial": True,
        "is_final": True,
        "created_at": now,
        "updated_at": now,
    }
    fake_repo.initial_count = 2
    fake_repo.final_count = 2
    fake_repo.update_fields_result = {
        **fake_repo.stage_by_id_result,
        "stage_name": "Qualified",
        "description": "Warm lead",
        "color": "green",
        "sort_order": 3,
        "is_initial": False,
        "is_final": False,
    }

    body = UpdateLeadStageRequest(
        stage_name="Qualified",
        description="Warm lead",
        color=LeadStageColor.GREEN,
        sort_order=3,
        is_initial=False,
        is_final=False,
    )
    result = await service.update_lead_stage("stage-1", body)

    assert result["stage_name"] == "Qualified"
    assert fake_repo.calls["adjust_sort_orders"][0] == ("org-1", 2, 3, -1)
    _, _, updated_fields = fake_repo.calls["update_stage"]
    assert updated_fields["stage_key"] == "qualified"
    assert updated_fields["is_initial"] is False
    assert updated_fields["is_final"] is False
    assert updated_fields["sort_order"] == 3


@pytest.mark.asyncio
async def test_update_lead_stage_rejects_unset_last_initial():
    """update_lead_stage rejects unsetting last initial stage."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    fake_repo.stage_by_id_result = {
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
    fake_repo.initial_count = 0

    with pytest.raises(ValidationException) as exc_info:
        await service.update_lead_stage("stage-1", UpdateLeadStageRequest(is_initial=False))

    assert exc_info.value.message_key == "lead_stages.errors.cannot_unset_last_initial"


@pytest.mark.asyncio
async def test_update_lead_stage_rejects_unset_last_final():
    """update_lead_stage rejects unsetting last final stage."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    fake_repo.stage_by_id_result = {
        "id": "stage-1",
        "stage_name": "Won",
        "stage_key": "won",
        "description": None,
        "color": "green",
        "sort_order": 3,
        "is_initial": False,
        "is_final": True,
        "created_at": now,
        "updated_at": now,
    }
    fake_repo.final_count = 0

    with pytest.raises(ValidationException) as exc_info:
        await service.update_lead_stage("stage-1", UpdateLeadStageRequest(is_final=False))

    assert exc_info.value.message_key == "lead_stages.errors.cannot_unset_last_final"


@pytest.mark.asyncio
async def test_update_lead_stage_rejects_key_conflict():
    """update_lead_stage raises when renamed stage_key would collide with another row."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    fake_repo.stage_by_id_result = {
        "id": "stage-1",
        "stage_name": "Alpha",
        "stage_key": "alpha",
        "description": None,
        "color": "blue",
        "sort_order": 1,
        "is_initial": True,
        "is_final": False,
        "created_at": now,
        "updated_at": now,
    }
    fake_repo.count = 3
    fake_repo.stage_key_exists = True

    with pytest.raises(ConflictException) as exc_info:
        await service.update_lead_stage("stage-1", UpdateLeadStageRequest(stage_name="Beta"))

    assert exc_info.value.message_key == "lead_stages.errors.stage_key_exists"


@pytest.mark.asyncio
async def test_update_stage_rejects_sort_order_out_of_range():
    """update_lead_stage validates sort_order against current org stage count."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    fake_repo.count = 3
    fake_repo.stage_by_id_result = {
        "id": "stage-1",
        "stage_name": "Mid",
        "stage_key": "mid",
        "description": None,
        "color": "blue",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
        "created_at": now,
        "updated_at": now,
    }

    with pytest.raises(ValidationException) as exc_info:
        await service.update_lead_stage("stage-1", UpdateLeadStageRequest(sort_order=10))

    assert exc_info.value.message_key == "lead_stages.errors.invalid_sort_order_range"


@pytest.mark.asyncio
async def test_update_no_op_when_sort_order_unchanged():
    """update_lead_stage skips persist when sort_order matches and no other mutations."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    fake_repo.count = 3
    fake_repo.stage_by_id_result = {
        "id": "stage-1",
        "stage_name": "Mid",
        "stage_key": "mid",
        "description": None,
        "color": "blue",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
        "created_at": now,
        "updated_at": now,
    }

    result = await service.update_lead_stage("stage-1", UpdateLeadStageRequest(sort_order=2))

    assert result["stage_key"] == "mid"
    assert result["sort_order"] == 2
    assert fake_repo.calls.get("update_stage") is None


@pytest.mark.asyncio
async def test_update_raises_not_found_returns_none():
    """update_lead_stage raises if UPDATE matches no row after validation."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    fake_repo.count = 2
    fake_repo.stage_by_id_result = {
        "id": "stage-1",
        "stage_name": "Mid",
        "stage_key": "mid",
        "description": None,
        "color": "blue",
        "sort_order": 1,
        "is_initial": False,
        "is_final": False,
        "created_at": now,
        "updated_at": now,
    }
    fake_repo.update_fields_result = None

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_lead_stage("stage-1", UpdateLeadStageRequest(stage_name="Renamed"))

    assert exc_info.value.message_key == "lead_stages.errors.stage_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("constraint_name", "expected_message_key"),
    [
        ("uq_lsd_stage_key", "lead_stages.errors.stage_key_exists"),
        ("uq_lsd_stage_name", "lead_stages.errors.stage_name_exists"),
    ],
)
async def test_update_maps_unique_constraint_conflicts(
    monkeypatch, constraint_name, expected_message_key
):
    """update_lead_stage maps unique violations from persist into ConflictException."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    fake_repo.count = 2
    fake_repo.stage_by_id_result = {
        "id": "stage-1",
        "stage_name": "Mid",
        "stage_key": "mid",
        "description": None,
        "color": "blue",
        "sort_order": 1,
        "is_initial": False,
        "is_final": False,
        "created_at": now,
        "updated_at": now,
    }

    class _FakeUniqueViolationError(Exception):
        """Fake unique violation error."""

        def __init__(self, name):
            """Initialize with constraint name."""
            super().__init__("duplicate")
            self.constraint_name = name

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_stage_service.UniqueViolationError",
        _FakeUniqueViolationError,
    )
    fake_repo.update_stage_error = _FakeUniqueViolationError(constraint_name)

    with pytest.raises(ConflictException) as exc_info:
        await service.update_lead_stage("stage-1", UpdateLeadStageRequest(stage_name="Other"))

    assert exc_info.value.message_key == expected_message_key


@pytest.mark.asyncio
async def test_update_reraises_unknown_unique_violation(monkeypatch):
    """update_lead_stage propagates UniqueViolationError for unrecognized constraints."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    fake_repo.count = 2
    fake_repo.stage_by_id_result = {
        "id": "stage-1",
        "stage_name": "Mid",
        "stage_key": "mid",
        "description": None,
        "color": "blue",
        "sort_order": 1,
        "is_initial": False,
        "is_final": False,
        "created_at": now,
        "updated_at": now,
    }

    class _FakeUniqueViolationError(Exception):
        """Fake unique violation error."""

        def __init__(self, name):
            """Initialize with constraint name."""
            super().__init__("duplicate")
            self.constraint_name = name

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_stage_service.UniqueViolationError",
        _FakeUniqueViolationError,
    )
    fake_repo.update_stage_error = _FakeUniqueViolationError("uq_lsd_sort_order")

    with pytest.raises(_FakeUniqueViolationError):
        await service.update_lead_stage("stage-1", UpdateLeadStageRequest(stage_name="Other"))


@pytest.mark.asyncio
async def test_delete_lead_stage_deletes_shifts_sort_order():
    """delete_lead_stage removes row then decrements sort_order for stages after the gap."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    row = {
        "id": "stage-2",
        "stage_name": "Screening",
        "stage_key": "screening",
        "description": None,
        "color": "blue",
        "sort_order": 2,
        "is_initial": False,
        "is_final": False,
        "created_at": now,
        "updated_at": now,
    }
    fake_repo.delete_stage_result = row

    deleted = await service.delete_lead_stage("stage-2")

    assert deleted["id"] == "stage-2"
    assert fake_repo.calls["delete_stage"] == ("org-1", "stage-2")
    assert fake_repo.calls["adjust_sort_orders"][-1] == ("org-1", 3, None, -1)


@pytest.mark.asyncio
async def test_delete_lead_stage_allows_last_remaining():
    """delete_lead_stage removes the final org stage (zero stages left)."""
    service, fake_repo = _service_with_fake_repo()
    now = datetime.now(timezone.utc)
    row = {
        "id": "stage-1",
        "stage_name": "New",
        "stage_key": "new",
        "description": None,
        "color": "blue",
        "sort_order": 1,
        "is_initial": True,
        "is_final": True,
        "created_at": now,
        "updated_at": now,
    }
    fake_repo.delete_stage_result = row

    deleted = await service.delete_lead_stage("stage-1")

    assert deleted["id"] == "stage-1"
    assert fake_repo.calls["delete_stage"] == ("org-1", "stage-1")
    assert fake_repo.calls["adjust_sort_orders"][-1] == ("org-1", 2, None, -1)


@pytest.mark.asyncio
async def test_delete_lead_stage_raises_not_found():
    """delete_lead_stage raises when DELETE matches no row."""
    service, fake_repo = _service_with_fake_repo()
    fake_repo.delete_stage_result = None

    with pytest.raises(NotFoundException) as exc_info:
        await service.delete_lead_stage("missing-id")

    assert exc_info.value.message_key == "lead_stages.errors.stage_not_found"


def test_update_request_rejects_empty_payload():
    """UpdateLeadStageRequest requires at least one explicit field."""
    with pytest.raises(ValidationException) as exc_info:
        UpdateLeadStageRequest()

    assert exc_info.value.message_key == "lead_stages.errors.empty_update_payload"


def test_create_request_rejects_blank_stage_name():
    """CreateLeadStageRequest rejects whitespace-only stage_name."""
    with pytest.raises(ValidationException) as exc_info:
        CreateLeadStageRequest(stage_name="   ")

    assert exc_info.value.message_key == "lead_stages.errors.stage_name_required"
