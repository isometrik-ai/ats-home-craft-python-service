"""Service layer for entity lists and memberships."""

from __future__ import annotations

from typing import Any, assert_never

from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.entity_lists_repository import (
    EntityListsRepository,
)
from apps.user_service.app.schemas.entity_lists import (
    CreateEntityListRequest,
    UpdateEntityListRequest,
)
from apps.user_service.app.schemas.enums import EntityListStatus, EntityType
from apps.user_service.app.utils.common_utils import (
    extract_user_context,
    require_permission,
)
from libs.shared_utils.common_query import (
    COMPANIES_MANAGEMENT_CREATE,
    COMPANIES_MANAGEMENT_DELETE,
    COMPANIES_MANAGEMENT_EDIT,
    COMPANIES_MANAGEMENT_VIEW,
    CONTACTS_MANAGEMENT_CREATE,
    CONTACTS_MANAGEMENT_DELETE,
    CONTACTS_MANAGEMENT_EDIT,
    CONTACTS_MANAGEMENT_VIEW,
    LEADS_MANAGEMENT_CREATE,
    LEADS_MANAGEMENT_DELETE,
    LEADS_MANAGEMENT_EDIT,
    LEADS_MANAGEMENT_VIEW,
    PROJECTS_MANAGEMENT_CREATE,
    PROJECTS_MANAGEMENT_DELETE,
    PROJECTS_MANAGEMENT_EDIT,
    PROJECTS_MANAGEMENT_VIEW,
)
from libs.shared_utils.http_exceptions import (
    DuplicateValueException,
    NotFoundException,
    ValidationException,
)


