"""Unit tests for LeadService business logic."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from apps.user_service.app.schemas.enums import (
    EntityType,
    IntakeStage,
    LeadsListMode,
    LeadStatus,
)
from apps.user_service.app.schemas.leads import (
    CreateLeadRequest,
    LeadsListQueryParams,
    UpdateLeadRequest,
)
from apps.user_service.app.services.lead_service import LeadService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException

ORG_ID = "org-1"
CTX_USER_ID = "33333333-3333-3333-3333-333333333333"

CLIENT_ID = "11111111-1111-1111-1111-111111111111"
STAGE_ID_1 = "22222222-2222-2222-2222-222222222222"
STAGE_ID_2 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OWNER_ID = "44444444-4444-4444-4444-444444444444"
POINT_OF_CONTACT_ID = "55555555-5555-5555-5555-555555555555"
LEAD_ID = "66666666-6666-6666-6666-666666666666"


def _ctx() -> UserContext:
    """Reusable user context."""
    return UserContext(
        user_id=CTX_USER_ID,
        email="u1@example.com",
        organization_id=ORG_ID,
        user_type="admin",
    )


class _FakeLeadRepository:
    """Lightweight fake LeadRepository."""

    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}
        self.get_client_existence_result: bool = True
        self.create_lead_result: dict[str, Any] = {"id": LEAD_ID}
        self.get_lead_detail_by_id_result: dict[str, Any] | None = None
        self.update_lead_result: dict[str, Any] | None = None
        self.count_leads_filtered_result: int = 0
        self.list_leads_page_result: list[dict[str, Any]] = []
        self.list_leads_for_kanban_result: list[dict[str, Any]] = []
        self.delete_lead_result: dict[str, Any] | None = None

    async def get_client_existence(
        self,
        organization_id: str,
        client_id: str,
    ) -> bool:
        """Return client existence."""
        self.calls["get_client_existence"] = (organization_id, client_id)
        return self.get_client_existence_result

    async def create_lead(self, lead_row: dict[str, Any]) -> dict[str, Any]:
        """Create lead."""
        self.calls["create_lead"] = lead_row
        return self.create_lead_result

    async def get_lead_detail_by_id(
        self,
        organization_id: str,
        lead_id: str,
    ) -> dict[str, Any] | None:
        """Get lead detail by id."""
        self.calls["get_lead_detail_by_id"] = (organization_id, lead_id)
        return self.get_lead_detail_by_id_result

    async def update_lead(
        self,
        organization_id: str,
        lead_id: str,
        update_data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update lead."""
        self.calls["update_lead"] = (organization_id, lead_id, update_data)
        return self.update_lead_result

    async def count_leads_filtered(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        search: str | None = None,
    ) -> int:
        """Count leads filtered."""
        self.calls["count_leads_filtered"] = (organization_id, stage_id, search)
        return self.count_leads_filtered_result

    async def list_leads_page(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        search: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List leads page."""
        self.calls["list_leads_page"] = (organization_id, stage_id, search, limit, offset)
        return self.list_leads_page_result

    async def list_leads_for_kanban(
        self,
        organization_id: str,
        *,
        stage_id: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """List leads for kanban."""
        self.calls["list_leads_for_kanban"] = (organization_id, stage_id, search)
        return self.list_leads_for_kanban_result

    async def delete_lead(
        self,
        organization_id: str,
        lead_id: str,
    ) -> dict[str, Any] | None:
        """Delete lead."""
        self.calls["delete_lead"] = (organization_id, lead_id)
        return self.delete_lead_result


class _FakeLeadStageRepository:
    """Lightweight fake LeadStageRepository."""

    def __init__(self) -> None:
        """Initialize LeadStageRepository."""
        self.calls: dict[str, Any] = {}
        self.get_stage_by_id_result: dict[str, Any] | None = None
        self.list_stages_by_organization_result: list[dict[str, Any]] = []

    async def get_stage_by_id(
        self,
        organization_id: str,
        stage_id: str,
    ) -> dict[str, Any] | None:
        """Get stage by id."""
        self.calls["get_stage_by_id"] = (organization_id, stage_id)
        return self.get_stage_by_id_result

    async def list_stages_by_organization(self, organization_id: str) -> list[dict[str, Any]]:
        """List stages by organization."""
        self.calls["list_stages_by_organization"] = organization_id
        return self.list_stages_by_organization_result


class _FakeClientRepository:
    """Lightweight fake ClientRepository."""

    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}
        self.client_exists_in_organization_result: bool = True

    async def client_exists_in_organization(
        self,
        organization_id: str,
        client_id: str,
    ) -> bool:
        """Check if client exists in organization."""
        self.calls["client_exists_in_organization"] = (organization_id, client_id)
        return self.client_exists_in_organization_result


class _FakeUserRepository:
    """Lightweight fake UserRepository."""

    def __init__(self) -> None:
        self.calls: dict[str, Any] = {}
        self.get_user_details_by_id_result: dict[str, Any] | None = None

    async def get_user_details_by_id(
        self,
        user_id: str,
        columns: list[str],
    ) -> dict[str, Any] | None:
        """Get user details by id."""
        self.calls["get_user_details_by_id"] = (user_id, columns)
        return self.get_user_details_by_id_result


def _service_with_fakes() -> tuple[
    LeadService,
    _FakeLeadRepository,
    _FakeLeadStageRepository,
    _FakeClientRepository,
    _FakeUserRepository,
]:
    """Create service with injected fake repositories."""
    lead_repo = _FakeLeadRepository()
    stage_repo = _FakeLeadStageRepository()
    client_repo = _FakeClientRepository()
    user_repo = _FakeUserRepository()

    service = LeadService(
        db_connection=None,
        user_context=_ctx(),
        client_repository=client_repo,
        lead_repository=lead_repo,
        lead_stage_repository=stage_repo,
        user_repository=user_repo,
    )
    return service, lead_repo, stage_repo, client_repo, user_repo


def _patch_custom_field_service(monkeypatch: pytest.MonkeyPatch, calls: dict[str, Any]) -> None:
    """Monkeypatch CustomFieldService for deterministic tests."""

    class _FakeCustomFieldService:
        """Lightweight fake CustomFieldService."""

        def __init__(self, db_connection: Any = None, user_context: Any = None) -> None:
            """Initialize CustomFieldService."""
            # Calls captured via closure; no instance state needed.

        async def validate_for_create(
            self,
            custom_fields: list[dict[str, Any]] | None,
            entity_type: EntityType,
        ) -> list[dict[str, Any]]:
            """Record call and return payload passthrough."""
            calls["validate_for_create"] = (custom_fields, entity_type)
            return list(custom_fields) if custom_fields else []

        async def merge_for_update(
            self,
            payload: list[dict[str, Any]] | None,
            stored: Any,
            entity_type: EntityType,
        ) -> list[dict[str, Any]]:
            """Record call and merge FieldCell lists (test double)."""
            calls["merge_for_update"] = (payload, stored, entity_type)
            stored_list = stored if isinstance(stored, list) else []
            by_id: dict[str, dict[str, Any]] = {}
            for cell in stored_list:
                if isinstance(cell, dict) and cell.get("field_id"):
                    by_id[str(cell["field_id"])] = dict(cell)
            for cell in payload or []:
                if not isinstance(cell, dict):
                    continue
                fid = str(cell.get("field_id") or "")
                if not fid:
                    continue
                if cell.get("value") is None and "value" in cell:
                    by_id.pop(fid, None)
                    continue
                prev = by_id.get(fid, {})
                nxt = {**prev, **cell, "field_id": fid}
                by_id[fid] = nxt
            return list(by_id.values())

    monkeypatch.setattr(
        "apps.user_service.app.services.lead_service.CustomFieldService",
        _FakeCustomFieldService,
    )


@pytest.mark.asyncio
async def test_create_lead_client_missing_raises(monkeypatch):
    """create_lead raises NotFoundException when client doesn't exist."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()
    lead_repo.get_client_existence_result = False

    custom_calls: dict[str, Any] = {}
    _patch_custom_field_service(monkeypatch, custom_calls)

    body = CreateLeadRequest(
        client_id=CLIENT_ID,
        name="New Lead",
        stage_id=STAGE_ID_1,
        intake_stage=IntakeStage.INITIAL_CONTACT,
        lead_status=LeadStatus.PROSPECT,
    )

    with pytest.raises(NotFoundException) as exc_info:
        await service.create_lead(body)

    assert exc_info.value.message_key == "clients.errors.not_found"
    assert not custom_calls  # Should fail before custom-field validation.
    assert not stage_repo.calls
    assert not client_repo.calls
    assert not user_repo.calls


@pytest.mark.asyncio
async def test_create_lead_stage_missing_raises(monkeypatch):
    """create_lead raises NotFoundException when provided stage doesn't exist."""
    service, lead_repo, stage_repo, _, _ = _service_with_fakes()
    lead_repo.get_client_existence_result = True
    stage_repo.get_stage_by_id_result = None

    custom_calls: dict[str, Any] = {}
    _patch_custom_field_service(monkeypatch, custom_calls)

    body = CreateLeadRequest(
        client_id=CLIENT_ID,
        name="New Lead",
        stage_id=STAGE_ID_1,
    )

    with pytest.raises(NotFoundException) as exc_info:
        await service.create_lead(body)

    assert exc_info.value.message_key == "lead_stages.errors.stage_not_found"
    assert not custom_calls  # Stage check happens before custom-field validation.


@pytest.mark.asyncio
async def test_create_lead_payload_and_poc_validation(monkeypatch):
    """Successful create_lead builds the expected DB payload and validates PoC."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()
    lead_repo.get_client_existence_result = True
    stage_repo.get_stage_by_id_result = {"id": STAGE_ID_1}
    client_repo.client_exists_in_organization_result = True

    custom_calls: dict[str, Any] = {}
    _patch_custom_field_service(monkeypatch, custom_calls)

    converted_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    body = CreateLeadRequest(
        client_id=CLIENT_ID,
        name="New Lead",
        stage_id=STAGE_ID_1,
        intake_stage=IntakeStage.INITIAL_CONTACT,
        lead_source="Referral",
        referral_source="Partner",
        lead_score="high",
        close_date=date(2026, 1, 10),
        converted_at=converted_at,
        notes="Notes",
        amount=Decimal("100.50"),
        description="Opportunity desc",
        point_of_contact=POINT_OF_CONTACT_ID,
        lead_status=LeadStatus.QUALIFIED,
    )

    result = await service.create_lead(body)

    assert result == lead_repo.create_lead_result
    assert lead_repo.calls["get_client_existence"] == (ORG_ID, CLIENT_ID)
    assert stage_repo.calls["get_stage_by_id"] == (ORG_ID, STAGE_ID_1)
    assert client_repo.calls["client_exists_in_organization"] == (ORG_ID, POINT_OF_CONTACT_ID)

    payload = lead_repo.calls["create_lead"]
    assert payload["client_id"] == CLIENT_ID
    assert payload["organization_id"] == ORG_ID
    assert payload["name"] == "New Lead"
    assert payload["stage_id"] == STAGE_ID_1
    assert payload["lead_status"] == LeadStatus.QUALIFIED.value
    assert payload["intake_stage"] == IntakeStage.INITIAL_CONTACT.value
    assert payload["created_by"] == CTX_USER_ID
    assert payload["owner_id"] == CTX_USER_ID  # owner_id defaults to creator
    assert payload["point_of_contact"] == POINT_OF_CONTACT_ID
    assert not payload["custom_fields"]

    assert custom_calls["validate_for_create"][0] == []
    assert custom_calls["validate_for_create"][1] == EntityType.LEAD
    # owner_id was omitted, so no user lookup should occur.
    assert not user_repo.calls


@pytest.mark.asyncio
async def test_create_lead_owner_id_validation(monkeypatch):
    """create_lead validates owner_id against user repository when explicitly provided."""
    service, lead_repo, stage_repo, _, user_repo = _service_with_fakes()
    lead_repo.get_client_existence_result = True
    stage_repo.get_stage_by_id_result = {"id": STAGE_ID_1}
    user_repo.get_user_details_by_id_result = {"id": OWNER_ID}

    custom_calls: dict[str, Any] = {}
    _patch_custom_field_service(monkeypatch, custom_calls)

    body = CreateLeadRequest(
        client_id=CLIENT_ID,
        name="New Lead",
        stage_id=STAGE_ID_1,
        owner_id=OWNER_ID,
    )

    result = await service.create_lead(body)

    assert result == lead_repo.create_lead_result
    assert user_repo.calls["get_user_details_by_id"] == (OWNER_ID, ["id"])
    payload = lead_repo.calls["create_lead"]
    assert payload["created_by"] == CTX_USER_ID
    assert payload["owner_id"] == OWNER_ID


@pytest.mark.asyncio
async def test_update_lead_missing_raises():
    """update_lead raises NotFoundException when the lead doesn't exist."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()
    lead_repo.get_lead_detail_by_id_result = None

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_lead(LEAD_ID, UpdateLeadRequest(name="New name"))

    assert exc_info.value.message_key == "leads.errors.not_found"
    assert "update_lead" not in lead_repo.calls
    assert not stage_repo.calls
    assert not client_repo.calls
    assert not user_repo.calls


@pytest.mark.asyncio
async def test_update_lead_stage_validation():
    """update_lead validates stage_id existence when stage_id is updated to a non-null UUID."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()
    lead_repo.get_lead_detail_by_id_result = {"id": LEAD_ID, "custom_fields": []}
    stage_repo.get_stage_by_id_result = None

    with pytest.raises(NotFoundException) as exc_info:
        await service.update_lead(
            LEAD_ID,
            UpdateLeadRequest(stage_id=STAGE_ID_2),
        )

    assert exc_info.value.message_key == "lead_stages.errors.stage_not_found"
    assert "update_lead" not in lead_repo.calls
    assert not client_repo.calls
    assert not user_repo.calls


@pytest.mark.asyncio
async def test_update_lead_custom_fields_merge(monkeypatch):
    """update_lead merges custom_fields FieldCells; explicit null clears optional root."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()
    lead_repo.get_lead_detail_by_id_result = {
        "id": LEAD_ID,
        "custom_fields": (
            "["
            '{"field_id":"f_old","instance_id":"a","value":"x"},'
            '{"field_id":"f_keep","instance_id":"b","value":"y"}'
            "]"
        ),
    }
    merged = [
        {"field_id": "f_keep", "instance_id": "b", "value": "y"},
        {"field_id": "f_new", "instance_id": "n", "value": "z"},
    ]
    lead_repo.update_lead_result = {"id": LEAD_ID, "custom_fields": merged}

    custom_calls: dict[str, Any] = {}
    _patch_custom_field_service(monkeypatch, custom_calls)

    previous, updated = await service.update_lead(
        LEAD_ID,
        UpdateLeadRequest(
            custom_fields=[
                {"field_id": "f_old", "value": None},
                {"field_id": "f_new", "instance_id": "n", "value": "z"},
            ]
        ),
    )

    assert previous == lead_repo.get_lead_detail_by_id_result
    assert updated == lead_repo.update_lead_result
    update_data = lead_repo.calls["update_lead"][2]
    assert update_data["custom_fields"] == merged
    patch_arg = custom_calls["merge_for_update"][0]
    assert patch_arg == [
        {"field_id": "f_old", "value": None},
        {"field_id": "f_new", "instance_id": "n", "value": "z"},
    ]
    assert custom_calls["merge_for_update"][2] == EntityType.LEAD
    # No stage/owner/poc validations were triggered for this body.
    assert not stage_repo.calls
    assert not client_repo.calls
    assert not user_repo.calls


@pytest.mark.asyncio
async def test_update_lead_clear_stage_id():
    """update_lead allows clearing stage_id with explicit null (no stage validation)."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()
    lead_repo.get_lead_detail_by_id_result = {"id": LEAD_ID}
    lead_repo.update_lead_result = {"id": LEAD_ID}

    previous, updated = await service.update_lead(LEAD_ID, UpdateLeadRequest(stage_id=None))

    assert previous == lead_repo.get_lead_detail_by_id_result
    assert updated == lead_repo.update_lead_result
    assert "get_stage_by_id" not in stage_repo.calls
    update_data = lead_repo.calls["update_lead"][2]
    assert update_data["stage_id"] is None
    assert not client_repo.calls
    assert not user_repo.calls


@pytest.mark.asyncio
async def test_list_leads_list_mode():
    """list_leads in LIST mode returns flat paginated list with total count."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()
    lead_repo.count_leads_filtered_result = 12
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    lead_repo.list_leads_page_result = [
        {
            "id": LEAD_ID,
            "client_id": CLIENT_ID,
            "client_name": "Client Co",
            "name": "Lead A",
            "stage_id": STAGE_ID_1,
            "stage_name": "Qualified",
            "lead_score": "high",
            "close_date": date(2026, 1, 10),
            "amount": Decimal("50.00"),
            "owner_id": OWNER_ID,
            "owner_name": "Owner Name",
            "point_of_contact_id": POINT_OF_CONTACT_ID,
            "point_of_contact": "PoC Name",
            "created_at": now,
            "updated_at": now,
        }
    ]

    query = LeadsListQueryParams(
        mode=LeadsListMode.LIST,
        stage_id=STAGE_ID_1,
        search=" lead ",
        page=2,
        limit=10,
    )

    items, total, page = await service.list_leads(query)

    assert total == 12
    assert page == 2
    assert len(items) == 1
    assert items[0]["id"] == LEAD_ID
    assert items[0]["client_name"] == "Client Co"
    assert items[0]["stage_id"] == STAGE_ID_1
    assert items[0]["close_date"] == "2026-01-10"
    assert items[0]["created_at"] == now.isoformat()
    assert items[0]["updated_at"] == now.isoformat()
    assert lead_repo.calls["count_leads_filtered"] == (ORG_ID, STAGE_ID_1, "lead")
    assert lead_repo.calls["list_leads_page"] == (ORG_ID, STAGE_ID_1, "lead", 10, 10)
    assert not stage_repo.calls
    assert not client_repo.calls
    assert not user_repo.calls


@pytest.mark.asyncio
async def test_list_leads_kanban_groups():
    """list_leads in KANBAN mode returns stage groups + optional unassigned group."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()
    stage_repo.list_stages_by_organization_result = [
        {"id": STAGE_ID_1, "stage_name": "Qualified", "sort_order": 1},
        {"id": STAGE_ID_2, "stage_name": "Lost", "sort_order": 2},
    ]

    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    lead_repo.list_leads_for_kanban_result = [
        {
            "id": LEAD_ID,
            "client_id": CLIENT_ID,
            "client_name": "Client Co",
            "name": "Lead A",
            "stage_id": STAGE_ID_1,
            "stage_name": "Qualified",
            "lead_score": None,
            "close_date": None,
            "amount": None,
            "owner_id": None,
            "owner_name": None,
            "point_of_contact_id": None,
            "point_of_contact": None,
            "created_at": now,
            "updated_at": now,
        },
        {
            "id": "77777777-7777-7777-7777-777777777777",
            "client_id": "99999999-9999-9999-9999-999999999999",
            "client_name": "Client Unassigned",
            "name": "Lead B",
            "stage_id": None,
            "stage_name": None,
            "lead_score": None,
            "close_date": None,
            "amount": None,
            "owner_id": None,
            "owner_name": None,
            "point_of_contact_id": None,
            "point_of_contact": None,
            "created_at": now,
            "updated_at": now,
        },
    ]

    query = LeadsListQueryParams(mode=LeadsListMode.KANBAN, stage_id=None, search=None)
    groups = await service.list_leads(query)

    assert isinstance(groups, list)
    assert len(groups) == 3  # 2 stages + unassigned
    assert groups[0]["stage_id"] == STAGE_ID_1
    assert groups[0]["stage_name"] == "Qualified"
    assert groups[0]["total"] == 1
    assert len(groups[0]["leads"]) == 1
    assert groups[1]["stage_id"] == STAGE_ID_2
    assert groups[1]["total"] == 0
    assert groups[2]["stage_id"] is None
    assert groups[2]["stage_name"] == "Unassigned"
    assert groups[2]["total"] == 1
    assert groups[2]["sort_order"] == 3
    assert stage_repo.calls["list_stages_by_organization"] == ORG_ID
    assert lead_repo.calls["list_leads_for_kanban"] == (ORG_ID, None, None)
    assert not client_repo.calls
    assert not user_repo.calls


@pytest.mark.asyncio
async def test_get_lead_detail_custom_fields(monkeypatch):
    """get_lead returns LeadDetail JSON with resolved custom_fields (id-keyed read shape)."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()

    class _CFRepo:
        """Minimal fake custom field repository for lead detail read tests."""

        get_fields_result = [
            {
                "id": "foo",
                "field_name": "Foo",
                "field_key": "foo_label",
                "field_type": "text",
                "parent_id": None,
                "entity_type": "lead",
                "show_on_create": True,
                "show_on_detail": False,
                "is_required": False,
                "type_config": {},
                "sort_order": 0,
                "is_active": True,
            }
        ]

        async def get_custom_fields_by_entity_type(self, _organization_id, _entity_type):
            """Return canned field definitions (no DB)."""
            return self.get_fields_result

    monkeypatch.setattr(
        "apps.user_service.app.services.custom_field_service.CustomFieldRepository",
        lambda db_connection=None: _CFRepo(),
    )
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    lead_repo.get_lead_detail_by_id_result = {
        "id": LEAD_ID,
        "client_id": CLIENT_ID,
        "client_name": "Client Co",
        "name": "Lead A",
        "stage_id": STAGE_ID_1,
        "stage_name": "Qualified",
        "intake_stage": "Initial",
        "lead_source": "Referral",
        "referral_source": None,
        "lead_score": "high",
        "close_date": date(2026, 1, 10),
        "converted_at": datetime(2026, 1, 11, tzinfo=timezone.utc),
        "notes": "Some notes",
        "amount": Decimal("123.45"),
        "created_by": CTX_USER_ID,
        "description": "Opportunity desc",
        "owner_id": OWNER_ID,
        "owner_name": "Owner Name",
        "point_of_contact_id": POINT_OF_CONTACT_ID,
        "point_of_contact": "PoC Name",
        "custom_fields": ('[{"field_id":"foo","instance_id":"f1","type":"text","value":"bar"}]'),
        "created_at": now,
        "updated_at": now,
    }

    detail = await service.get_lead(LEAD_ID)

    assert detail["id"] == LEAD_ID
    assert detail["client_name"] == "Client Co"
    assert detail["stage_name"] == "Qualified"
    assert detail["custom_fields"] == [
        {
            "field_id": "foo",
            "field_key": "foo_label",
            "label": "Foo",
            "instance_id": "f1",
            "type": "text",
            "value": "bar",
        }
    ]
    assert detail["created_at"] == now.isoformat()
    assert detail["updated_at"] == now.isoformat()
    assert detail["converted_at"].startswith("2026-01-11T")
    assert not stage_repo.calls
    assert not client_repo.calls
    assert not user_repo.calls


@pytest.mark.asyncio
async def test_get_lead_missing_raises():
    """get_lead raises NotFoundException when repository returns None."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()
    lead_repo.get_lead_detail_by_id_result = None

    with pytest.raises(NotFoundException) as exc_info:
        await service.get_lead(LEAD_ID)

    assert exc_info.value.message_key == "leads.errors.not_found"
    assert not stage_repo.calls
    assert not client_repo.calls
    assert not user_repo.calls


@pytest.mark.asyncio
async def test_delete_lead_returns_or_raises():
    """delete_lead returns deleted row and raises when missing."""
    service, lead_repo, stage_repo, client_repo, user_repo = _service_with_fakes()
    lead_repo.delete_lead_result = {"id": LEAD_ID}

    deleted = await service.delete_lead(LEAD_ID)
    assert deleted["id"] == LEAD_ID
    assert lead_repo.calls["delete_lead"] == (ORG_ID, LEAD_ID)

    lead_repo.delete_lead_result = None
    with pytest.raises(NotFoundException) as exc_info:
        await service.delete_lead(LEAD_ID)

    assert exc_info.value.message_key == "leads.errors.not_found"
    assert not stage_repo.calls
    assert not client_repo.calls
    assert not user_repo.calls
