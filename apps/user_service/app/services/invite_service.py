"""Service for invite business logic."""

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple
from uuid import uuid4

import asyncpg
from supabase import AsyncClient, AuthApiError

from apps.user_service.app.config.app_settings import app_settings
from apps.user_service.app.db.repositories import (
    InviteRepository,
    OrganizationMemberRepository,
    OrganizationRepository,
    PatchPendingInviteResult,
    RoleRepository,
    TeamRepository,
    UserRepository,
)
from apps.user_service.app.schemas.auth import SignupRequest
from apps.user_service.app.schemas.enums import (
    INVITE_ACCEPT_SUCCESS_MESSAGE_KEYS,
    InviteAcceptAuthKind,
    InviteStatus,
    OrganizationMemberRole,
    OrganizationMemberStatus,
)
from apps.user_service.app.schemas.invites import (
    InviteAcceptBySettingPasswordRequest,
    InviteAcceptResponse,
    InviteCreateRequest,
    InvitedUserInfo,
    PatchInviteRequest,
)
from apps.user_service.app.schemas.teams import MemberData
from apps.user_service.app.services.session_management_service import (
    SessionManagementService,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    hash_token,
    validate_uuid_format,
)
from apps.user_service.app.utils.email_utils import send_organization_invitation_email
from apps.user_service.app.utils.user_utils import build_full_name
from libs.shared_db.supabase_db.auth_repository import (
    generate_magiclink_and_exchange_for_session,
    get_user_by_id,
    login_user,
    sign_up_supabase_user,
)
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    GoneException,
    InternalServerErrorException,
    NotFoundException,
    ServiceUnavailableException,
)
from libs.shared_utils.isometrik_service import create_isometrik_user
from libs.shared_utils.status_codes import CustomStatusCode


def _invite_row_audit_fields(invitation_data: dict[str, Any]) -> dict[str, Any]:
    """JSON-friendly snapshot of invite row fields for audit (no raw token)."""

    def _iso(val: Any) -> Any:
        if val is None:
            return None
        if hasattr(val, "isoformat"):
            return val.isoformat()
        return val

    return {
        "invite_id": str(invitation_data["id"]),
        "organization_id": str(invitation_data["organization_id"]),
        "email": invitation_data.get("email"),
        "role_id": str(invitation_data["role_id"]) if invitation_data.get("role_id") else None,
        "status": invitation_data.get("status"),
        "invited_by": str(invitation_data["invited_by"])
        if invitation_data.get("invited_by")
        else None,
        "expires_at": _iso(invitation_data.get("expires_at")),
        "created_at": _iso(invitation_data.get("created_at")),
        "updated_at": _iso(invitation_data.get("updated_at")),
    }


class InviteAcceptOutcome(NamedTuple):
    """Result of accepting an invite: payload and the success message key for the API layer."""

    response: InviteAcceptResponse
    message_key: str
    audit_user_context: dict[str, str]
    audit_old: dict[str, Any]
    audit_new: dict[str, Any]
    audit_record_id: str


