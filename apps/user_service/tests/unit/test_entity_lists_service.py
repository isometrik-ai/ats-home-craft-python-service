"""Unit tests for EntityListsService."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from asyncpg import UniqueViolationError

from apps.user_service.app.schemas.entity_lists import (
    CreateEntityListRequest,
    UpdateEntityListRequest,
)
from apps.user_service.app.schemas.enums import EntityListStatus, EntityType
from apps.user_service.app.services.entity_lists_service import EntityListsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.common_query import (
    CONTACTS_MANAGEMENT_VIEW,
    LEADS_MANAGEMENT_EDIT,
)
from libs.shared_utils.http_exceptions import (
    DuplicateValueException,
    NotFoundException,
    ValidationException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
LIST_ID = "660e8400-e29b-41d4-a716-446655440001"


class _FakeEntityListsRepo:
    """Configurable fake EntityListsRepository."""

    def __init__(
        self,
        *,
        list_row: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        updated: dict[str, Any] | None = None,
        lists: list[dict[str, Any]] | None = None,
        member_ids: list[str] | None = None,
        create_error: Exception | None = None,
        update_error: Exception | None = None,
    ) -> None:
        self.list_row = list_row
        self.details = details
        self.updated = updated
        self.lists = lists or []
        self.total = len(self.lists)
        self.member_ids = member_ids or []
        self.create_error = create_error
        self.update_error = update_error
        self.last_create_kwargs: dict[str, Any] | None = None
        self.last_update_kwargs: dict[str, Any] | None = None

    async def create_list(self, **kwargs):
        """Return configured create payload or raise."""
        self.last_create_kwargs = kwargs
        if self.create_error:
            raise self.create_error
        return {"list": {"id": LIST_ID, **kwargs}, "members": None}

    async def get_list(self, **kwargs):
        """Return configured list row."""
        del kwargs
        return self.list_row

    async def get_list_details(self, **kwargs):
        """Return configured details row."""
        del kwargs
        return self.details

    async def update_list(self, **kwargs):
        """Return configured updated row or raise."""
        self.last_update_kwargs = kwargs
        if self.update_error:
            raise self.update_error
        return self.updated

    async def list_lists_with_counts_for_entity_type(self, **kwargs):
        """Return configured list index rows."""
        del kwargs
        return self.lists, self.total

    async def list_member_ids(self, **kwargs):
        """Return configured member ids."""
        del kwargs
        return self.member_ids, len(self.member_ids)


def _service(repo: _FakeEntityListsRepo) -> EntityListsService:
    """Build EntityListsService with fake repository."""
    service = EntityListsService(db_connection=MagicMock(), organization_id=ORG_ID)
    service.repo = repo
    return service


def test_get_permission_code_for_lead_actions():
    """Permission map resolves entity type and action."""
    assert (
        EntityListsService.get_permission_code(entity_type=EntityType.LEAD, action="edit")
        == LEADS_MANAGEMENT_EDIT
    )
    assert (
        EntityListsService.get_permission_code(entity_type=EntityType.CONTACT, action="view")
        == CONTACTS_MANAGEMENT_VIEW
    )


def test_normalize_entity_ids():
    """Entity ids are stripped and blank values removed."""
    assert EntityListsService._normalize_entity_ids([" a ", "", "b"]) == ["a", "b"]


@pytest.mark.asyncio
async def test_create_list_success():
    """Create list delegates to repository."""
    repo = _FakeEntityListsRepo()
    service = _service(repo)
    body = CreateEntityListRequest(name="VIP", entity_type=EntityType.CONTACT, tags=[" hot "])

    result = await service.create_list(body)

    assert result["list"]["id"] == LIST_ID
    assert repo.last_create_kwargs["name"] == "VIP"
    assert repo.last_create_kwargs["tags"] == ["hot"]


@pytest.mark.asyncio
async def test_create_list_duplicate_name():
    """Unique violation becomes DuplicateValueException."""
    repo = _FakeEntityListsRepo(create_error=UniqueViolationError("duplicate"))
    service = _service(repo)

    with pytest.raises(DuplicateValueException) as exc_info:
        await service.create_list(
            CreateEntityListRequest(name="VIP", entity_type=EntityType.CONTACT)
        )
    assert exc_info.value.message_key == "entity_lists.errors.name_already_exists"


@pytest.mark.asyncio
async def test_get_list_details_not_found():
    """Missing list raises NotFoundException."""
    service = _service(_FakeEntityListsRepo(details=None))

    with pytest.raises(NotFoundException):
        await service.get_list_details(list_id=LIST_ID)


@pytest.mark.asyncio
async def test_update_list_rejects_deleted_status():
    """Cannot set status to deleted via update endpoint."""
    repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "status": EntityListStatus.ACTIVE.value},
    )
    service = _service(repo)

    with pytest.raises(ValidationException) as exc_info:
        await service.update_list(
            list_id=LIST_ID,
            body=UpdateEntityListRequest(status=EntityListStatus.DELETED),
        )
    assert exc_info.value.message_key == "entity_lists.errors.cannot_modify_deleted_list"


@pytest.mark.asyncio
async def test_update_list_empty_payload():
    """Empty update payload is rejected."""
    repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "status": EntityListStatus.ACTIVE.value},
    )
    service = _service(repo)

    with pytest.raises(ValidationException) as exc_info:
        await service.update_list(list_id=LIST_ID, body=UpdateEntityListRequest())
    assert exc_info.value.message_key == "entity_lists.errors.empty_update_payload"


@pytest.mark.asyncio
async def test_update_list_too_many_member_ids():
    """Bulk membership updates enforce max size."""
    repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "status": EntityListStatus.ACTIVE.value},
    )
    service = _service(repo)
    too_many = [f"id-{idx}" for idx in range(service.BULK_MEMBER_IDS_MAX + 1)]
    update_data, add_ids, remove_ids = service._build_update_data(
        body=UpdateEntityListRequest(name="Renamed")
    )
    add_ids = too_many

    with pytest.raises(ValidationException) as exc_info:
        service._validate_update_payload(
            body=UpdateEntityListRequest(name="Renamed"),
            update_data=update_data,
            add_ids=add_ids,
            remove_ids=remove_ids,
        )
    assert exc_info.value.message_key == "entity_lists.errors.too_many_member_ids"


@pytest.mark.asyncio
async def test_soft_delete_is_idempotent_for_deleted_list():
    """Soft delete no-ops when list already deleted."""
    repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "status": EntityListStatus.DELETED.value},
    )
    service = _service(repo)

    await service.soft_delete(list_id=LIST_ID)

    assert repo.last_update_kwargs is None


@pytest.mark.asyncio
async def test_list_member_ids_wraps_entity_ids():
    """Member ids are returned as entity_id objects."""
    repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "status": EntityListStatus.ACTIVE.value},
        member_ids=["e1", "e2"],
    )
    service = _service(repo)

    rows, total = await service.list_member_ids(list_id=LIST_ID, limit=10, offset=0)

    assert rows == [{"entity_id": "e1"}, {"entity_id": "e2"}]
    assert total == 2


@pytest.mark.asyncio
async def test_require_list_permission():
    """Helper loads list, resolves entity type, and checks permission."""
    fake_repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "entity_type": EntityType.LEAD.value},
    )
    user_context = UserContext(user_id="u1", email="u@example.com", organization_id=ORG_ID)

    with (
        patch(
            "apps.user_service.app.services.entity_lists_service.extract_user_context",
            new=AsyncMock(return_value=user_context),
        ),
        patch(
            "apps.user_service.app.services.entity_lists_service.EntityListsRepository",
            return_value=fake_repo,
        ),
        patch(
            "apps.user_service.app.services.entity_lists_service.require_permission",
            new=AsyncMock(),
        ) as mock_require,
    ):
        ctx, entity_type = await EntityListsService.require_list_permission(
            current_user={"sub": "u1"},
            db_connection=MagicMock(),
            list_id=LIST_ID,
            action="edit",
        )

    assert ctx.organization_id == ORG_ID
    assert entity_type == EntityType.LEAD
    mock_require.assert_awaited_once()


def test_get_permission_code_company_and_project_actions():
    """Permission map covers company/project and normalizes unknown actions to view."""
    from libs.shared_utils.common_query import (
        COMPANIES_MANAGEMENT_CREATE,
        PROJECTS_MANAGEMENT_VIEW,
    )

    assert (
        EntityListsService.get_permission_code(entity_type=EntityType.COMPANY, action="create")
        == COMPANIES_MANAGEMENT_CREATE
    )
    assert (
        EntityListsService.get_permission_code(entity_type=EntityType.PROJECT, action="archive")
        == PROJECTS_MANAGEMENT_VIEW
    )


def test_build_update_data_includes_metadata_fields():
    """Update builder maps description, tags, and active status."""
    service = _service(_FakeEntityListsRepo())
    update_data, add_ids, remove_ids = service._build_update_data(
        body=UpdateEntityListRequest(
            name=" Renamed ",
            description="Updated",
            tags=[" one ", ""],
            status=EntityListStatus.INACTIVE,
            add_ids=[" a "],
            remove_ids=[" b "],
        )
    )
    assert update_data["name"] == "Renamed"
    assert update_data["description"] == "Updated"
    assert update_data["tags"] == ["one"]
    assert update_data["status"] == EntityListStatus.INACTIVE.value
    assert add_ids == ["a"]
    assert remove_ids == ["b"]


def test_validate_update_payload_rejects_too_many_remove_ids():
    """Bulk remove ids enforce the same max size as add ids."""
    service = _service(_FakeEntityListsRepo())
    too_many = [f"id-{idx}" for idx in range(service.BULK_MEMBER_IDS_MAX + 1)]
    update_data, add_ids, remove_ids = service._build_update_data(
        body=UpdateEntityListRequest(name="Renamed")
    )
    remove_ids = too_many

    with pytest.raises(ValidationException) as exc_info:
        service._validate_update_payload(
            body=UpdateEntityListRequest(name="Renamed"),
            update_data=update_data,
            add_ids=add_ids,
            remove_ids=remove_ids,
        )
    assert exc_info.value.message_key == "entity_lists.errors.too_many_member_ids"


@pytest.mark.asyncio
async def test_require_list_permission_list_not_found():
    """Helper raises when list id does not exist."""
    user_context = UserContext(user_id="u1", email="u@example.com", organization_id=ORG_ID)
    fake_repo = _FakeEntityListsRepo(list_row=None)

    with (
        patch(
            "apps.user_service.app.services.entity_lists_service.extract_user_context",
            new=AsyncMock(return_value=user_context),
        ),
        patch(
            "apps.user_service.app.services.entity_lists_service.EntityListsRepository",
            return_value=fake_repo,
        ),
        pytest.raises(NotFoundException),
    ):
        await EntityListsService.require_list_permission(
            current_user={"sub": "u1"},
            db_connection=MagicMock(),
            list_id=LIST_ID,
            action="view",
        )


@pytest.mark.asyncio
async def test_get_list_details_success():
    """Existing list details are returned from repository."""
    details = {"id": LIST_ID, "name": "VIP", "member_count": 2}
    service = _service(_FakeEntityListsRepo(details=details))

    row = await service.get_list_details(list_id=LIST_ID)

    assert row == details


@pytest.mark.asyncio
async def test_update_list_rejects_already_deleted_list():
    """Cannot update a list that is already soft-deleted."""
    repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "status": EntityListStatus.DELETED.value},
    )
    service = _service(repo)

    with pytest.raises(ValidationException) as exc_info:
        await service.update_list(
            list_id=LIST_ID,
            body=UpdateEntityListRequest(name="Renamed"),
        )
    assert exc_info.value.message_key == "entity_lists.errors.cannot_modify_deleted_list"


@pytest.mark.asyncio
async def test_update_list_success():
    """Successful update delegates normalized payload to repository."""
    repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "status": EntityListStatus.ACTIVE.value},
        updated={"id": LIST_ID, "name": "Renamed"},
    )
    service = _service(repo)

    updated = await service.update_list(
        list_id=LIST_ID,
        body=UpdateEntityListRequest(name="Renamed", add_ids=["e1"]),
    )

    assert updated["name"] == "Renamed"
    assert repo.last_update_kwargs["update_data"]["add_entity_ids"] == ["e1"]


@pytest.mark.asyncio
async def test_update_list_duplicate_name():
    """Unique violation on update becomes DuplicateValueException."""
    repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "status": EntityListStatus.ACTIVE.value},
        update_error=UniqueViolationError("duplicate"),
    )
    service = _service(repo)

    with pytest.raises(DuplicateValueException):
        await service.update_list(
            list_id=LIST_ID,
            body=UpdateEntityListRequest(name="Renamed"),
        )


@pytest.mark.asyncio
async def test_update_list_not_found_after_repo_update():
    """Update raises when repository returns no row."""
    repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "status": EntityListStatus.ACTIVE.value},
        updated=None,
    )
    service = _service(repo)

    with pytest.raises(NotFoundException):
        await service.update_list(
            list_id=LIST_ID,
            body=UpdateEntityListRequest(name="Renamed"),
        )


@pytest.mark.asyncio
async def test_soft_delete_active_list():
    """Soft delete marks an active list as deleted."""
    repo = _FakeEntityListsRepo(
        list_row={"id": LIST_ID, "status": EntityListStatus.ACTIVE.value},
        updated={"id": LIST_ID, "status": EntityListStatus.DELETED.value},
    )
    service = _service(repo)

    await service.soft_delete(list_id=LIST_ID)

    assert repo.last_update_kwargs["update_data"]["status"] == EntityListStatus.DELETED.value


@pytest.mark.asyncio
async def test_list_lists_delegates_to_repository():
    """List index delegates to repository with entity type filter."""
    rows = [{"id": LIST_ID, "name": "VIP"}]
    repo = _FakeEntityListsRepo(lists=rows)
    service = _service(repo)

    result, total = await service.list_lists(
        entity_type=EntityType.CONTACT,
        status=EntityListStatus.ACTIVE,
        search="VIP",
        limit=10,
        offset=0,
    )

    assert result == rows
    assert total == 1


@pytest.mark.asyncio
async def test_list_member_ids_requires_existing_list():
    """Listing members raises when list does not exist."""
    service = _service(_FakeEntityListsRepo(list_row=None))

    with pytest.raises(NotFoundException):
        await service.list_member_ids(list_id=LIST_ID, limit=10, offset=0)
