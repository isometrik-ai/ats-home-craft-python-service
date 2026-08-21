"""Unit tests for move events admin API route handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api.move_events import (
    _staff_move_event_access,
    create_move_event,
    delete_move_event,
)
from apps.user_service.app.schemas.enums import MoveEventType
from apps.user_service.app.schemas.move_events import (
    CreateMoveEventRequest,
    MoveEventResponse,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
MOVE_EVENT_ID = "22222222-2222-2222-2222-222222222222"
UNIT_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def _skip_audit_logging():
    with patch(
        "apps.user_service.app.dependencies.audit_logs.audit_decorator._log_audit_event",
        new_callable=AsyncMock,
    ):
        yield


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/move-events",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


def _user_context() -> UserContext:
    return UserContext(user_id="staff-1", email="staff@example.com", organization_id="org-1")


@pytest.mark.asyncio
async def test_staff_move_event_access_missing_org():
    with patch(
        "apps.user_service.app.utils.common_utils.extract_user_context",
        new_callable=AsyncMock,
        return_value=UserContext(user_id="staff-1", email="s@x.com", organization_id=""),
    ):
        with pytest.raises(ValidationException):
            await _staff_move_event_access(
                request=_request(),
                current_user={"sub": "staff-1"},
                db_connection=MagicMock(),
                move_event_id=MOVE_EVENT_ID,
                permission_codes="view",
            )


@pytest.mark.asyncio
async def test_staff_move_event_access_event_not_found():
    with (
        patch(
            "apps.user_service.app.utils.common_utils.extract_user_context",
            new_callable=AsyncMock,
            return_value=_user_context(),
        ),
        patch(
            "apps.user_service.app.db.repositories.move_events_repository.MoveEventsRepository"
        ) as repo_cls,
    ):
        repo_cls.return_value.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(NotFoundException):
            await _staff_move_event_access(
                request=_request(),
                current_user={"sub": "staff-1"},
                db_connection=MagicMock(),
                move_event_id=MOVE_EVENT_ID,
                permission_codes="view",
            )


@pytest.mark.asyncio
async def test_create_move_event_unit_not_found():
    with (
        patch(
            "apps.user_service.app.utils.common_utils.extract_user_context",
            new_callable=AsyncMock,
            return_value=_user_context(),
        ),
        patch(
            "apps.user_service.app.db.repositories.contact_units_repository.ContactUnitsRepository"
        ) as repo_cls,
    ):
        repo_cls.return_value.get_unit_project = AsyncMock(return_value=None)
        body = CreateMoveEventRequest(
            unit_id=UNIT_ID,
            contact_id="44444444-4444-4444-4444-444444444444",
            move_type=MoveEventType.MOVE_OUT,
            event_date="2026-08-01",
        )
        with pytest.raises(NotFoundException):
            await create_move_event(
                request=_request(),
                body=body,
                db_connection=MagicMock(),
                current_user={"sub": "staff-1"},
            )


@pytest.mark.asyncio
async def test_delete_move_event_success():
    detail = MoveEventResponse(
        id=MOVE_EVENT_ID,
        organization_id="org-1",
        project_id=PROJECT_ID,
        unit_id=UNIT_ID,
        contact_id="44444444-4444-4444-4444-444444444444",
        move_type=MoveEventType.MOVE_OUT.value,
        event_date="2026-08-01",
        fee_currency="INR",
    )
    with (
        patch(
            "apps.user_service.app.api.move_events._staff_move_event_access",
            new_callable=AsyncMock,
            return_value=_user_context(),
        ),
        patch("apps.user_service.app.api.move_events.MoveEventsService") as svc_cls,
    ):
        svc_cls.return_value.delete_move_event = AsyncMock(return_value=detail)
        response = await delete_move_event(
            request=_request(),
            move_event_id=MOVE_EVENT_ID,
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )

    assert response.status_code == 200
    assert MOVE_EVENT_ID in response.body.decode()
