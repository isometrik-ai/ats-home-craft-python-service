"""Service for invite business logic."""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories import (
    InviteRepository,
    OrganisationMemberRepository,
    OrganisationRepository,
    RoleRepository,
)
from apps.user_service.app.schemas.auth import SignupRequest
from apps.user_service.app.schemas.invites import (
    InviteAcceptBySettingPasswordRequest,
    InviteCreateRequest,
)
from apps.user_service.app.utils.common_utils import UserContext, validate_uuid_format
from apps.user_service.app.utils.invite_utils import build_invite_list_item, hash_token
from apps.user_service.app.utils.organisation_utils import (
    validate_organization_subscription,
)
from apps.user_service.app.utils.user_utils import build_full_name
from libs.shared_db.supabase_db.admin_operations.user import get_user_by_id
from libs.shared_db.supabase_db.admin_operations.user_utility_admin import (
    sign_up_supabase_user,
)
from libs.shared_utils.email_utils import send_organization_invitation_email
from libs.shared_utils.http_exceptions import (
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
    ServiceUnavailableException,
)
from libs.shared_utils.isometrik_service import (
    create_isometrik_user,
    is_isometrik_enabled,
)
from libs.shared_utils.status_codes import CustomStatusCode

INVITE_EXPIRY_DAYS = int(os.getenv("INVITE_EXPIRY_DAYS", "7"))
BASE_URL = os.getenv("BASE_URL")


