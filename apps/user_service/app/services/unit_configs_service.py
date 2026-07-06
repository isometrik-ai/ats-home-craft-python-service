"""Unit configs service: apartment/commercial/plot configs, items, media, steps."""

from __future__ import annotations

from typing import Any

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.unit_configs_repository import (
    UnitConfigsRepository,
)
from apps.user_service.app.schemas.enums import ProjectSetupStep, UnitConfigKind
from apps.user_service.app.schemas.project_inventory import (
    ConfigMediaRequest,
    CreatePlotConfigItemRequest,
    CreateUnitConfigRequest,
    UpdateUnitConfigRequest,
)
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from apps.user_service.app.utils.project_serialization import serialize_row
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode

# Which wizard step a given config kind belongs to.
_KIND_TO_STEP: dict[str, str] = {
    UnitConfigKind.APARTMENT.value: ProjectSetupStep.APARTMENT_CONFIG.value,
    UnitConfigKind.COMMERCIAL.value: ProjectSetupStep.COMMERCIAL_CONFIG.value,
    UnitConfigKind.PLOT.value: ProjectSetupStep.PLOT_CONFIG.value,
}

# Required fields per config kind (mirrors DB check constraints).
_REQUIRED_BY_KIND: dict[str, tuple[str, ...]] = {
    UnitConfigKind.APARTMENT.value: ("bedrooms", "bathrooms", "area_sqft"),
    UnitConfigKind.COMMERCIAL.value: (
        "commercial_unit_type",
        "carpet_area_sqft",
        "power_load_kw",
    ),
    UnitConfigKind.PLOT.value: ("plot_type",),
}


