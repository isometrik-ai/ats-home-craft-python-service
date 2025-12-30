"""Service for invite business logic."""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from supabase import AsyncClient

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
from apps.user_service.app.utils.common_utils import (
    UserContext,
    hash_token,
    validate_uuid_format,
)
from apps.user_service.app.utils.email_utils import send_organization_invitation_email
from apps.user_service.app.utils.user_utils import build_full_name
from libs.shared_db.supabase_db.auth_repository import (
    get_user_by_id,
    sign_up_supabase_user,
)
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
        sb_client: AsyncClient | None = None,
    ) -> None:
        self.user_context = user_context
        self.db_connection = db_connection
        self.invite_repository = InviteRepository(db_connection=db_connection)
        self.organisation_repository = OrganisationRepository(db_connection=db_connection)
        self.role_repository = RoleRepository(db_connection=db_connection)
        self.organisation_member_repository = OrganisationMemberRepository(
            db_connection=db_connection
        )
        self.supabase_client = sb_client

    def _generate_invite_token(self) -> tuple[str, str]:
        """Generate a fresh invite token and its hash.

        Returns:
            tuple[str, str]: A tuple of (invite_token, token_hash)
        """
        invite_token = secrets.token_urlsafe(32)
        token_hash = hash_token(invite_token)
        return invite_token, token_hash

    def _parse_json_field(self, field_value: str | dict[str, Any] | None) -> dict[str, Any]:
        """Parse a JSON field that may be a string or dict.

        Args:
            field_value: The field value that may be a JSON string or dict

        Returns:
            dict[str, Any]: Parsed dictionary, empty dict if None or invalid
        """
        if field_value is None:
            return {}
        if isinstance(field_value, dict):
            return field_value
        if isinstance(field_value, str):
            return json.loads(field_value) if field_value else {}
        return {}

    def _generate_invite_url(self, invite_token: str) -> str:
        """Generate invitation URL from token.

        Args:
            invite_token: The invitation token

        Returns:
            str: The complete invitation URL
        """
        return f"{BASE_URL.rstrip('/')}/invite/accept/?token={invite_token}&page=invite-user"

    def _format_datetime_iso(self, dt: datetime | Any) -> str:
        """Format datetime to ISO string.

        Args:
            dt: Datetime object or any value

        Returns:
            str: ISO formatted string or string representation
        """
        if isinstance(dt, datetime):
            return dt.isoformat()
        return str(dt)

    async def _get_role_data(self, role_id: str, organization_id: str) -> dict[str, Any]:
        """Get role data by ID and validate it exists.

        Args:
            role_id: The role ID
            organization_id: The organization ID

        Returns:
            dict[str, Any]: Role data dictionary

        Raises:
            NotFoundException: If role is not found
        """
        role_row = await self.role_repository.get_role_by_id(role_id, organization_id)
        if not role_row:
            raise NotFoundException(
                message_key="invitations.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return dict(role_row)

    def _build_full_name_from_metadata(self, metadata: dict[str, Any] | None) -> str:
        """Build full name from user metadata dictionary.

        Args:
            metadata: User metadata dictionary containing salutation, first_name, last_name

        Returns:
            str: Full name string
        """
        if not metadata:
            return ""
        return build_full_name(
            metadata.get("salutation"),
            metadata.get("first_name"),
            metadata.get("last_name"),
        )

    def _build_full_name_from_user_metadata(self, user_metadata: dict[str, Any] | None) -> str:
        """Build full name from user's user_metadata field.

        Args:
            user_metadata: User's user_metadata field

        Returns:
            str: Full name string
        """
        if not user_metadata:
            return ""
        return build_full_name(
            user_metadata.get("salutation"),
            user_metadata.get("first_name"),
            user_metadata.get("last_name"),
        )

    def _validate_invitation_for_acceptance(
        self, invitation_data: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Validate invitation exists, is pending, and not expired.

        Args:
            invitation_data: Invitation data dictionary or None

        Returns:
            dict[str, Any]: Validated invitation data

        Raises:
            NotFoundException: If invitation is invalid, not pending, or expired
        """
        if not invitation_data:
            raise NotFoundException(
                message_key="invitations.errors.invitation_invalid_or_expired",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if invitation_data.get("status") != "pending":
            raise NotFoundException(
                message_key="invitations.errors.invitation_invalid_or_expired",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        expires_at = invitation_data.get("expires_at")
        if expires_at and isinstance(expires_at, datetime):
            if expires_at <= datetime.now(timezone.utc):
                raise NotFoundException(
                    message_key="invitations.errors.invitation_invalid_or_expired",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        return invitation_data

    async def accept_and_set_password(
        self, body: InviteAcceptBySettingPasswordRequest
    ) -> dict[str, Any]:
        """Accept an organization invitation by setting password."""
        # Get invitation details by token with row locking for atomic acceptance
        token_hash = hash_token(body.token)
        invitation_data = await self.invite_repository.get_invite_by_token(
            token_hash, for_update=True
        )
        invitation_data = self._validate_invitation_for_acceptance(invitation_data)

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

        role_data = await self._get_role_data(
            invitation_data["role_id"], invitation_data["organization_id"]
        )

        inv_meta = self._parse_json_field(invitation_data.get("metadata"))

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
            ),
            self.supabase_client,
        )

        if not signup_result:
            raise InternalServerErrorException(
                message_key="errors.internal_server_error",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )
        # Get isometrik credentials from organization settings
        org_settings = self._parse_json_field(organization_data.get("settings"))
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
        await self.validate_organization_subscription(organization_data)

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
        invite_token, token_hash = self._generate_invite_token()
        expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)

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
        invite_url = self._generate_invite_url(invite_token)

        role_data = await self._get_role_data(str(body.role_id), organization_id)

        inviter = await get_user_by_id(self.supabase_client, self.user_context.user_id)

        invitee_full_name = build_full_name(body.salutation, body.first_name, body.last_name)

        # Access user_metadata from the returned dictionary
        inviter_user_metadata = inviter.get("user_metadata", {})
        inviter_full_name = self._build_full_name_from_user_metadata(inviter_user_metadata)

        # Send invitation email
        expires_at_str = self._format_datetime_iso(created_invite["expires_at"])
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

        invitations_list = [self.build_invite_list_item(invite) for invite in invitations_data]

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

        role_data = await self._get_role_data(invitation_data["role_id"], organization_data["id"])

        inviter = await get_user_by_id(self.supabase_client, str(invitation_data["invited_by"]))

        inv_meta = self._parse_json_field(invitation_data.get("metadata"))
        invitee_full_name = self._build_full_name_from_metadata(inv_meta)

        # Access user_metadata from the returned dictionary
        inviter_user_metadata = inviter.get("user_metadata", {})
        inviter_full_name = self._build_full_name_from_user_metadata(inviter_user_metadata)

        # Generate fresh token and extend expiration date when resending
        invite_token, token_hash = self._generate_invite_token()
        new_expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_EXPIRY_DAYS)
        updated_invitation = await self.invite_repository.update_invite_token_and_expiration(
            invite_id, token_hash, new_expires_at
        )

        if not updated_invitation:
            raise NotFoundException(
                message_key="invitations.errors.invitation_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Generate invitation URL with new token
        invite_url = self._generate_invite_url(invite_token)

        # Send invitation email with new expiration date
        expires_at_str = self._format_datetime_iso(new_expires_at)
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
            "expires_at": new_expires_at,
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

    async def validate_organization_subscription(self, organization_data: dict[str, Any]) -> bool:
        """Validate whether the organization has a valid subscription.

        Args:
            organization_data (dict): Organization data

        Returns:
            bool: True if organization has a valid subscription

        Raises:
            ForbiddenException: If subscription is missing or expired
            ConflictException: If max users limit is exceeded
        """
        organization_id = organization_data["id"]
        subscription_raw = organization_data.get("subscription")

        if not subscription_raw:
            raise ForbiddenException(
                message_key="invitations.errors.organization_subscription_missing",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        # Parse subscription if it's a JSON string
        if isinstance(subscription_raw, str):
            try:
                subscription = json.loads(subscription_raw)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ForbiddenException(
                    message_key="invitations.errors.organization_subscription_missing",
                    custom_code=CustomStatusCode.FORBIDDEN,
                ) from exc
        else:
            subscription = subscription_raw

        max_users = subscription.get("max_users")
        subscription_end = subscription.get("end_date")

        # Parse end date safely
        try:
            end_date = datetime.fromisoformat(subscription_end)
        except ValueError as exc:
            raise ForbiddenException(
                message_key="invitations.errors.subscription_expired",
                custom_code=CustomStatusCode.FORBIDDEN,
            ) from exc

        # Make datetime timezone-aware
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        total_members = await self.organisation_member_repository.get_users_total_count(
            organization_id=organization_id,
            search=None,
        )

        # Subscription expired
        if datetime.now(timezone.utc) > end_date:
            raise ForbiddenException(
                message_key="invitations.errors.subscription_expired",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        # Max capacity exceeded
        if total_members >= max_users:
            raise ConflictException(
                message_key="invitations.errors.invalid_max_users",
                custom_code=CustomStatusCode.CONFLICT,
            )

        return True

    def build_invite_list_item(self, invite_data: dict[str, Any]) -> dict[str, Any]:
        """Build invitation list item for API response.

        Args:
            invite_data (dict): Invitation data from database

        Returns:
            dict: Formatted invitation list item
        """
        # Handle metadata - it might be a JSON string or a dict
        metadata = invite_data.get("metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}
        elif not isinstance(metadata, dict):
            metadata = {}

        return {
            "invite_id": str(invite_data.get("id")),
            "email": invite_data.get("email"),
            "role_id": str(invite_data.get("role_id")),
            "status": invite_data.get("status"),
            "invited_by": str(invite_data.get("invited_by")),
            "expires_at": invite_data.get("expires_at"),
            "created_at": invite_data.get("created_at"),
            "updated_at": invite_data.get("updated_at"),
            "salutation": metadata.get("salutation", None),
            "first_name": metadata.get("first_name", None),
            "last_name": metadata.get("last_name", None),
            "phone": metadata.get("phone", None),
        }