class EntityListsService:
    """Business logic for list operations.

    This service enforces:
    - organization scoping
    - list status rules (e.g. cannot mutate deleted lists)
    - entity existence validation for bulk membership operations
    """

    def __init__(self, *, db_connection: Any, organization_id: str) -> None:
        """Initialize the service for a specific organization."""
        self.organization_id = organization_id
        self.repo = EntityListsRepository(db_connection=db_connection)

    BULK_MEMBER_IDS_MAX = 1000

    @staticmethod
    def _normalize_entity_ids(values: list[Any] | None) -> list[str]:
        """Normalize the entity IDs."""
        return [str(v).strip() for v in (values or []) if str(v).strip()]

    def _build_update_data(
        self, *, body: UpdateEntityListRequest
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        """Build the update data for the list."""
        update_data: dict[str, Any] = {}
        add_ids = self._normalize_entity_ids(body.add_ids)
        remove_ids = self._normalize_entity_ids(body.remove_ids)

        if body.name is not None:
            update_data["name"] = body.name.strip()
        if body.description is not None:
            update_data["description"] = body.description
        if body.tags is not None:
            update_data["tags"] = [t.strip() for t in body.tags if t and t.strip()]
        if body.status is not None:
            if body.status == EntityListStatus.DELETED:
                raise ValidationException(
                    message_key="entity_lists.errors.cannot_modify_deleted_list"
                )
            update_data["status"] = body.status.value

        update_data["add_entity_ids"] = add_ids
        update_data["remove_entity_ids"] = remove_ids
        return update_data, add_ids, remove_ids

    def _validate_update_payload(
        self,
        *,
        body: UpdateEntityListRequest,
        update_data: dict[str, Any],
        add_ids: list[str],
        remove_ids: list[str],
    ) -> None:
        """Validate the update payload."""
        if all(
            (
                not update_data.get("name"),
                body.description is None,
                body.tags is None,
                body.status is None,
                not add_ids,
                not remove_ids,
            )
        ):
            raise ValidationException(message_key="entity_lists.errors.empty_update_payload")

        if len(add_ids) > self.BULK_MEMBER_IDS_MAX or len(remove_ids) > self.BULK_MEMBER_IDS_MAX:
            raise ValidationException(message_key="entity_lists.errors.too_many_member_ids")

    @staticmethod
    async def require_list_permission(
        *,
        current_user: dict,
        db_connection: Any,
        list_id: str,
        action: str,
    ) -> tuple[Any, EntityType]:
        """Load a list, infer entity type, and enforce the correct permission.

        This helper is used by endpoints that only have `list_id` and therefore need
        to infer the list `entity_type` before checking permissions.

        Args:
            current_user: JWT claims extracted by auth middleware.
            db_connection: Request-scoped asyncpg connection.
            list_id: List UUID.
            action: One of `view`, `create`, `edit`, `delete`.

        Returns:
            Tuple of `(user_context, entity_type)` when authorized.
        """
        user_context = await extract_user_context(current_user, db_connection)
        repository = EntityListsRepository(db_connection=db_connection)
        list_row = await repository.get_list(
            organization_id=user_context.organization_id,
            list_id=list_id,
        )
        if not list_row:
            raise NotFoundException(message_key="entity_lists.errors.list_not_found")

        entity_type = EntityType(str(list_row.get("entity_type")))
        await require_permission(
            permission_code=EntityListsService.get_permission_code(
                entity_type=entity_type,
                action=action,
            ),
            user_context=user_context,
            db_connection=db_connection,
            organization_id=user_context.organization_id,
        )
        return user_context, entity_type

    @staticmethod
    def get_permission_code(*, entity_type: EntityType, action: str) -> str:
        """Return the permission code for a list action.

        Args:
            entity_type: The list entity type.
            action: One of `create`, `view`, `edit`, `delete`.

        Returns:
            Permission code string used by `require_permission`.
        """
        normalized_action = action if action in {"create", "edit", "delete"} else "view"

        permission_map: dict[EntityType, dict[str, str]] = {
            EntityType.LEAD: {
                "create": LEADS_MANAGEMENT_CREATE,
                "edit": LEADS_MANAGEMENT_EDIT,
                "delete": LEADS_MANAGEMENT_DELETE,
                "view": LEADS_MANAGEMENT_VIEW,
            },
            EntityType.CONTACT: {
                "create": CONTACTS_MANAGEMENT_CREATE,
                "edit": CONTACTS_MANAGEMENT_EDIT,
                "delete": CONTACTS_MANAGEMENT_DELETE,
                "view": CONTACTS_MANAGEMENT_VIEW,
            },
            EntityType.COMPANY: {
                "create": COMPANIES_MANAGEMENT_CREATE,
                "edit": COMPANIES_MANAGEMENT_EDIT,
                "delete": COMPANIES_MANAGEMENT_DELETE,
                "view": COMPANIES_MANAGEMENT_VIEW,
            },
            EntityType.PROJECT: {
                "create": PROJECTS_MANAGEMENT_CREATE,
                "edit": PROJECTS_MANAGEMENT_EDIT,
                "delete": PROJECTS_MANAGEMENT_DELETE,
                "view": PROJECTS_MANAGEMENT_VIEW,
            },
        }

        try:
            return permission_map[entity_type][normalized_action]
        except KeyError:
            assert_never(entity_type)

    async def create_list(self, body: CreateEntityListRequest) -> dict[str, Any]:
        """Create a list and optionally add initial members.

        Returns:
            Dictionary with:
            - `list`: created list row
            - `members`: optional bulk membership result when `body.ids` is provided
        """
        try:
            created = await self.repo.create_list(
                organization_id=self.organization_id,
                name=body.name.strip(),
                entity_type=body.entity_type,
                description=(body.description or None),
                tags=[t.strip() for t in (body.tags or []) if t and t.strip()],
                entity_ids=body.ids,
            )
            return {
                "list": created.get("list"),
                "members": created.get("members"),
            }
        except UniqueViolationError as exc:
            raise DuplicateValueException(
                message_key="entity_lists.errors.name_already_exists",
            ) from exc

    async def get_list_details(self, *, list_id: str) -> dict[str, Any]:
        """Return list details with derived counters."""
        row = await self.repo.get_list_details(
            organization_id=self.organization_id,
            list_id=list_id,
        )
        if not row:
            raise NotFoundException(message_key="entity_lists.errors.list_not_found")
        return row

    async def update_list(self, *, list_id: str, body: UpdateEntityListRequest) -> dict[str, Any]:
        """Update list metadata and membership and return the updated row."""
        existing = await self._require_list(list_id=list_id)
        if existing.get("status") == EntityListStatus.DELETED.value:
            raise ValidationException(message_key="entity_lists.errors.cannot_modify_deleted_list")

        update_data, add_ids, remove_ids = self._build_update_data(body=body)
        self._validate_update_payload(
            body=body,
            update_data=update_data,
            add_ids=add_ids,
            remove_ids=remove_ids,
        )

        try:
            updated = await self.repo.update_list(
                organization_id=self.organization_id,
                list_id=list_id,
                update_data=update_data,
            )
        except UniqueViolationError as exc:
            raise DuplicateValueException(
                message_key="entity_lists.errors.name_already_exists"
            ) from exc

        if not updated:
            raise NotFoundException(message_key="entity_lists.errors.list_not_found")
        return updated

    async def soft_delete(self, *, list_id: str) -> None:
        """Soft delete a list by setting its status to deleted."""
        existing = await self._require_list(list_id=list_id)
        if existing.get("status") == EntityListStatus.DELETED.value:
            return
        await self.repo.update_list(
            organization_id=self.organization_id,
            list_id=list_id,
            update_data={"status": EntityListStatus.DELETED.value},
        )

    async def list_lists(
        self,
        *,
        entity_type: EntityType,
        status: EntityListStatus | None,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List lists for an entity type with derived counters."""
        return await self.repo.list_lists_with_counts_for_entity_type(
            organization_id=self.organization_id,
            entity_type=entity_type,
            status=status,
            search=search,
            limit=limit,
            offset=offset,
        )

    async def list_member_ids(
        self,
        *,
        list_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List member entity IDs for a list."""
        await self._require_list(list_id=list_id)
        entity_ids, total = await self.repo.list_member_ids(
            list_id=list_id,
            limit=limit,
            offset=offset,
        )
        return [{"entity_id": entity_id} for entity_id in entity_ids], total

    async def _require_list(self, *, list_id: str) -> dict[str, Any]:
        """Load a list or raise NotFoundException."""
        row = await self.repo.get_list(organization_id=self.organization_id, list_id=list_id)
        if not row:
            raise NotFoundException(message_key="entity_lists.errors.list_not_found")
        return row
