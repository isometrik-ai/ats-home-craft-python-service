"""Unit tests for gate pass API route handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from apps.user_service.app.api import gate_passes as gate_passes_module
from apps.user_service.app.api.gate_passes import (
    _ensure_gate_access_for_pass,
    check_in_pass,
    check_out_pass,
    verify_pass,
)
from apps.user_service.app.schemas.enums import PassAccessStatus, PassEntryMethod
from apps.user_service.app.schemas.gate_passes import (
    CheckInRequest,
    CheckOutRequest,
    VerifyPassRequest,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ForbiddenException, NotFoundException


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
            "path": "/passes/verify",
            "headers": [],
            "client": ("127.0.0.1", 50000),
        }
    )


def _user_context(*, org_id: str | None = "org-1") -> UserContext:
    return UserContext(
        user_id="staff-1",
        email="guard@example.com",
        organization_id=org_id,
    )


@pytest.mark.asyncio
async def test_ensure_gate_access_missing_project_id():
    with pytest.raises(ForbiddenException):
        await _ensure_gate_access_for_pass(
            current_user={"sub": "staff-1"},
            db_connection=MagicMock(),
            pass_row={"id": "pass-1"},
            request=_request(),
        )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.gate_passes.ensure_staff_project_access",
    new_callable=AsyncMock,
)
async def test_ensure_gate_access_delegates_to_staff_access(mock_access):
    mock_access.return_value = _user_context()
    ctx = await _ensure_gate_access_for_pass(
        current_user={"sub": "staff-1"},
        db_connection=MagicMock(),
        pass_row={"id": "pass-1", "project_id": "project-1"},
        request=_request(),
    )
    assert ctx.organization_id == "org-1"
    mock_access.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.gate_passes.extract_user_context",
    new_callable=AsyncMock,
)
async def test_verify_pass_missing_organization(mock_extract):
    mock_extract.return_value = _user_context(org_id=None)
    with pytest.raises(ForbiddenException):
        await verify_pass(
            request=_request(),
            body=VerifyPassRequest(code="4821"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.gate_passes.extract_user_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.gate_passes.PassesRepository")
async def test_verify_pass_not_found(mock_repo_cls, mock_extract):
    mock_extract.return_value = _user_context()
    mock_repo_cls.return_value.get_by_code = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await verify_pass(
            request=_request(),
            body=VerifyPassRequest(code="9999"),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.gate_passes._ensure_gate_access_for_pass",
    new_callable=AsyncMock,
)
@patch(
    "apps.user_service.app.api.gate_passes.extract_user_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.gate_passes.PassVerificationService")
@patch("apps.user_service.app.api.gate_passes.PassesRepository")
async def test_verify_pass_success(
    mock_repo_cls,
    mock_service_cls,
    mock_extract,
    mock_gate_access,
):
    mock_extract.return_value = _user_context()
    mock_gate_access.return_value = _user_context()
    mock_repo_cls.return_value.get_by_code = AsyncMock(
        return_value={"id": "pass-1", "project_id": "project-1", "code": "4821"}
    )
    service = mock_service_cls.return_value
    service.verify = AsyncMock(return_value={"pass_id": "pass-1", "can_check_in": True})

    response = await verify_pass(
        request=_request(),
        body=VerifyPassRequest(code="4821", gate_id="gate-1"),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )

    assert response.status_code == 200
    service.verify.assert_awaited_once_with(code="4821", gate_id="gate-1")


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.gate_passes.extract_user_context",
    new_callable=AsyncMock,
)
async def test_check_in_missing_organization(mock_extract):
    mock_extract.return_value = _user_context(org_id=None)
    with pytest.raises(ForbiddenException):
        await check_in_pass(
            request=_request(),
            pass_id="pass-1",
            body=_check_in_body(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )


def _check_in_body() -> CheckInRequest:
    return CheckInRequest(
        entry_method=PassEntryMethod.QR,
        access_status=PassAccessStatus.APPROVED,
    )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.gate_passes.extract_user_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.gate_passes.PassesRepository")
async def test_check_in_pass_not_found(mock_repo_cls, mock_extract):
    mock_extract.return_value = _user_context()
    mock_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await check_in_pass(
            request=_request(),
            pass_id="pass-1",
            body=_check_in_body(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )


@pytest.mark.asyncio
@patch("apps.user_service.app.api.gate_passes.set_audit_context")
@patch(
    "apps.user_service.app.api.gate_passes._ensure_gate_access_for_pass",
    new_callable=AsyncMock,
)
@patch(
    "apps.user_service.app.api.gate_passes.extract_user_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.gate_passes.PassVerificationService")
@patch("apps.user_service.app.api.gate_passes.PassesRepository")
async def test_check_in_success(
    mock_repo_cls,
    mock_service_cls,
    mock_extract,
    mock_gate_access,
    mock_audit,
):
    mock_extract.return_value = _user_context()
    mock_gate_access.return_value = _user_context()
    mock_repo_cls.return_value.get_by_id = AsyncMock(
        return_value={"id": "pass-1", "project_id": "project-1"}
    )
    service = mock_service_cls.return_value
    service.check_in = AsyncMock(return_value={"entry_count": 1})

    response = await check_in_pass(
        request=_request(),
        pass_id="pass-1",
        body=_check_in_body(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )

    assert response.status_code == 200
    mock_audit.assert_called_once()
    service.check_in.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.gate_passes.extract_user_context",
    new_callable=AsyncMock,
)
async def test_check_out_missing_organization(mock_extract):
    mock_extract.return_value = _user_context(org_id=None)
    with pytest.raises(ForbiddenException):
        await check_out_pass(
            request=_request(),
            pass_id="pass-1",
            body=CheckOutRequest(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.api.gate_passes.extract_user_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.gate_passes.PassesRepository")
async def test_check_out_pass_not_found(mock_repo_cls, mock_extract):
    mock_extract.return_value = _user_context()
    mock_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)
    with pytest.raises(NotFoundException):
        await check_out_pass(
            request=_request(),
            pass_id="pass-1",
            body=CheckOutRequest(),
            db_connection=MagicMock(),
            current_user={"sub": "staff-1"},
        )


@pytest.mark.asyncio
@patch("apps.user_service.app.api.gate_passes.set_audit_context")
@patch(
    "apps.user_service.app.api.gate_passes._ensure_gate_access_for_pass",
    new_callable=AsyncMock,
)
@patch(
    "apps.user_service.app.api.gate_passes.extract_user_context",
    new_callable=AsyncMock,
)
@patch("apps.user_service.app.api.gate_passes.PassVerificationService")
@patch("apps.user_service.app.api.gate_passes.PassesRepository")
async def test_check_out_success(
    mock_repo_cls,
    mock_service_cls,
    mock_extract,
    mock_gate_access,
    mock_audit,
):
    mock_extract.return_value = _user_context()
    mock_gate_access.return_value = _user_context()
    mock_repo_cls.return_value.get_by_id = AsyncMock(
        return_value={"id": "pass-1", "project_id": "project-1"}
    )
    service = mock_service_cls.return_value
    service.check_out = AsyncMock(return_value={"pass_status": "active"})

    response = await check_out_pass(
        request=_request(),
        pass_id="pass-1",
        body=CheckOutRequest(),
        db_connection=MagicMock(),
        current_user={"sub": "staff-1"},
    )

    assert response.status_code == 200
    mock_audit.assert_called_once()
    service.check_out.assert_awaited_once()


def test_gate_passes_router_prefix():
    assert gate_passes_module.router.prefix == "/passes"