class UnitConfigsService:
    """Business logic for the apartment/commercial/plot config steps."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.configs_repo = UnitConfigsRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    @staticmethod
    def _validate_kind_fields(config_kind: str, data: dict[str, Any]) -> None:
        """Ensure kind-specific required fields are present."""
        required = _REQUIRED_BY_KIND.get(config_kind, ())
        missing = [field for field in required if data.get(field) is None]
        if missing:
            raise ValidationException(
                message_key="project_setup.errors.missing_required_fields",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    async def _ensure_config(self, *, project_id: str, config_id: str) -> dict[str, Any]:
        """Return the config row scoped to org + project or raise 404."""
        await self.setup_service.ensure_project(project_id=project_id)
        config = await self.configs_repo.get_config(
            organization_id=self._org_id, project_id=project_id, config_id=config_id
        )
        if not config:
            raise NotFoundException(
                message_key="project_setup.errors.config_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return config

    async def create_config(
        self, *, project_id: str, body: CreateUnitConfigRequest
    ) -> dict[str, Any]:
        """Create a unit configuration after validating kind-specific fields."""
        await self.setup_service.ensure_project(project_id=project_id)
        data = body.model_dump()
        config_kind = body.config_kind.value
        self._validate_kind_fields(config_kind, data)
        data["config_kind"] = config_kind
        for enum_field in ("default_facing", "commercial_unit_type", "plot_type", "facing"):
            value = getattr(body, enum_field)
            data[enum_field] = value.value if value is not None else None
        data["organization_id"] = self._org_id
        data["project_id"] = project_id
        try:
            inserted = await self.configs_repo.insert_config(data)
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return serialize_row(inserted)

    async def list_configs(
        self, *, project_id: str, config_kind: str | None
    ) -> list[dict[str, Any]]:
        """List configs for a project, optionally filtered by kind."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.configs_repo.list_configs(
            organization_id=self._org_id,
            project_id=project_id,
            config_kind=config_kind,
        )
        return [serialize_row(row) for row in rows]

    async def update_config(
        self, *, project_id: str, config_id: str, body: UpdateUnitConfigRequest
    ) -> dict[str, Any]:
        """Patch a config, re-validating kind-specific required fields."""
        current = await self._ensure_config(project_id=project_id, config_id=config_id)
        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        for enum_field in ("default_facing", "commercial_unit_type", "plot_type", "facing"):
            value = getattr(body, enum_field)
            if enum_field in patch and value is not None:
                patch[enum_field] = value.value
        merged = {**current, **patch}
        self._validate_kind_fields(str(current["config_kind"]), merged)
        try:
            updated = await self.configs_repo.update_config(
                organization_id=self._org_id,
                project_id=project_id,
                config_id=config_id,
                update_data=patch,
            )
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return serialize_row(updated or {})

    async def delete_config(self, *, project_id: str, config_id: str) -> dict[str, Any]:
        """Delete a config."""
        current = await self._ensure_config(project_id=project_id, config_id=config_id)
        await self.configs_repo.delete_config(
            organization_id=self._org_id, project_id=project_id, config_id=config_id
        )
        return {"old_data": serialize_row(current), "new_data": None}

    # -- plot items ---------------------------------------------------------

    async def create_plot_item(
        self, *, project_id: str, config_id: str, body: CreatePlotConfigItemRequest
    ) -> dict[str, Any]:
        """Create a plot item under a plot config."""
        config = await self._ensure_config(project_id=project_id, config_id=config_id)
        if str(config["config_kind"]) != UnitConfigKind.PLOT.value:
            raise ValidationException(
                message_key="project_setup.errors.config_kind_mismatch",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        data = body.model_dump()
        data["status"] = body.status.value
        data["organization_id"] = self._org_id
        data["config_id"] = config_id
        try:
            inserted = await self.configs_repo.insert_plot_item(data)
        except UniqueViolationError as exc:
            raise ConflictException(
                message_key="project_setup.errors.duplicate_code",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        return serialize_row(inserted)

    async def list_plot_items(self, *, project_id: str, config_id: str) -> list[dict[str, Any]]:
        """List plot items for a plot config."""
        await self._ensure_config(project_id=project_id, config_id=config_id)
        rows = await self.configs_repo.list_plot_items(
            organization_id=self._org_id, config_id=config_id
        )
        return [serialize_row(row) for row in rows]

    async def delete_plot_item(
        self, *, project_id: str, config_id: str, item_id: str
    ) -> dict[str, Any]:
        """Delete a plot item."""
        await self._ensure_config(project_id=project_id, config_id=config_id)
        deleted = await self.configs_repo.delete_plot_item(
            organization_id=self._org_id, config_id=config_id, item_id=item_id
        )
        if not deleted:
            raise NotFoundException(
                message_key="project_setup.errors.plot_item_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {"old_data": {"id": item_id}, "new_data": None}

    # -- config media -------------------------------------------------------

    async def add_media(
        self, *, project_id: str, config_id: str, body: ConfigMediaRequest
    ) -> dict[str, Any]:
        """Attach media metadata to a config."""
        await self._ensure_config(project_id=project_id, config_id=config_id)
        inserted = await self.configs_repo.insert_media(
            {
                "organization_id": self._org_id,
                "config_id": config_id,
                "kind": body.kind.value,
                "path": body.path,
                "mime": body.mime,
                "size_bytes": body.size_bytes,
                "original_name": body.original_name,
                "sort_order": body.sort_order,
                "uploaded_by": self.user_context.user_id,
            }
        )
        return serialize_row(inserted)

    async def list_media(self, *, project_id: str, config_id: str) -> list[dict[str, Any]]:
        """List media for a config."""
        await self._ensure_config(project_id=project_id, config_id=config_id)
        rows = await self.configs_repo.list_media(organization_id=self._org_id, config_id=config_id)
        return [serialize_row(row) for row in rows]

    async def delete_media(
        self, *, project_id: str, config_id: str, media_id: str
    ) -> dict[str, Any]:
        """Delete config media."""
        await self._ensure_config(project_id=project_id, config_id=config_id)
        deleted = await self.configs_repo.delete_media(
            organization_id=self._org_id, config_id=config_id, media_id=media_id
        )
        if not deleted:
            raise NotFoundException(
                message_key="project_setup.errors.config_media_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {"old_data": {"id": media_id}, "new_data": None}

    # -- step ---------------------------------------------------------------

    async def complete_config_step(self, *, project_id: str, config_kind: str) -> dict[str, Any]:
        """Complete the wizard step matching a config kind."""
        try:
            UnitConfigKind(config_kind)
        except ValueError as exc:
            raise ValidationException(
                message_key="project_setup.errors.config_kind_mismatch",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            ) from exc
        step_key = _KIND_TO_STEP[config_kind]
        return await self.setup_service.complete_step(project_id=project_id, step_key=step_key)
