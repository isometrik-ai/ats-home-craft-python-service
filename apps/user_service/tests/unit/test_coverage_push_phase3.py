"""Phase 3 coverage push: quick wins across libs and services."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from apps.user_service.app.schemas.common import Phone
from apps.user_service.app.schemas.contacts import CreateContactRequest
from apps.user_service.app.schemas.enums import ContactType
from apps.user_service.app.services.contacts_service import ContactsService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_db.drivers.asyncpg_uow import UnitOfWork
from libs.shared_middleware.jwt_auth import check_user_access_async, get_user_from_auth
from libs.shared_utils.http_exceptions import (
    NotFoundException,
    ServiceUnavailableException,
    UnauthorizedException,
)
from libs.shared_utils.translations import Translator

ORG_ID = "550e8400-e29b-41d4-a716-446655440000"
CONTACT_ID = "660e8400-e29b-41d4-a716-446655440001"


# --- asyncpg_uow ---


@pytest.mark.asyncio
async def test_unit_of_work_commits_on_success() -> None:
    """UnitOfWork acquires connection and transaction, then commits."""
    mock_conn = MagicMock()
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    class _ConnCtx:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *_args):
            return False

    mock_pool = MagicMock()
    with patch(
        "libs.shared_db.drivers.asyncpg_uow.AcquireConnection",
        return_value=_ConnCtx(),
    ):
        async with UnitOfWork(pool=mock_pool) as conn:
            assert conn is mock_conn
    mock_tx.__aenter__.assert_awaited_once()
    mock_tx.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_unit_of_work_uses_get_pool_when_missing() -> None:
    """UnitOfWork lazily loads pool when not injected."""
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction.return_value = mock_tx

    class _ConnCtx:
        async def __aenter__(self):
            return mock_conn

        async def __aexit__(self, *_args):
            return False

    conn_ctx = _ConnCtx()
    with (
        patch("libs.shared_db.drivers.asyncpg_uow.get_pool", AsyncMock(return_value=mock_pool)),
        patch("libs.shared_db.drivers.asyncpg_uow.AcquireConnection", return_value=conn_ctx),
    ):
        async with UnitOfWork():
            pass


# --- jwt_auth remaining branches ---


@pytest.mark.asyncio
async def test_check_user_access_reraises_http_exception() -> None:
    """HTTPException from DB layer is re-raised unchanged."""
    db_connection = AsyncMock()
    db_connection.fetchrow = AsyncMock(side_effect=HTTPException(status_code=403, detail="nope"))
    with pytest.raises(HTTPException):
        await check_user_access_async(
            permission_code=["perm.a"],
            user_id="user-1",
            organization_id="org-1",
            db_connection=db_connection,
        )


@pytest.mark.asyncio
async def test_get_user_from_auth_blocked_session() -> None:
    """get_user_from_auth raises when Redis marks session blocked."""
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    request.state.user = {"sub": "u1", "session_id": "s1"}
    with (
        patch(
            "libs.shared_middleware.jwt_auth.resolve_session_context_from_redis",
            AsyncMock(return_value=(True, None)),
        ),
        pytest.raises(UnauthorizedException),
    ):
        await get_user_from_auth(request, redis_client=MagicMock())


# --- graphiti_health ---


@pytest.mark.asyncio
async def test_check_graphiti_readiness_not_initialized() -> None:
    """Graphiti readiness fails when driver is not initialized."""
    from libs.shared_utils.graphiti_health import check_graphiti_readiness

    with (
        patch("libs.shared_utils.graphiti_health.is_graphiti_configured", return_value=True),
        patch("libs.shared_utils.graphiti_health.is_graphiti_initialized", return_value=False),
        pytest.raises(RuntimeError, match="not initialized"),
    ):
        await check_graphiti_readiness()


# --- translations ---


def test_translator_file_not_found_on_missing_path(tmp_path) -> None:
    """Missing locale path during load is ignored."""
    custom = Translator(default_language="en")
    custom._load_from_path(tmp_path / "missing-dir")
    assert custom.get("missing") == "missing"


# --- event_service ---


@pytest.mark.asyncio
async def test_create_lifecycle_events_requires_db() -> None:
    """create_lifecycle_events raises when db_connection is missing."""
    service = EventService(db_connection=None)
    with pytest.raises(ValueError):
        await service.create_lifecycle_events(items=[], topics=[])


@pytest.mark.asyncio
async def test_create_lifecycle_events_empty_items() -> None:
    """create_lifecycle_events returns [] for empty input."""
    service = EventService(db_connection=MagicMock())
    assert await service.create_lifecycle_events(items=[], topics=[]) == []


# --- contacts property flow ---


@pytest.mark.asyncio
async def test_create_contact_org_not_found_for_phone_only_body(monkeypatch) -> None:
    """Phone-only create fails when organization is missing."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(return_value=None)
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=UserContext(user_id="u1", email="a@b.com", organization_id=ORG_ID),
        supabase_client=MagicMock(),
    )
    svc.contacts_repo = MagicMock()
    svc.org_repo = org_repo
    monkeypatch.setattr(svc, "_validate_custom_fields_for_create", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        svc,
        "_prepare_optional_contact_company_association",
        AsyncMock(return_value=(None, None, None, False)),
    )
    monkeypatch.setattr(
        svc,
        "_apply_inferred_company_assoc_on_create",
        AsyncMock(return_value=(None, None, None, False)),
    )

    with pytest.raises(NotFoundException):
        await svc.create_contact(
            CreateContactRequest(
                contact_type=ContactType.OWNER,
                first_name="Jane",
                phones=[Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True)],
            ),
        )