class InviteService:
    """Service for invite business logic.

    User context is provided during initialization.
    """

    def __init__(
        self,
        user_context: UserContext | None,
        db_connection: asyncpg.Connection,
    ) -> None:
        self.user_context = user_context
        self.db_connection = db_connection
        self.invite_repository = InviteRepository(db_connection=db_connection)
        self.organisation_repository = OrganisationRepository(db_connection=db_connection)
        self.role_repository = RoleRepository(db_connection=db_connection)
        self.organisation_member_repository = OrganisationMemberRepository(
            db_connection=db_connection
        )

    async def accept_and_set_password(
        self, body: InviteAcceptBySettingPasswordRequest
    ) -> dict[str, Any]:
        """Accept an organization invitation by setting password."""
        # Get invitation details by token
        invitation_data = await self.invite_repository.get_invite_by_token(body.token)
        if not invitation_data:
            raise NotFoundException(
                message_key="invitations.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Check if user is already a member
        existing_member = await self.invite_repository.check_user_membership(
            invitation_data["organization_id"], invitation_data["email"]
        )
        if existing_member:
            raise ConflictException(
                message_key="invitations.errors.user_already_a_member",
                custom_code=CustomStatusCode.CONFLICT,
            )

        # Get organization data when needed for isometrik credentials
        organization_data = await self.organisation_repository.get_organisation_by_id(
            invitation_data["organization_id"]
        )
        if not organization_data:
            raise NotFoundException(
                message_key="invitations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        role_row = await self.role_repository.get_role_by_id(
            invitation_data["role_id"], invitation_data["organization_id"]
        )
        if not role_row:
            raise NotFoundException(
                message_key="invitations.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        role_data = dict(role_row)

        inv_meta = invitation_data.get("metadata", {})
        if isinstance(inv_meta, str):
            import json

            inv_meta = json.loads(inv_meta) if inv_meta else {}

        signup_result = await sign_up_supabase_user(
            SignupRequest(
                email=invitation_data["email"],
                password=body.password,
                first_name=inv_meta.get("first_name", None),
                last_name=inv_meta.get("last_name", None),
                phone=inv_meta.get("phone", None),
                timezone="UTC",
                salutation=inv_meta.get("salutation", None),
                verification_id="",
                verification_code="",
            )
        )

        if not signup_result:
            raise InternalServerErrorException(
                message_key="errors.internal_server_error",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )
        # Get isometrik credentials from organization settings
        org_settings = organization_data.get("settings", {})
        if isinstance(org_settings, str):
            import json

            org_settings = json.loads(org_settings) if org_settings else {}

        isometrik_credentials = org_settings.get("isometrik_application_details", {})

        if not isometrik_credentials:
            raise NotFoundException(
                message_key="invitations.errors.isometrik_application_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Add user to organization
        await self._add_user_to_organization(
            organization_id=invitation_data["organization_id"],
            invite_data={
                "user_id": signup_result.user.id,
                "first_name": inv_meta.get("first_name", None),
                "last_name": inv_meta.get("last_name", None),
                "phone": inv_meta.get("phone", None),
                "timezone": "UTC",
                "salutation": inv_meta.get("salutation", None),
            },
            email=invitation_data["email"],
            role_id=invitation_data["role_id"],
            role_name=role_data["name"],
            invited_by=invitation_data["invited_by"],
            isometrik_credentials=isometrik_credentials,
        )

        # Update invitation status
        await self.invite_repository.update_invite_status(
            invitation_data["id"], "accepted", signup_result.user.id
        )

        return {"status": "accepted"}

    async def create_invitation(
        self, organization_id: str, body: InviteCreateRequest
    ) -> dict[str, Any]:
        """Create a new organization invitation."""
        # Validate organization ID format
        validate_uuid_format(organization_id, "organization ID")

        if not self.user_context.organization_id == organization_id:
            raise ForbiddenException(
                message_key="errors.forbidden",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        # Get organization details when needed for validation and email
        organization_data = await self.organisation_repository.get_organisation_by_id(
            organization_id
        )
        if not organization_data:
            raise NotFoundException(
                message_key="invitations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        # Check organization capacity
        await validate_organization_subscription(organization_data)

        # Check if user is already a member
        existing_member = await self.invite_repository.check_user_membership(
            organization_id, body.email
        )
        if existing_member:
            raise ConflictException(
                message_key="invitations.errors.user_already_a_member",
                custom_code=CustomStatusCode.CONFLICT,
            )

        # Check for existing pending invitation
        existing_invite = await self.invite_repository.check_existing_invite(
            organization_id, body.email
        )
        if existing_invite:
            raise ConflictException(
                message_key="invitations.errors.pending_invitation_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

        # Generate invite token
        invite_token = secrets.token_urlsafe(32)
        token_hash = hash_token(invite_token)
        expires_at = datetime.now() + timedelta(days=INVITE_EXPIRY_DAYS)

        invite_data = {
            "organization_id": organization_id,
            "email": body.email,
            "role_id": str(body.role_id),
            "token_hash": token_hash,
            "invited_by": self.user_context.user_id,
            "status": "pending",
            "expires_at": expires_at,
            "metadata": {
                "first_name": body.first_name,
                "last_name": body.last_name,
                "phone": body.phone,
                "salutation": body.salutation,
            },
        }

        created_invite = await self.invite_repository.create_invite(invite_data)

        # Generate invitation URL
        invite_url = f"{BASE_URL.rstrip('/')}/invite/accept/?token={token_hash}&page=invite-user"

        role_row = await self.role_repository.get_role_by_id(str(body.role_id), organization_id)
        if not role_row:
            raise NotFoundException(
                message_key="invitations.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        role_data = dict(role_row)

        inviter = await get_user_by_id(self.user_context.user_id)

        invitee_full_name = build_full_name(body.salutation, body.first_name, body.last_name)

        user_meta = inviter.user.user_metadata or {}
        inviter_full_name = build_full_name(
            user_meta.get("salutation"),
            user_meta.get("first_name"),
            user_meta.get("last_name"),
        )

        # Send invitation email
        expires_at_str = (
            created_invite["expires_at"].isoformat()
            if isinstance(created_invite["expires_at"], datetime)
            else str(created_invite["expires_at"])
        )
        send_organization_invitation_email(
            email=body.email,
            organization_name=organization_data["name"],
            inviter_name=inviter_full_name.strip(),
            invitee_name=invitee_full_name.strip(),
            invite_url=invite_url,
            role_name=role_data["name"],
            expires_at=expires_at_str,
        )

        return {
            "invite_id": created_invite["id"],
            "invite_url": invite_url,
            "email": body.email,
            "expires_at": created_invite["expires_at"],
        }

    async def get_organization_invitations(
        self,
        organization_id: str,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Get list of all invitations for an organization with pagination."""
        # Validate organization ID format
        validate_uuid_format(organization_id, "organization ID")

        if not self.user_context.organization_id == organization_id:
            raise ForbiddenException(
                message_key="errors.forbidden",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        # Execute queries and get results
        invitations_data = await self.invite_repository.get_organization_invites(
            organization_id=organization_id,
            limit=page_size,
            offset=(page - 1) * page_size,
            status=status,
        )

        invitations_list = [build_invite_list_item(invite) for invite in invitations_data]

        total_count = await self.invite_repository.get_organization_invites_count(
            organization_id=organization_id, status=status
        )

        return {
            "items": invitations_list,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
        }

    async def resend_invitation(self, invite_id: str) -> dict[str, Any]:
        """Resend an organization invitation."""
        # Get invitation details
        invitation_data = await self.invite_repository.get_invite_by_id(invite_id)

        if not invitation_data:
            raise NotFoundException(
                message_key="invitations.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if not self.user_context.organization_id == str(invitation_data["organization_id"]):
            raise ForbiddenException(
                message_key="errors.forbidden",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        # Get organization details when needed for email
        organization_data = await self.organisation_repository.get_organisation_by_id(
            invitation_data["organization_id"]
        )
        if not organization_data:
            raise NotFoundException(
                message_key="invitations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Generate invitation URL
        invite_url = (
            f"{BASE_URL.rstrip('/')}/invite/accept/"
            f"?token={invitation_data['token_hash']}&page=invite-user"
        )

        role_row = await self.role_repository.get_role_by_id(
            invitation_data["role_id"], organization_data["id"]
        )

        if not role_row:
            raise NotFoundException(
                message_key="invitations.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        role_data = dict(role_row)

        inviter = await get_user_by_id(str(invitation_data["invited_by"]))

        inv_meta = invitation_data.get("metadata", {})
        if isinstance(inv_meta, str):
            import json

            inv_meta = json.loads(inv_meta) if inv_meta else {}

        invitee_full_name = build_full_name(
            inv_meta.get("salutation"),
            inv_meta.get("first_name"),
            inv_meta.get("last_name"),
        )

        user_meta = inviter.user.user_metadata or {}
        inviter_full_name = build_full_name(
            user_meta.get("salutation"),
            user_meta.get("first_name"),
            user_meta.get("last_name"),
        )

        # Send invitation email
        expires_at_str = (
            invitation_data["expires_at"].isoformat()
            if isinstance(invitation_data["expires_at"], datetime)
            else str(invitation_data["expires_at"])
        )
        send_organization_invitation_email(
            email=invitation_data["email"],
            organization_name=organization_data["name"],
            inviter_name=inviter_full_name.strip(),
            invitee_name=invitee_full_name.strip(),
            invite_url=invite_url,
            role_name=role_data["name"],
            expires_at=expires_at_str,
        )

        return {
            "invite_id": invite_id,
            "invite_url": invite_url,
            "email": invitation_data["email"],
            "expires_at": invitation_data["expires_at"],
        }

    async def delete_invitation(self, invite_id: str) -> None:
        """Delete an organization invitation."""
        await self.invite_repository.delete_invite(invite_id, self.user_context.organization_id)

    async def _add_user_to_organization(
        self,
        organization_id: str,
        invite_data: dict[str, Any],
        email: str,
        role_id: str,
        role_name: str,
        invited_by: str,
        isometrik_credentials: dict[str, Any],
    ) -> dict[str, Any]:
        """Add user to organization as a member."""
        isometrik_user_id = None
        if is_isometrik_enabled():
            isometrik_response = await create_isometrik_user(
                user_id=invite_data["user_id"],
                first_name=invite_data.get("first_name", None),
                last_name=invite_data.get("last_name", None),
                email=email,
                isometrik_credentials=isometrik_credentials,
                organization_id=organization_id,
                role="member",
            )
            if isometrik_response:
                isometrik_user_id = isometrik_response.get("userId", None)

                if not isometrik_user_id:
                    raise ServiceUnavailableException(
                        message_key="errors.isometrik.failed_to_create_user",
                        custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
                    )

        if isometrik_user_id:
            invite_data["isometrik_user_id"] = isometrik_user_id

        member_record = {
            "user_id": invite_data["user_id"],
            "email": email,
            "first_name": invite_data.get("first_name", None),
            "last_name": invite_data.get("last_name", None),
            "phone": invite_data.get("phone", None),
            "timezone": invite_data.get("timezone", "UTC"),
            "salutation": invite_data.get("salutation", None),
            "role_id": role_id,
            "role": role_name,
            "status": "active",
            "invited_by": invited_by,
            "isometrik_user_id": invite_data.get("isometrik_user_id", None),
        }

        result = await self.organisation_member_repository.add_member(
            organization_id=organization_id, member_data=member_record
        )

        return result
