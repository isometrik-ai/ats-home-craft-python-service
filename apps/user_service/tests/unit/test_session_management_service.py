"""Unit tests for SessionManagementService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.user_service.app.services.session_management_service import (
    SessionManagementService,
)
from libs.shared_utils.http_exceptions import ValidationException


@pytest.mark.asyncio
async def test_extract_session_id_missing_session() -> None:
    """Invalid session payload raises ValidationException."""
    svc = SessionManagementService(db_connection=MagicMock())
    with pytest.raises(ValidationException):
        await svc._extract_session_id(None, MagicMock())


@pytest.mark.asyncio
async def test_extract_session_id_missing_access_token() -> None:
    """Session without access token raises ValidationException."""
    svc = SessionManagementService(db_connection=MagicMock())
    session = MagicMock(access_token=None)
    with pytest.raises(ValidationException):
        await svc._extract_session_id(session, MagicMock())


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.session_management_service.get_claims_from_token",
    new_callable=AsyncMock,
)
async def test_extract_session_id_missing_claim(mock_claims: AsyncMock) -> None:
    """JWT without session_id or jti raises ValidationException."""
    mock_claims.return_value = {}
    svc = SessionManagementService(db_connection=MagicMock())
    session = MagicMock(access_token="token")
    with pytest.raises(ValidationException):
        await svc._extract_session_id(session, MagicMock())


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.session_management_service.get_claims_from_token",
    new_callable=AsyncMock,
)
async def test_extract_session_id_success(mock_claims: AsyncMock) -> None:
    """Session id is extracted from JWT claims."""
    mock_claims.return_value = {"session_id": "sess-1"}
    svc = SessionManagementService(db_connection=MagicMock())
    session = MagicMock(access_token="token")
    assert await svc._extract_session_id(session, MagicMock()) == "sess-1"


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.session_management_service.get_claims_from_token",
    new_callable=AsyncMock,
)
async def test_extract_session_id_uses_jti_fallback(mock_claims: AsyncMock) -> None:
    """Session id falls back to jti claim when session_id absent."""
    mock_claims.return_value = {"jti": "sess-jti"}
    svc = SessionManagementService(db_connection=MagicMock())
    session = MagicMock(access_token="token")
    assert await svc._extract_session_id(session, MagicMock()) == "sess-jti"


@pytest.mark.asyncio
async def test_update_session_organization_context_invalid_ids() -> None:
    """Blank session or organization id raises ValidationException."""
    svc = SessionManagementService(db_connection=MagicMock())
    with pytest.raises(ValidationException):
        await svc.update_session_organization_context("", "u1", "org-1")


@pytest.mark.asyncio
async def test_update_session_organization_context_success() -> None:
    """Valid ids delegate to session repository."""
    repo = MagicMock()
    repo.update_session_organization_context = AsyncMock()
    svc = SessionManagementService(db_connection=MagicMock())
    svc.session_repository = repo

    await svc.update_session_organization_context("sess-1", "u1", "org-1")

    repo.update_session_organization_context.assert_awaited_once_with(
        session_id="sess-1",
        user_id="u1",
        organization_id="org-1",
    )
