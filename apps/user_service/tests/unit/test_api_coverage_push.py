"""API route unit tests for remaining coverage gaps."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.user_service.app.api.organization_memory import post_org_memory_query
from apps.user_service.app.api.verification_codes import (
    get_optional_user,
    send_verification_code,
    verify_verification_code,
)
from apps.user_service.app.schemas.org_memory import OrgMemoryQueryBody
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    VerificationType,
    VerifyVerificationCodeRequest,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import (
    ForbiddenException,
    InternalServerErrorException,
    ServiceUnavailableException,
)

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []})


@pytest.mark.asyncio
async def test_get_optional_user_without_auth() -> None:
    """Optional auth returns None when request has no user."""
    assert await get_optional_user(_request()) is None


@pytest.mark.asyncio
async def test_get_optional_user_with_auth() -> None:
    """Optional auth delegates to get_user_from_auth when user present."""
    request = _request()
    request.state.user = {"sub": "u1"}
    with patch(
        "apps.user_service.app.api.verification_codes.get_user_from_auth",
        AsyncMock(return_value={"sub": "u1", "email": "a@b.com"}),
    ):
        user = await get_optional_user(request)
    assert user["sub"] == "u1"


@pytest.mark.asyncio
async def test_send_verification_code_success() -> None:
    """send_verification_code returns service payload."""
    expiry = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
    with patch(
        "apps.user_service.app.api.verification_codes.VerificationCodeService",
    ) as svc_cls:
        svc_cls.return_value.send_verification_code = AsyncMock(
            return_value={
                "verification_id": "ver-1",
                "expiryAt": expiry,
                "attemptsLeft": 3,
            }
        )
        response = await send_verification_code(
            request=_request(),
            data=SendVerificationCodeRequest(
                type=VerificationType.EMAIL,
                email="user@example.com",
            ),
            current_user=None,
            db_connection=MagicMock(),
            sb_client=MagicMock(),
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_send_verification_code_internal_error() -> None:
    """Unexpected send errors map to InternalServerErrorException."""
    with patch(
        "apps.user_service.app.api.verification_codes.VerificationCodeService",
    ) as svc_cls:
        svc_cls.return_value.send_verification_code = AsyncMock(
            side_effect=RuntimeError("smtp down")
        )
        with pytest.raises(InternalServerErrorException):
            await send_verification_code(
                request=_request(),
                data=SendVerificationCodeRequest(
                    type=VerificationType.EMAIL,
                    email="user@example.com",
                ),
                current_user=None,
                db_connection=MagicMock(),
                sb_client=MagicMock(),
            )


@pytest.mark.asyncio
async def test_send_verification_code_reraises_http_exception() -> None:
    """HTTPException from service is re-raised."""
    with patch(
        "apps.user_service.app.api.verification_codes.VerificationCodeService",
    ) as svc_cls:
        svc_cls.return_value.send_verification_code = AsyncMock(
            side_effect=HTTPException(status_code=400, detail="bad")
        )
        with pytest.raises(HTTPException):
            await send_verification_code(
                request=_request(),
                data=SendVerificationCodeRequest(
                    type=VerificationType.EMAIL,
                    email="user@example.com",
                ),
                current_user=None,
                db_connection=MagicMock(),
                sb_client=MagicMock(),
            )


@pytest.mark.asyncio
async def test_verify_verification_code_internal_error() -> None:
    """Unexpected verify errors map to InternalServerErrorException."""
    with patch(
        "apps.user_service.app.api.verification_codes.VerificationCodeService",
    ) as svc_cls:
        svc_cls.return_value.verify_verification_code = AsyncMock(
            side_effect=RuntimeError("db down")
        )
        with pytest.raises(InternalServerErrorException):
            await verify_verification_code(
                request=_request(),
                data=VerifyVerificationCodeRequest(
                    type=VerificationType.EMAIL,
                    verification_id="ver-1",
                    verification_code="123456",
                    email="user@example.com",
                ),
                current_user=None,
                db_connection=MagicMock(),
                sb_client=MagicMock(),
            )


@pytest.mark.asyncio
async def test_org_memory_query_graphiti_not_configured() -> None:
    """Org memory query fails when Graphiti is off."""
    user_ctx = UserContext(user_id="u1", email="a@b.com", organization_id=ORG_ID)
    with (
        patch(
            "apps.user_service.app.api.organization_memory.extract_user_context",
            AsyncMock(return_value=user_ctx),
        ),
        patch(
            "apps.user_service.app.api.organization_memory.require_org_memory_query_access",
            AsyncMock(),
        ),
        patch(
            "apps.user_service.app.api.organization_memory.is_graphiti_configured",
            return_value=False,
        ),
        pytest.raises(ServiceUnavailableException),
    ):
        await post_org_memory_query(
            request=_request(),
            body=OrgMemoryQueryBody(query="Who is Jane?"),
            db_connection=MagicMock(),
            current_user={"sub": "u1"},
        )


@pytest.mark.asyncio
async def test_org_memory_query_disabled_for_org() -> None:
    """Org memory query fails when feature flag is off."""
    user_ctx = UserContext(user_id="u1", email="a@b.com", organization_id=ORG_ID)
    with (
        patch(
            "apps.user_service.app.api.organization_memory.extract_user_context",
            AsyncMock(return_value=user_ctx),
        ),
        patch(
            "apps.user_service.app.api.organization_memory.require_org_memory_query_access",
            AsyncMock(),
        ),
        patch(
            "apps.user_service.app.api.organization_memory.is_graphiti_configured",
            return_value=True,
        ),
        patch(
            "apps.user_service.app.api.organization_memory.is_organization_memory_enabled",
            AsyncMock(return_value=False),
        ),
        pytest.raises(ForbiddenException),
    ):
        await post_org_memory_query(
            request=_request(),
            body=OrgMemoryQueryBody(query="Who is Jane?"),
            db_connection=MagicMock(),
            current_user={"sub": "u1"},
        )


@pytest.mark.asyncio
async def test_org_memory_query_success() -> None:
    """Org memory query returns grounded answer."""
    user_ctx = UserContext(user_id="u1", email="a@b.com", organization_id=ORG_ID)
    with (
        patch(
            "apps.user_service.app.api.organization_memory.extract_user_context",
            AsyncMock(return_value=user_ctx),
        ),
        patch(
            "apps.user_service.app.api.organization_memory.require_org_memory_query_access",
            AsyncMock(),
        ),
        patch(
            "apps.user_service.app.api.organization_memory.is_graphiti_configured",
            return_value=True,
        ),
        patch(
            "apps.user_service.app.api.organization_memory.is_organization_memory_enabled",
            AsyncMock(return_value=True),
        ),
        patch(
            "apps.user_service.app.api.organization_memory.OrgMemoryQueryService",
        ) as svc_cls,
    ):
        svc_cls.return_value.run = AsyncMock(return_value="Jane is a contact.")
        response = await post_org_memory_query(
            request=_request(),
            body=OrgMemoryQueryBody(query="Who is Jane?"),
            db_connection=MagicMock(),
            current_user={"sub": "u1"},
        )
    assert response.status_code == 200
    svc_cls.return_value.run.assert_awaited_once()
