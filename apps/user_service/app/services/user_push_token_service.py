"""User push token registration service."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.user_push_tokens_repository import (
    UserPushTokensRepository,
)
from apps.user_service.app.schemas.user_push_tokens import (
    RegisterUserPushTokenRequest,
    UserPushTokenResponse,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    extract_user_context,
    format_iso_datetime,
)
from libs.shared_utils.http_exceptions import BadRequestException
from libs.shared_utils.status_codes import CustomStatusCode


def user_push_topic(org_id: str, user_id: str) -> str:
    """Derive a deterministic FCM/APNS topic for an org-scoped user."""
    return f"org:{org_id}:user:{user_id}"


class UserPushTokenService:
    """Register and unregister push devices for authenticated users."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.push_tokens_repo = UserPushTokensRepository(db_connection=db_connection)

    @classmethod
    async def for_end_user(
        cls,
        *,
        db_connection: asyncpg.Connection,
        current_user: dict,
    ) -> UserPushTokenService:
        """Build service for end-user routes using JWT org and user context."""
        user_context = await extract_user_context(current_user, db_connection)
        user_metadata = current_user.get("user_metadata") or {}
        metadata_organization_id = user_metadata.get("organization_id")
        if metadata_organization_id:
            user_context.organization_id = metadata_organization_id

        return cls(
            db_connection=db_connection,
            user_context=user_context,
        )

    def _require_org_and_user(self) -> tuple[str, str]:
        """Return organization and user ids from context; raise if missing."""
        org_id = (self.user_context.organization_id or "").strip()
        user_id = (self.user_context.user_id or "").strip()
        if not org_id or not user_id:
            raise BadRequestException(
                message_key="errors.bad_request",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )
        return org_id, user_id

    async def register_device(
        self,
        *,
        body: RegisterUserPushTokenRequest,
    ) -> dict[str, Any]:
        """Upsert a push device registration for the authenticated user."""
        org_id, user_id = self._require_org_and_user()

        row = await self.push_tokens_repo.upsert_device(
            device_id=body.device_id,
            organization_id=org_id,
            user_id=user_id,
            push_token=body.push_token,
            platform=body.platform.value,
            app_version=body.app_version,
        )
        if not row:
            raise BadRequestException(
                message_key="contacts.errors.push_device_registration_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        response = UserPushTokenResponse(
            device_id=str(row["device_id"]),
            platform=body.platform,
            registered_at=format_iso_datetime(row.get("updated_at")) or "",
        )
        return response.model_dump(exclude_none=True)

    async def unregister_device(self, *, device_id: str) -> dict[str, Any]:
        """Remove a push device registration for the authenticated user (idempotent)."""
        _, user_id = self._require_org_and_user()
        normalized_device_id = (device_id or "").strip()
        if not normalized_device_id:
            raise BadRequestException(
                message_key="errors.bad_request",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        await self.push_tokens_repo.delete_by_device_and_user(
            device_id=normalized_device_id,
            user_id=user_id,
        )
        return {"device_id": normalized_device_id}