@pytest.mark.asyncio
async def test_create_contact_auth_user_failure(monkeypatch) -> None:
    """Unified create raises when Supabase user creation fails."""
    org_repo = MagicMock()
    org_repo.get_organization_by_id = AsyncMock(return_value={"id": ORG_ID, "settings": "{}"})
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=UserContext(user_id="u1", email="a@b.com", organization_id=ORG_ID),
        supabase_client=MagicMock(),
    )
    svc.contacts_repo = MagicMock()
    svc.org_repo = org_repo
    monkeypatch.setattr(svc, "_validate_custom_fields_for_create", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        svc,
        "_prepare_optional_contact_company_association",
        AsyncMock(return_value=(None, None, None, False)),
    )
    monkeypatch.setattr(
        svc,
        "_apply_inferred_company_assoc_on_create",
        AsyncMock(return_value=(None, None, None, False)),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.create_user",
        AsyncMock(return_value=None),
    )
    mock_user_repo = MagicMock()
    mock_user_repo.get_auth_users_by_phone_or_email = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.UserRepository",
        lambda db_connection: mock_user_repo,
    )

    with pytest.raises(ServiceUnavailableException):
        await svc.create_contact(
            CreateContactRequest(
                contact_type=ContactType.OWNER,
                first_name="Jane",
                phones=[Phone(phone_number="9876543210", phone_isd_code="+91", is_primary=True)],
            ),
        )


@pytest.mark.asyncio
async def test_validate_custom_fields_for_create_with_org(monkeypatch) -> None:
    """Contact create validates custom fields when organization context exists."""
    svc = ContactsService(
        db_connection=MagicMock(),
        user_context=UserContext(user_id="u1", email="a@b.com", organization_id=ORG_ID),
    )
    monkeypatch.setattr(
        "apps.user_service.app.services.contacts_service.CustomFieldService",
        lambda **kwargs: MagicMock(
            validate_for_create=AsyncMock(return_value=[{"field_id": "f1"}])
        ),
    )
    result = await svc._validate_custom_fields_for_create([{"field_id": "f1"}])
    assert result == [{"field_id": "f1"}]
