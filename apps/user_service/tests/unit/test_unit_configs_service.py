"""Unit tests for UnitConfigsService kind-specific validation and step mapping."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.user_service.app.schemas.enums import ProjectSetupStep, UnitConfigKind
from apps.user_service.app.schemas.project_inventory import CreateUnitConfigRequest
from apps.user_service.app.services.unit_configs_service import UnitConfigsService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ValidationException


def _user_context() -> UserContext:
    """Build a minimal UserContext for service tests."""
    return UserContext(user_id="user-1", email="owner@example.com", organization_id="org-1")


def _service() -> UnitConfigsService:
    """Build UnitConfigsService with mocked repo and setup service."""
    svc = UnitConfigsService(db_connection=MagicMock(), user_context=_user_context())
    svc.configs_repo = MagicMock()
    svc.configs_repo.insert_config = AsyncMock(return_value={"id": "c1"})
    svc.setup_service = MagicMock()
    svc.setup_service.ensure_project = AsyncMock(return_value={"id": "p1"})
    svc.setup_service.complete_step = AsyncMock(
        return_value={"step_key": "x", "status": "completed"}
    )
    return svc


@pytest.mark.asyncio
async def test_create_apartment_config_requires_core_fields():
    """Apartment config without bedrooms/bathrooms/area is rejected."""
    svc = _service()
    body = CreateUnitConfigRequest(
        config_kind=UnitConfigKind.APARTMENT,
        name="2BHK",
        code="2BHK",
    )

    with pytest.raises(ValidationException):
        await svc.create_config(project_id="p1", body=body)


@pytest.mark.asyncio
async def test_create_apartment_config_succeeds_with_fields():
    """Apartment config with required fields inserts successfully."""
    svc = _service()
    body = CreateUnitConfigRequest(
        config_kind=UnitConfigKind.APARTMENT,
        name="2BHK",
        code="2BHK",
        bedrooms=2,
        bathrooms=2,
        area_sqft=1200,
    )

    result = await svc.create_config(project_id="p1", body=body)

    assert result["id"] == "c1"
    svc.configs_repo.insert_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_plot_config_requires_plot_type():
    """Plot config without plot_type is rejected."""
    svc = _service()
    body = CreateUnitConfigRequest(
        config_kind=UnitConfigKind.PLOT,
        name="Plot A",
        code="PA",
    )

    with pytest.raises(ValidationException):
        await svc.create_config(project_id="p1", body=body)


@pytest.mark.asyncio
async def test_complete_config_step_maps_kind_to_step():
    """Completing a config step maps the kind to the right wizard step."""
    svc = _service()

    await svc.complete_config_step(project_id="p1", config_kind=UnitConfigKind.COMMERCIAL.value)

    svc.setup_service.complete_step.assert_awaited_once()
    _, kwargs = svc.setup_service.complete_step.await_args
    assert kwargs["step_key"] == ProjectSetupStep.COMMERCIAL_CONFIG.value


@pytest.mark.asyncio
async def test_complete_config_step_rejects_invalid_kind():
    """An invalid config kind raises ValidationException."""
    svc = _service()

    with pytest.raises(ValidationException):
        await svc.complete_config_step(project_id="p1", config_kind="invalid")