class InviteService:
    """Service for invite business logic.

    User context is provided during initialization.
    """

    def __init__(
        self,
        user_context: UserContext | None,
        db_connection: asyncpg.Connection,
        sb_admin_client: AsyncClient | None = None,
        sb_anon_client: AsyncClient | None = None,
    ) -> None:
        self.user_context = user_context
        self.db_connection = db_connection
        self.invite_repository = InviteRepository(db_connection=db_connection)
        self.organization_repository = OrganizationRepository(db_connection=db_connection)
        self.role_repository = RoleRepository(db_connection=db_connection)
        self.organization_member_repository = OrganizationMemberRepository(
            db_connection=db_connection
        )
        self.user_repository = UserRepository(db_connection=db_connection)
        self.team_repository = TeamRepository(db_connection=db_connection)
        self.session_management_service = SessionManagementService(db_connection=db_connection)
        self.supabase_admin_client = sb_admin_client
        self.supabase_anon_client = sb_anon_client

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
        return (
            f"{app_settings.shared_settings.website_url.rstrip('/')}"
            f"/invite/accept/?token={invite_token}&page=invite-user"
        )

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

    async def _validate_team_in_org(self, team_id: str, organization_id: str) -> None:
        """Ensure the team exists and belongs to the organization.

        Raises:
            NotFoundException: If the team is missing or not in the organization.
        """
        validate_uuid_format(team_id, "team ID")
        team_data, _ = await self.team_repository.get_team_detail(team_id, organization_id)
        if not team_data:
            raise NotFoundException(
                message_key="teams.errors.team_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    def _build_invite_metadata(self, body: InviteCreateRequest) -> dict[str, Any]:
        """Build invitation metadata from the create request."""
        metadata: dict[str, Any] = {
            "first_name": body.first_name,
            "last_name": body.last_name,
            "phone_number": body.phone_number,
            "phone_isd_code": body.phone_isd_code,
            "salutation": body.salutation,
        }
        if body.team_id:
            metadata["team_id"] = str(body.team_id)
        if body.tags is not None:
            metadata["tags"] = [tag.strip() for tag in body.tags if tag and tag.strip()]
        return metadata

    async def _add_invitee_to_team(
        self,
        *,
        team_id: str | None,
        organization_id: str,
        user_id: str,
        added_by: str,
    ) -> None:
        """Add an accepted invitee to a team when team_id was set on the invitation."""
        if not team_id:
            return

        team_data, _ = await self.team_repository.get_team_detail(team_id, organization_id)
        if not team_data:
            return

        await self.team_repository._insert_team_members(  # pylint: disable=protected-access
            team_id=team_id,
            member_data=[MemberData(member_id=user_id, additional_data=None)],
            added_by=added_by,
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
            ConflictException: If invitation has already been accepted (409)
            GoneException: If invitation is invalid, expired, or revoked (410)
        """
        if not invitation_data:
            raise GoneException(
                message_key="invitations.errors.invitation_invalid_or_expired",
                custom_code=CustomStatusCode.GONE,
            )

        status = invitation_data.get("status")

        # Check if invitation has already been accepted
        if status == InviteStatus.ACCEPTED.value:
            raise ConflictException(
                message_key="invitations.errors.invitation_already_accepted",
                custom_code=CustomStatusCode.CONFLICT,
            )

        # Check if invitation has expired based on expiration date
        expires_at = invitation_data.get("expires_at")
        if expires_at and isinstance(expires_at, datetime):
            if expires_at <= datetime.now(timezone.utc):
                raise GoneException(
                    message_key="invitations.errors.invitation_invalid_or_expired",
                    custom_code=CustomStatusCode.GONE,
                )

        return invitation_data

    def _extract_invite_metadata(
        self, invitation_data: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None, str | None]:
        """Extract metadata, phone number, and ISD code from invitation data.

        Args:
            invitation_data: Invitation data dictionary

        Returns:
            tuple: (metadata dict, phone_number, phone_isd_code)
        """
        inv_meta = self._parse_json_field(invitation_data.get("metadata"))
        phone_number = inv_meta.get("phone_number", None)
        phone_isd_code = inv_meta.get("phone_isd_code", None)
        return inv_meta, phone_number, phone_isd_code

    def _build_signup_request_from_invite(
        self,
        email: str,
        password: str,
        inv_meta: dict[str, Any],
        phone_number: str | None,
        phone_isd_code: str | None,
    ) -> SignupRequest:
        """Build SignupRequest from invitation metadata.

        Args:
            email: User email
            password: User password
            inv_meta: Invitation metadata dictionary
            phone_number: Phone number
            phone_isd_code: Phone ISD code

        Returns:
            SignupRequest: Signup request object
        """
        return SignupRequest(
            email=email,
            password=password,
            first_name=inv_meta.get("first_name", None),
            last_name=inv_meta.get("last_name", None),
            phone_number=phone_number,
            phone_isd_code=phone_isd_code,
            timezone="UTC",
            salutation=inv_meta.get("salutation", None),
            verification_id="",
            verification_code="",
        )

    async def _authenticate_existing_user(
        self, email: str, password: str
    ) -> Any:  # Returns auth result
        """Authenticate an existing user with email and password.

        Args:
            email: User email
            password: User password

        Returns:
            Auth result from Supabase

        Raises:
            BadRequestException: If authentication fails
            InternalServerErrorException: If auth result is invalid
        """
        try:
            auth_result = await login_user(
                email=email, password=password, sb_client=self.supabase_anon_client
            )
        except AuthApiError as login_error:
            if login_error.status == 400:
                raise BadRequestException(
                    message_key="auth.errors.invalid_credentials",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                ) from login_error
            raise BadRequestException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            ) from login_error
        except Exception as login_error:
            raise BadRequestException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            ) from login_error

        if not auth_result or not auth_result.user:
            raise InternalServerErrorException(
                message_key="errors.internal_server_error",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        return auth_result

    async def _signup_new_user(self, signup_request: SignupRequest) -> Any:  # Returns auth result
        """Create a new user account.

        Args:
            signup_request: Signup request data

        Returns:
            Auth result from Supabase

        Raises:
            BadRequestException: If signup fails
            InternalServerErrorException: If auth result is invalid
        """
        try:
            auth_result = await sign_up_supabase_user(signup_request, self.supabase_anon_client)
        except AuthApiError as signup_error:
            raise BadRequestException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            ) from signup_error

        if not auth_result:
            raise InternalServerErrorException(
                message_key="errors.internal_server_error",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        return auth_result

    async def _authenticate_or_signup_user(
        self,
        email: str,
        password: str | None,
        inv_meta: dict[str, Any],
        phone_number: str | None,
        phone_isd_code: str | None,
    ) -> tuple[Any, InviteAcceptAuthKind]:
        """Authenticate existing user or create new user account.

        Args:
            email: User email
            password: User password
            inv_meta: Invitation metadata
            phone_number: Phone number
            phone_isd_code: Phone ISD code

        Returns:
            Auth result from Supabase and which auth path was used (for API messaging).
        """
        # Check if user already exists in the auth system
        existing_auth_user = await self.user_repository.get_auth_user_by_email(email)

        if existing_auth_user:
            has_password = bool(existing_auth_user.get("encrypted_password"))
            if has_password:
                if not password:
                    raise BadRequestException(
                        message_key="auth.errors.password_required",
                        custom_code=CustomStatusCode.BAD_REQUEST,
                    )
                # User already exists, authenticate them with the provided password
                auth_result = await self._authenticate_existing_user(email, password)
                return auth_result, InviteAcceptAuthKind.EXISTING_WITH_PASSWORD

            if not self.supabase_admin_client or not self.supabase_anon_client:
                raise ServiceUnavailableException(
                    message_key="errors.service_unavailable",
                    custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
                )
            auth_result = await generate_magiclink_and_exchange_for_session(
                admin_client=self.supabase_admin_client,
                email=email,
            )
            return auth_result, InviteAcceptAuthKind.EXISTING_PASSWORDLESS

        # User doesn't exist, create a new account
        if not password:
            raise BadRequestException(
                message_key="auth.errors.password_required",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )
        signup_request = self._build_signup_request_from_invite(
            email, password, inv_meta, phone_number, phone_isd_code
        )
        auth_result = await self._signup_new_user(signup_request)
        return auth_result, InviteAcceptAuthKind.NEW_SIGNUP

    def _build_invite_accept_response(
        self,
        session: Any,
        user: Any,
        user_metadata: dict[str, Any],
        organization_id: str,
    ) -> InviteAcceptResponse:
        """Build InviteAcceptResponse from authentication data.

        Args:
            session: Supabase session
            user: Supabase user
            user_metadata: User metadata dictionary
            organization_id: Organization ID

        Returns:
            InviteAcceptResponse: Authentication response
        """
        return InviteAcceptResponse(
            access_token=session.access_token,
            refresh_token=getattr(session, "refresh_token", None),
            expires_in=getattr(session, "expires_in", None),
            expires_at=getattr(session, "expires_at", None),
            user=InvitedUserInfo(
                id=getattr(user, "id", None),
                email=getattr(user, "email", None),
                first_name=user_metadata.get("first_name", None),
                last_name=user_metadata.get("last_name", None),
                phone_number=user_metadata.get("phone_number", None),
                phone_isd_code=user_metadata.get("phone_isd_code", None),
                timezone=user_metadata.get("timezone", None),
                organization_id=organization_id,
            ),
        )

    async def accept_and_set_password(
        self, body: InviteAcceptBySettingPasswordRequest
    ) -> InviteAcceptOutcome:
        """Accept an organization invitation by setting password."""
        # Get invitation details by token with row locking for atomic acceptance
        token_hash = hash_token(body.token)
        invitation_data = await self.invite_repository.get_invite_by_token(
            token_hash, for_update=True
        )
        invitation_data = self._validate_invitation_for_acceptance(invitation_data)
        audit_old = _invite_row_audit_fields(invitation_data)

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
        organization_data = await self.organization_repository.get_organization_by_id(
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

        # Extract invitation metadata
        inv_meta, phone_number, phone_isd_code = self._extract_invite_metadata(invitation_data)

        # Authenticate existing user or create new user account
        # This allows existing users to accept invitations from new organizations
        auth_result, auth_kind = await self._authenticate_or_signup_user(
            email=invitation_data["email"],
            password=body.password,
            inv_meta=inv_meta,
            phone_number=phone_number,
            phone_isd_code=phone_isd_code,
        )

        session = auth_result.session
        user = auth_result.user
        user_metadata = getattr(user, "user_metadata", {}) or {}

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
                "user_id": user.id,
                "first_name": inv_meta.get("first_name", None),
                "last_name": inv_meta.get("last_name", None),
                "phone_number": phone_number,
                "phone_isd_code": phone_isd_code,
                "timezone": "UTC",
                "salutation": inv_meta.get("salutation", None),
                "tags": inv_meta.get("tags") or [],
            },
            email=invitation_data["email"],
            role_data={"id": invitation_data["role_id"], "name": role_data["name"]},
            member_role=OrganizationMemberRole.MEMBER.value,
            invited_by=invitation_data["invited_by"],
            isometrik_credentials=isometrik_credentials,
        )

        await self._add_invitee_to_team(
            team_id=inv_meta.get("team_id"),
            organization_id=str(invitation_data["organization_id"]),
            user_id=str(user.id),
            added_by=str(invitation_data["invited_by"]),
        )

        # Update invitation status
        await self.invite_repository.update_invite_status(
            invitation_data["id"], InviteStatus.ACCEPTED.value, user.id
        )

        # Increment subscription users after successful membership creation
        org_id = invitation_data["organization_id"]
        await self.organization_repository.update_subscription_users(org_id)

        response = self._build_invite_accept_response(
            session=session,
            user=user,
            user_metadata=user_metadata,
            organization_id=str(invitation_data["organization_id"]),
        )
        message_key = INVITE_ACCEPT_SUCCESS_MESSAGE_KEYS[auth_kind]

        user_email = (getattr(user, "email", None) or invitation_data.get("email") or "").strip()
        if not user_email:
            user_email = str(invitation_data["email"])

        audit_user_context = {
            "user_id": str(user.id),
            "user_email": user_email,
            "organization_id": str(invitation_data["organization_id"]),
        }
        audit_new = {
            **_invite_row_audit_fields(invitation_data),
            "status": InviteStatus.ACCEPTED.value,
            "accepted_by_user_id": str(user.id),
        }
        return InviteAcceptOutcome(
            response=response,
            message_key=message_key,
            audit_user_context=audit_user_context,
            audit_old=audit_old,
            audit_new=audit_new,
            audit_record_id=str(invitation_data["id"]),
        )

    async def validate_invite_link(self, token: str) -> dict[str, bool]:
        """Validate invite link and check if user is existing.

        Args:
            token: Invite token from the URL

        Returns:
            dict[str, bool]: Dictionary with is_existing_user boolean

        Raises:
            NotFoundException: If invitation is invalid, not pending, or expired
        """
        # Get invitation details by token
        token_hash = hash_token(token)
        invitation_data = await self.invite_repository.get_invite_by_token(token_hash)

        # Validate invitation exists, is pending, and not expired
        invitation_data = self._validate_invitation_for_acceptance(invitation_data)

        # Check if user already exists in the auth system
        email = invitation_data["email"]
        existing_auth_user = await self.user_repository.get_auth_user_by_email(email)

        has_password = bool(existing_auth_user and existing_auth_user.get("encrypted_password"))
        return {"is_existing_user": existing_auth_user is not None, "has_password": has_password}

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
        organization_data = await self.organization_repository.get_organization_by_id(
            organization_id
        )
        if not organization_data:
            raise NotFoundException(
                message_key="invitations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        # Check organization capacity
        # await self.validate_organization_subscription(organization_data)

        # Check if user is already a member
        existing_member = await self.invite_repository.check_user_membership(
            organization_id, body.email
        )
        if existing_member:
            raise ConflictException(
                message_key="invitations.errors.user_already_a_member",
                custom_code=CustomStatusCode.CONFLICT,
            )

        pending_invite = await self.invite_repository.check_existing_invite(
            organization_id, body.email, status=InviteStatus.PENDING.value
        )
        if pending_invite:
            expires_at_val = pending_invite.get("expires_at")
            if expires_at_val and expires_at_val > datetime.now(timezone.utc):
                raise ConflictException(
                    message_key="invitations.errors.pending_invitation_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                )

        # Validate the role exists for this organization before inserting the invite
        role_data = await self._get_role_data(str(body.role_id), organization_id)

        if body.team_id:
            await self._validate_team_in_org(str(body.team_id), organization_id)

        # Generate invite token
        invite_token, token_hash = self._generate_invite_token()
        expires_at = datetime.now(timezone.utc) + timedelta(days=app_settings.invite_expiry_days)

        metadata = self._build_invite_metadata(body)

        if pending_invite:
            created_invite = await self.invite_repository.renew_expired_invite(
                str(pending_invite["id"]),
                {
                    "role_id": str(body.role_id),
                    "token_hash": token_hash,
                    "invited_by": self.user_context.user_id,
                    "expires_at": expires_at,
                    "metadata": metadata,
                },
            )
            if not created_invite:
                raise InternalServerErrorException(
                    message_key="errors.internal_server_error",
                    custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
                )
        else:
            invite_data = {
                "organization_id": organization_id,
                "email": body.email,
                "role_id": str(body.role_id),
                "token_hash": token_hash,
                "invited_by": self.user_context.user_id,
                "status": InviteStatus.PENDING.value,
                "expires_at": expires_at,
                "metadata": metadata,
            }
            created_invite = await self.invite_repository.create_invite(invite_data)

        # Generate invitation URL
        invite_url = self._generate_invite_url(invite_token)

        inviter = await get_user_by_id(self.supabase_admin_client, self.user_context.user_id)

        invitee_full_name = build_full_name(body.salutation, body.first_name, body.last_name)

        # Access user_metadata from the returned dictionary
        inviter_user_metadata = inviter.get("user_metadata", {})
        inviter_full_name = build_full_name(
            inviter_user_metadata.get("salutation"),
            inviter_user_metadata.get("first_name"),
            inviter_user_metadata.get("last_name"),
        )

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
        organization_data = await self.organization_repository.get_organization_by_id(
            invitation_data["organization_id"]
        )
        if not organization_data:
            raise NotFoundException(
                message_key="invitations.errors.organization_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        role_data = await self._get_role_data(invitation_data["role_id"], organization_data["id"])

        inviter = await get_user_by_id(
            self.supabase_admin_client, str(invitation_data["invited_by"])
        )

        inv_meta = self._parse_json_field(invitation_data.get("metadata"))
        invitee_full_name = build_full_name(
            inv_meta.get("salutation"), inv_meta.get("first_name"), inv_meta.get("last_name")
        )

        # Access user_metadata from the returned dictionary
        inviter_user_metadata = inviter.get("user_metadata", {})
        inviter_full_name = build_full_name(
            inviter_user_metadata.get("salutation"),
            inviter_user_metadata.get("first_name"),
            inviter_user_metadata.get("last_name"),
        )

        # Generate fresh token and extend expiration date when resending
        invite_token, token_hash = self._generate_invite_token()
        new_expires_at = datetime.now(timezone.utc) + timedelta(
            days=app_settings.invite_expiry_days
        )
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

    async def patch_invitation(
        self, invite_id: str, body: PatchInviteRequest
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Update the RBAC role on a pending invitation (validated in one DB round-trip).

        Returns ``(audit_old_data, audit_new_data)`` snapshots for ``raw_audit_*``; the HTTP
        layer does not expose invite payload in the response.
        """

        organization_id = self.user_context.organization_id if self.user_context else None
        new_role_id = str(body.role_id)

        outcome: PatchPendingInviteResult = await self.invite_repository.patch_pending_invitation(
            invite_id,
            organization_id,
            InviteStatus.PENDING.value,
            role_id=new_role_id,
        )
        if outcome.updated_row is not None:
            old_role = outcome.previous_role_id or ""
            audit_old = {"invite_id": invite_id, "role_id": old_role}
            audit_new = {"invite_id": invite_id, "role_id": new_role_id}
            return audit_old, audit_new

        if outcome.invite_ok and not outcome.role_ok:
            raise NotFoundException(
                message_key="invitations.errors.role_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        raise NotFoundException(
            message_key="invitations.errors.invitation_not_found",
            custom_code=CustomStatusCode.NOT_FOUND,
        )

    async def delete_invitation(self, invite_id: str) -> None:
        """Delete an organization invitation."""
        await self.invite_repository.delete_invite(invite_id, self.user_context.organization_id)

    async def _add_user_to_organization(
        self,
        organization_id: str,
        invite_data: dict[str, Any],
        email: str,
        role_data: dict[str, str],
        member_role: str,
        invited_by: str,
        isometrik_credentials: dict[str, Any],
    ) -> None:
        """Add user to organization as a member."""
        member_id = str(uuid4())
        isometrik_user_id = None
        isometrik_response = await create_isometrik_user(
            user={
                "user_id": member_id,
                "first_name": invite_data.get("first_name", None),
                "last_name": invite_data.get("last_name", None),
                "email": email,
                "organization_id": organization_id,
                "role": OrganizationMemberRole.MEMBER.value,
            },
            isometrik_credentials=isometrik_credentials,
        )
        if isometrik_response:
            isometrik_user_id = isometrik_response.get("userId", None)

            if not isometrik_user_id:
                raise ServiceUnavailableException(
                    message_key="errors.isometrik.failed_to_create_user",
                    custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
                )

        member_record = {
            "id": member_id,
            "user_id": invite_data["user_id"],
            "email": email,
            "first_name": invite_data.get("first_name", None),
            "last_name": invite_data.get("last_name", None),
            "phone_number": invite_data.get("phone_number", None),
            "phone_isd_code": invite_data.get("phone_isd_code", None),
            "timezone": invite_data.get("timezone", "UTC"),
            "salutation": invite_data.get("salutation", None),
            "role_id": role_data["id"],
            "role": role_data["name"],
            "member_role": member_role,
            "status": OrganizationMemberStatus.ACTIVE.value,
            "invited_by": invited_by,
            "isometrik_user_id": isometrik_user_id,
            "tags": invite_data.get("tags") or [],
        }

        await self.organization_member_repository.add_member(
            organization_id=organization_id, member_data=member_record
        )

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

        total_members = await self.organization_member_repository.get_users_total_count(
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

        # Get phone_number and phone_isd_code from metadata and merge for output
        phone_number_db = metadata.get("phone_number", None)
        phone_isd_code = metadata.get("phone_isd_code")
        phone_full = None
        if phone_number_db and phone_isd_code:
            phone_full = f"{phone_isd_code}{phone_number_db}"

        team_id = metadata.get("team_id")
        tags = metadata.get("tags")

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
            "phone": phone_full,
            "team_id": team_id,
            "tags": tags if tags else None,
        }
