"""Unit tests for UserPushTokenService."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from apps.user_service.app.schemas.enums import PushPlatform
from apps.user_service.app.schemas.user_push_tokens import RegisterUserPushTokenRequest
from apps.user_service.app.services.user_push_token_service import (
    UserPushTokenService,
    user_push_topic,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import BadRequestException


class _FakePushTokensRepo:
    """Fake push tokens repository."""

    def __init__(self):
        self.upsert_calls = []
        self.delete_calls = []

    async def upsert_device(self, **kwargs):
        """Record upsert calls."""
        self.upsert_calls.append(kwargs)
        return {
            "device_id": kwargs["device_id"],
            "platform": kwargs["platform"],
            "updated_at": datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc),
        }

    async def delete_by_device_and_user(self, *, device_id, user_id):
        """Record delete calls."""
        self.delete_calls.append((device_id, user_id))
        return True


def _service(
    *,
    org_id: str = "org-1",
    user_id: str = "user-1",
    push_repo: _FakePushTokensRepo | None = None,
) -> UserPushTokenService:
    """Build service with fake repo."""
    service = UserPushTokenService(
        db_connection=None,
        user_context=UserContext(
            user_id=user_id,
            email="user@example.com",
            organization_id=org_id,
            user_type="client",
        ),
    )
    service.push_tokens_repo = push_repo or _FakePushTokensRepo()
    return service


@pytest.mark.asyncio
async def test_register_upserts_for_authenticated_user():
    """register_device upserts with authenticated user and organization."""
    push_repo = _FakePushTokensRepo()
    service = _service(push_repo=push_repo)
    body = RegisterUserPushTokenRequest(
        device_id="device-xyz",
        push_token="token-abc",
        platform=PushPlatform.IOS,
        app_version="2.0.0",
    )

    result = await service.register_device(body=body)

    assert result["device_id"] == "device-xyz"
    assert result["platform"] == PushPlatform.IOS
    assert result["registered_at"].startswith("2026")
    assert len(push_repo.upsert_calls) == 1
    upsert = push_repo.upsert_calls[0]
    assert upsert["device_id"] == "device-xyz"
    assert upsert["organization_id"] == "org-1"
    assert upsert["user_id"] == "user-1"
    assert upsert["push_token"] == "token-abc"


@pytest.mark.asyncio
async def test_register_rejects_missing_org_or_user():
    """register_device fails when org or user context is missing."""
    service = _service(org_id="", user_id="")

    with pytest.raises(BadRequestException):
        await service.register_device(
            body=RegisterUserPushTokenRequest(
                device_id="device-xyz",
                push_token="token-abc",
                platform=PushPlatform.WEB,
            )
        )


@pytest.mark.asyncio
async def test_unregister_calls_scoped_delete():
    """unregister_device deletes locally scoped to authenticated user."""
    push_repo = _FakePushTokensRepo()
    service = _service(push_repo=push_repo)

    result = await service.unregister_device(device_id="device-xyz")

    assert result == {"device_id": "device-xyz"}
    assert push_repo.delete_calls == [("device-xyz", "user-1")]


@pytest.mark.asyncio
async def test_unregister_idempotent_no_row():
    """unregister_device succeeds even when repository deletes nothing."""
    push_repo = _FakePushTokensRepo()
    push_repo.delete_by_device_and_user = AsyncMock(return_value=False)
    service = _service(push_repo=push_repo)

    result = await service.unregister_device(device_id="device-xyz")

    assert result == {"device_id": "device-xyz"}


@pytest.mark.asyncio
async def test_device_handoff_upsert_reassigns_new_user():
    """Second register on same device passes new user to upsert (handoff)."""
    push_repo = _FakePushTokensRepo()
    service = _service(push_repo=push_repo, user_id="user-2")
    body = RegisterUserPushTokenRequest(
        device_id="device-xyz",
        push_token="token-abc",
        platform=PushPlatform.IOS,
    )

    await service.register_device(body=body)

    upsert = push_repo.upsert_calls[0]
    assert upsert["device_id"] == "device-xyz"
    assert upsert["user_id"] == "user-2"


def test_user_push_topic_helper():
    """user_push_topic returns deterministic org/user topic string."""
    assert user_push_topic("org-1", "user-1") == "org:org-1:user:user-1"
