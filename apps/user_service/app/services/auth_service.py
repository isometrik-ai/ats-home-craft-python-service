"""Authentication Service Module

This module provides authentication business logic operations.
All business logic for authentication endpoints is centralized here.
"""

# Standard library imports
import json
import re
import time
from typing import Any

# Third-party imports
import asyncpg
from supabase import AsyncClient, AuthApiError

# repositories
from apps.user_service.app.db.repositories import (
    ContactsRepository,
    OrganizationMemberRepository,
    OrganizationRepository,
    SessionRepository,
    UserRepository,
)

# Schema imports
from apps.user_service.app.schemas.auth import (
    AuthLogin,
    AuthResponse,
    ChangePasswordResponse,
    ForgotPasswordResponse,
    PasswordResponse,
    RefreshSessionResponse,
    SelectOrganizationResponse,
    SetPasswordResponse,
    SignupRequest,
    UserInfo,
    ValidateAccountResponse,
    ValidateAccountTrigger,
)
from apps.user_service.app.schemas.common import OrganizationBasicDetails
from apps.user_service.app.schemas.enums import SelectOrganizationType
from apps.user_service.app.schemas.verification_codes import (
    VerificationType,
    VerifyVerificationCodeRequest,
)
from apps.user_service.app.services.verification_code_service import (
    VerificationCodeService,
)

# Email utilities
from apps.user_service.app.utils.email_utils import (
    send_password_change_success_email,
    send_password_reset_success_email,
    send_welcome_email,
)
from apps.user_service.app.utils.user_utils import (
    build_full_name,
    get_isometrik_details,
)

# Shared library imports
from libs.shared_db.supabase_db.auth_repository import (
    delete_user,
    login_user,
    refresh_session,
    send_password_reset_email,
    sign_up_supabase_user,
    update_password_by_user_id,
    update_password_with_link_identity,
)

# Internal utility imports
from libs.shared_middleware.jwt_auth import get_claims_from_token

# Shared exceptions and status codes
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    InternalServerErrorException,
    NotFoundException,
    ServiceUnavailableException,
    TooManyRequestsException,
    UnauthorizedException,
    ValidationException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

# Initialize logger
logger = get_logger("auth-service")


class AuthService:
    """Service for authentication business logic.
    Handles all authentication operations including login, signup, password management, and 2FA.
    """

    def __init__(self, db_connection: asyncpg.Connection, sb_client: AsyncClient | None = None):
        """Initialize AuthService with database connection.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection
        self.user_repository = UserRepository(db_connection=db_connection)
        self.organization_repository = OrganizationRepository(db_connection=db_connection)
        self.supabase_client = sb_client

    # UTILITY METHODS
    @staticmethod
    def _parse_user_metadata(raw_metadata: dict | str | None) -> dict:
        """Parse user metadata from raw_user_meta_data.

        Args:
            raw_metadata: Raw metadata as JSON string from database (or None)

        Returns:
            Parsed metadata dictionary, empty dict if None or invalid JSON
        """
        if not raw_metadata:
            return {}

        # If it's already a dict, return it directly
        if isinstance(raw_metadata, dict):
            return raw_metadata

        try:
            return json.loads(raw_metadata)
        except json.JSONDecodeError:
            return {}

    # PASSWORD VALIDATION METHODS
    @staticmethod
    def _is_password_strong(password: str) -> bool:
        """Check if password is strong.
        Checks This Conditions:
        1. At least 6 characters
        2. At least one uppercase letter
        3. At least one lowercase letter
        4. At least one number
        5. At least one special character

        Args:
            password (str): Password to check

        Returns:
            bool: True if password is strong, False otherwise
        """
        password_pattern = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z0-9]).{6,}$")
        return bool(password_pattern.match(password))

    def _validate_password_strength(self, password: str) -> None:
        """Validate password strength and raise exception if weak.

        Args:
            password: Password to validate

        Raises:
            ValidationException: If password is weak or empty
        """
        if not password or not password.strip():
            raise ValidationException(
                message_key="auth.errors.password_required",
                custom_code=CustomStatusCode.INVALID_DATA,
            )
        if not self._is_password_strong(password):
            raise ValidationException(
                message_key="auth.errors.password_strength",
                custom_code=CustomStatusCode.INVALID_DATA,
            )

    # VERIFICATION CODE METHODS
    async def _validate_verification_code_for_signup(
        self, verification_id: str, email: str, verification_code: str
    ) -> None:
        """Validate verification code for signup (cross-security check).

        Args:
            verification_id: Verification code ID
            email: Email to validate
            verification_code: Verification code to validate

        Raises:
            NotFoundException: If verification code not found
            BadRequestException: If validation fails
        """
        # Use VerificationCodeService for verification operations
        verification_service = VerificationCodeService(db_connection=self.db_connection)

        # Get verification record
        verification_record = (
            await verification_service.verification_code_repository.get_verification_code_by_id(
                verification_id
            )
        )

        if not verification_record:
            raise NotFoundException(
                message_key="verification_codes.errors.verification_code_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if not verification_record.get("verified", False):
            raise BadRequestException(
                message_key="verification_codes.errors.verification_code_not_verified",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        stored_given_input = verification_record.get("given_input")
        if stored_given_input != email:
            raise BadRequestException(
                message_key="auth.errors.verification_code_not_matched_email",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        stored_code = verification_record.get("verification_code")
        if verification_code != stored_code:
            raise BadRequestException(
                message_key="verification_codes.errors.verification_code_invalid",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

    # SESSION MANAGEMENT METHODS
    @staticmethod
    def _extract_session(session: Any) -> Any:
        """Extract session object if available.

        Args:
            session: Session object

        Returns:
            Session object if available, None otherwise
        """
        if session and hasattr(session, "access_token"):
            return session
        return None

    def _get_session_after_signup(self, signup_result: Any) -> Any:
        """Get session after signup, trying signup session first, then login if needed.

        Args:
            signup_result: Result from sign_up_supabase_user

        Returns:
            Session object if available, None otherwise
        """
        session = self._extract_session(signup_result.session)
        if session:
            return session
        return None

    # EMAIL METHODS
    @staticmethod
    def _send_welcome_email_safely(email: str, first_name: str) -> None:
        """Send welcome email safely without failing the signup operation.

        Args:
            email: User email
            first_name: User first name
        """
        try:
            send_welcome_email(email=email, first_name=first_name)
        except Exception as email_error:
            logger.error("Error sending welcome email: %s", str(email_error))

    # ============================================================================
    # 2FA METHODS
    # ============================================================================

    @classmethod
    def _is_2fa_enabled(cls, raw_user_metadata: dict | str | None) -> tuple[bool, dict | None]:
        """Check if 2FA is enabled in user metadata.

        Args:
            raw_user_metadata: Raw user metadata JSON string from database (or None)

        Returns:
            Tuple of (is_enabled, verification_preference_dict)
        """
        parsed_metadata = cls._parse_user_metadata(raw_user_metadata)

        verification_preference = parsed_metadata.get("verification_preference")
        if verification_preference and isinstance(verification_preference, dict):
            enabled = verification_preference.get("enabled", False)
            if enabled is True:
                return True, verification_preference
        return False, None

    @staticmethod
    def _validate_2fa_credentials_required(
        verification_id: str | None, verification_code: str | None
    ) -> None:
        """Validate that 2FA credentials are provided when required.

        Args:
            verification_id: Optional verification code ID
            verification_code: Optional verification code

        Raises:
            BadRequestException: If credentials are missing
        """
        if not verification_id or not verification_code:
            raise BadRequestException(
                message_key="verification_codes.errors.verification_code_invalid",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

    async def _get_and_validate_verification_record(self, verification_id: str) -> dict:
        """Get and validate verification code record.

        Args:
            verification_id: Verification code ID

        Returns:
            Verification record dictionary

        Raises:
            NotFoundException: If record not found
            BadRequestException: If record is invalid
        """
        # Use VerificationCodeService for verification operations
        verification_service = VerificationCodeService(db_connection=self.db_connection)

        verification_record = (
            await verification_service.verification_code_repository.get_verification_code_by_id(
                verification_id
            )
        )

        if not verification_record:
            raise NotFoundException(
                message_key="verification_codes.errors.verification_code_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        stored_given_input = verification_record.get("given_input")
        if not stored_given_input:
            raise BadRequestException(
                message_key="verification_codes.errors.verification_code_invalid",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        return verification_record

    @staticmethod
    def _validate_phone_match(stored_given_input: str, user_phone: str | None) -> None:
        """Validate that stored phone matches user's phone.

        Args:
            stored_given_input: Stored phone from verification record
            user_phone: User's phone number

        Raises:
            BadRequestException: If phones don't match
        """
        if user_phone and stored_given_input != user_phone:
            normalized_stored = stored_given_input.lstrip("+")
            normalized_user = user_phone.lstrip("+")
            if normalized_stored != normalized_user:
                raise BadRequestException(
                    message_key="auth.errors.verification_code_not_matched_phone",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )

    @staticmethod
    def _create_verification_request(
        verification_preference: dict,
        verification_id: str,
        verification_code: str,
        stored_given_input: str,
        email: str,
        phone_number: str | None = None,
        phone_isd_code: str | None = None,
    ) -> VerifyVerificationCodeRequest:
        """Create VerifyVerificationCodeRequest based on verification type.

        Args:
            verification_preference: Verification preference dict
            verification_id: Verification code ID
            verification_code: Verification code
            stored_given_input: Stored email (for EMAIL type) or
                phone (for PHONE type, used for validation only)
            email: User email
            phone_number: Phone number from user_metadata (for PHONE type)
            phone_isd_code: Phone ISD code from user_metadata (for PHONE type)

        Returns:
            VerifyVerificationCodeRequest object

        Raises:
            BadRequestException: If email doesn't match for EMAIL type
        """
        verification_method = verification_preference.get("type", "EMAIL").upper()

        if verification_method == "PHONE":
            if not phone_number or not phone_isd_code:
                raise BadRequestException(
                    message_key="auth.errors.phone_number_missing",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )
            return VerifyVerificationCodeRequest(
                type=VerificationType.PHONE_NUMBER,
                verification_id=verification_id,
                verification_code=verification_code,
                phone_number=phone_number,
                phone_isd_code=phone_isd_code,
            )
        # For email verification, verify that stored_given_input matches user's email
        if stored_given_input.lower() != email.lower():
            raise BadRequestException(
                message_key="auth.errors.verification_code_not_matched_email",
                custom_code=CustomStatusCode.INVALID_DATA,
            )
        return VerifyVerificationCodeRequest(
            type=VerificationType.EMAIL,
            verification_id=verification_id,
            verification_code=verification_code,
            email=stored_given_input,
        )

    async def _verify_2fa_code(
        self,
        verification_record: dict,
        verify_data: VerifyVerificationCodeRequest,
        verification_code: str,
        verification_id: str,
    ) -> None:
        """Verify 2FA code and update record.

        Args:
            verification_record: Verification record dictionary
            verify_data: VerifyVerificationCodeRequest object
            verification_code: Verification code
            verification_id: Verification code ID

        Raises:
            BadRequestException: If verification fails
            GoneException: If verification code expired
        """
        # Use VerificationCodeService for verification operations
        verification_service = VerificationCodeService(db_connection=self.db_connection)

        # Use service's validation method
        verification_service._validate_verification_record(verification_record, verify_data)

        # Use service's verification method
        await verification_service._verify_code_and_update_record(
            verification_record, verification_code, verification_id
        )

    async def _check_and_verify_2fa(
        self,
        raw_user_metadata: dict | str | None,
        verification_id: str | None,
        verification_code: str | None,
        email: str,
        user_phone: str | None = None,
        phone_number: str | None = None,
        phone_isd_code: str | None = None,
    ) -> None:
        """Check if user has 2FA enabled and verify the code if required.

        Args:
            raw_user_metadata: Raw user metadata JSON string from database (or None)
            verification_id: Optional verification code ID
            verification_code: Optional verification code
            email: User email for verification
            user_phone: Optional user phone number
             (for PHONE type verification, used for validation only)
            phone_number: Phone number from user_metadata (for PHONE type verification)
            phone_isd_code: Phone ISD code from user_metadata (for PHONE type verification)

        Raises:
            BadRequestException: If 2FA verification fails
            GoneException: If verification code expired
        """
        is_enabled, verification_preference = self._is_2fa_enabled(raw_user_metadata)
        if not is_enabled:
            return

        self._validate_2fa_credentials_required(verification_id, verification_code)

        verification_record = await self._get_and_validate_verification_record(verification_id)
        stored_given_input = verification_record.get("given_input")

        # Validate phone match if PHONE type
        verification_method = verification_preference.get("type", "EMAIL").upper()
        if verification_method == "PHONE":
            # Validate phone match using stored_given_input
            self._validate_phone_match(stored_given_input, user_phone)

        verify_data = self._create_verification_request(
            verification_preference,
            verification_id,
            verification_code,
            stored_given_input,
            email,
            phone_number=phone_number,
            phone_isd_code=phone_isd_code,
        )

        await self._verify_2fa_code(
            verification_record, verify_data, verification_code, verification_id
        )

    # MAIN SERVICE METHODS
    async def login(
        self,
        data: AuthLogin,
    ) -> AuthResponse:
        """Handle user login with optional 2FA support.

        Security: Password is verified first, then 2FA is checked to prevent timing attacks.

        Args:
            data: Login credentials containing email, password, and optional 2FA fields

        Returns:
            AuthResponse: Access token and user information

        Raises:
            BadRequestException: For invalid credentials or 2FA verification failure
            InternalServerErrorException: For result format issues

        """
        # First verify password to prevent timing attacks
        try:
            result = await login_user(
                email=data.email, password=data.password, sb_client=self.supabase_client
            )
        except AuthApiError as auth_error:
            # Handle specific credential-related errors
            if auth_error.status == 400:
                # Invalid credentials (wrong email/password)
                logger.error("Invalid credentials for email %s", data.email)
                raise BadRequestException(
                    message_key="auth.errors.invalid_credentials",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                ) from auth_error
            # For other AuthApiError cases, fall through to generic handler
            logger.error("Auth API error for email %s: %s", data.email, str(auth_error))
            raise BadRequestException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            ) from auth_error
        except Exception as login_error:
            # Convert Supabase authentication failures to BadRequestException
            logger.error("Login error for email %s: %s", data.email, str(login_error))
            raise BadRequestException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            ) from login_error

        session = result.session
        user = result.user

        if not hasattr(session, "access_token") or not session.access_token:
            logger.error("login_user session missing access_token for email: %s", data.email)
            raise InternalServerErrorException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        # Get user metadata for 2FA check
        user_metadata = getattr(user, "user_metadata", {}) or {}
        # Get phone for 2FA check (combine phone_number and phone_isd_code)
        phone_number = user_metadata.get("phone_number")
        phone_isd_code = user_metadata.get("phone_isd_code")
        user_phone = None
        if phone_number and phone_isd_code:
            user_phone = f"{phone_isd_code}{phone_number}"

        # Check 2FA after password verification (security best practice)
        await self._check_and_verify_2fa(
            raw_user_metadata=user_metadata,
            verification_id=data.verification_id,
            verification_code=data.verification_code,
            email=data.email,
            user_phone=user_phone,
            phone_number=phone_number,
            phone_isd_code=phone_isd_code,
        )

        # Get user's active organizations
        user_id = getattr(user, "id", None)
        organizations_data = await self.organization_repository.get_user_active_organizations(
            user_id
        )
        organizations = [
            OrganizationBasicDetails(
                id=str(org["id"]),
                name=org["name"],
                domain=org.get("domain"),
                logo_url=org.get("logo_url"),
                description=org.get("description"),
            )
            for org in organizations_data
        ]

        return AuthResponse(
            access_token=session.access_token,
            refresh_token=getattr(session, "refresh_token", None),
            expires_in=getattr(session, "expires_in", None),
            expires_at=getattr(session, "expires_at", None),
            user=UserInfo(
                id=getattr(user, "id", None),
                email=getattr(user, "email", None),
                first_name=user_metadata.get("first_name", None),
                last_name=user_metadata.get("last_name", None),
                phone_number=phone_number,
                phone_isd_code=phone_isd_code,
                timezone=user_metadata.get("timezone", None),
                org_setup_status_completed=bool(organizations),
            ),
            organizations=organizations,
        )

    async def _build_auth_response(self, *, session: Any, user: Any) -> AuthResponse:
        """Build an AuthResponse from a Supabase session + user.

        Loads the user's active organizations and shapes the standard auth
        response used by login and post-set-password auto-login flows.
        """
        user_metadata = getattr(user, "user_metadata", {}) or {}
        phone_number = user_metadata.get("phone_number")
        phone_isd_code = user_metadata.get("phone_isd_code")

        user_id = getattr(user, "id", None)
        organizations_data = await self.organization_repository.get_user_active_organizations(
            user_id
        )
        organizations = [
            OrganizationBasicDetails(
                id=str(org["id"]),
                name=org["name"],
                domain=org.get("domain"),
                logo_url=org.get("logo_url"),
                description=org.get("description"),
            )
            for org in organizations_data
        ]

        return AuthResponse(
            access_token=session.access_token,
            refresh_token=getattr(session, "refresh_token", None),
            expires_in=getattr(session, "expires_in", None),
            expires_at=getattr(session, "expires_at", None),
            user=UserInfo(
                id=user_id,
                email=getattr(user, "email", None),
                first_name=user_metadata.get("first_name", None),
                last_name=user_metadata.get("last_name", None),
                phone_number=phone_number,
                phone_isd_code=phone_isd_code,
                timezone=user_metadata.get("timezone", None),
                org_setup_status_completed=bool(organizations),
            ),
            organizations=organizations,
        )

    def _validate_tokens_present(
        self, access_token: str | None, refresh_token: str | None
    ) -> tuple[str, str]:
        """Validate that both tokens are present and return stripped versions.

        Args:
            access_token: Current access token
            refresh_token: Refresh token

        Returns:
            Tuple of (stripped_access_token, stripped_refresh_token)

        Raises:
            BadRequestException: If access token or refresh token is missing
        """
        if not access_token:
            raise BadRequestException(
                message_key="errors.required_headers_missing",
                custom_code=CustomStatusCode.BAD_REQUEST,
                params={"missing_headers": "Access-Token"},
            )

        if not refresh_token:
            raise BadRequestException(
                message_key="errors.required_headers_missing",
                custom_code=CustomStatusCode.BAD_REQUEST,
                params={"missing_headers": "Refresh-Token"},
            )

        return access_token.strip(), refresh_token.strip()

    async def _decode_and_validate_access_token(
        self, access_token: str, supabase_client: AsyncClient
    ) -> tuple[str | None, bool]:
        """Decode access token and check if it's expired.

        Args:
            access_token: Access token to decode
            supabase_client: Supabase client instance

        Returns:
            Tuple of (user_id, is_expired) where is_expired is True if token is expired.
            user_id will be None if token is expired.

        Raises:
            UnauthorizedException: If access token is invalid (but not if it's just expired)
        """
        try:
            decoded_access_token = await get_claims_from_token(access_token, supabase_client)
            access_token_user_id = decoded_access_token.get("sub")

            # Check if token is expired (required for refresh)
            exp = decoded_access_token.get("exp")
            is_expired = not (exp and exp > int(time.time()))

            return access_token_user_id, is_expired
        except UnauthorizedException as e:
            # Allow expired tokens for refresh flows
            if e.message_key == "errors.token_expired":
                return None, True
            # Re-raise for invalid tokens
            raise

    async def _refresh_user_session_with_error_handling(self, refresh_token: str) -> Any:
        """Refresh user session with comprehensive error handling.

        Args:
            refresh_token: Refresh token

        Returns:
            Result from refresh_user_session

        Raises:
            UnauthorizedException: If refresh token is invalid/expired
            TooManyRequestsException: If rate limit is exceeded
            ServiceUnavailableException: If authentication service is unavailable
        """
        try:
            return await refresh_session(refresh_token, self.supabase_client)
        except AuthApiError as auth_error:
            status = getattr(auth_error, "status", None)

            # Invalid refresh token (status 400)
            if status == 400:
                logger.error("Invalid refresh token: %s", str(auth_error))
                raise UnauthorizedException(
                    message_key="auth.errors.invalid_refresh_token",
                    custom_code=CustomStatusCode.UNAUTHORIZED,
                ) from auth_error

            # Rate limiting (status 429)
            if status == 429:
                logger.error("Rate limit exceeded during refresh: %s", str(auth_error))
                raise TooManyRequestsException(
                    message_key="errors.rate_limit_exceeded",
                    custom_code=CustomStatusCode.RATE_LIMIT_EXCEEDED,
                ) from auth_error

            # Other client-side errors (4xx)
            if status and 400 <= status < 500:
                logger.error(
                    "Auth API error during refresh (status %s): %s",
                    status,
                    str(auth_error),
                )
                raise UnauthorizedException(
                    message_key="auth.errors.authentication_failed",
                    custom_code=CustomStatusCode.UNAUTHORIZED,
                ) from auth_error

            # Server-side or unexpected errors (5xx or unknown)
            logger.error("Authentication service error during refresh: %s", str(auth_error))
            raise ServiceUnavailableException(
                message_key="auth.errors.authentication_service_unavailable",
                custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
            ) from auth_error
        except Exception as exc:
            # Handle network errors, connection issues, or unexpected errors
            logger.error("Failed to refresh session: %s", str(exc))
            raise ServiceUnavailableException(
                message_key="auth.errors.authentication_service_unavailable",
                custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
            ) from exc

    def _validate_token_user_match(self, access_token_user_id: str, refresh_user_id: str) -> None:
        """Validate that access token and refresh token belong to the same user.

        Args:
            access_token_user_id: User ID from access token
            refresh_user_id: User ID from refresh token result

        Raises:
            UnauthorizedException: If tokens don't belong to same user
        """
        if access_token_user_id != refresh_user_id:
            raise UnauthorizedException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )

    async def refresh_session(
        self, access_token: str | None, refresh_token: str | None
    ) -> RefreshSessionResponse:
        """Refresh user session.

        Args:
            access_token: Current access token
            refresh_token: Refresh token

        Returns:
            RefreshSessionResponse: Token information with refresh status

        Raises:
            BadRequestException: If access token or refresh token is missing
            UnauthorizedException: If access token is invalid, refresh token is invalid/expired,
                or tokens don't belong to same user
            ServiceUnavailableException: If authentication service is unavailable or
                encounters server errors
            TooManyRequestsException: If rate limit is exceeded
        """
        # Validate required tokens
        access_token, refresh_token = self._validate_tokens_present(access_token, refresh_token)

        # Decode access token and check expiration
        access_token_user_id, is_expired = await self._decode_and_validate_access_token(
            access_token, self.supabase_client
        )

        # If token is not expired, return without refreshing
        if not is_expired:
            return RefreshSessionResponse(token_refreshed=False)

        # Refresh the session
        res = await self._refresh_user_session_with_error_handling(refresh_token)

        # Verify tokens belong to same user (skip if access token was expired)
        if access_token_user_id is not None:
            self._validate_token_user_match(access_token_user_id, res.user.id)

        return RefreshSessionResponse(
            access_token=res.session.access_token,
            refresh_token=res.session.refresh_token,
            expires_in=res.session.expires_in,
            expires_at=res.session.expires_at,
            token_refreshed=True,
        )

    async def set_password(
        self,
        *,
        user_id: str,
        current_session_id: str | None,
        password: str,
        admin_client: AsyncClient,
        anon_client: AsyncClient,
    ) -> SetPasswordResponse:
        """Set password (admin) and return a fresh session (auto-login).

        Supabase revokes existing sessions when the password is changed via the
        admin API, so we sign in again with the new password and return a fresh
        AuthResponse. If the caller's previous session had an organization
        selected, that selection is carried over to the new session row.

        Note: 2FA is intentionally not enforced here. The caller is already
        authenticated via JWT, and this flow is effectively a session renewal
        for the same user.

        Raises:
            BadRequestException: If password is weak or update fails / email missing.
            UnauthorizedException: If post-update sign-in fails.
        """
        self._validate_password_strength(password)

        session_repo = SessionRepository(db_connection=self.db_connection)

        old_org_id = await self._get_session_org_id(session_repo, current_session_id)
        email = await self._set_password_and_get_email(
            admin_client=admin_client, user_id=user_id, password=password
        )
        session, user = await self._relogin_after_password_set(
            anon_client=anon_client, email=email, password=password, user_id=user_id
        )
        await self._carry_over_org_context(
            session_repo=session_repo,
            anon_client=anon_client,
            user_id=user_id,
            old_org_id=old_org_id,
            access_token=session.access_token,
        )

        auth_response = await self._build_auth_response(session=session, user=user)
        select_org_response = await self._build_select_org_response(user_id, old_org_id)
        return SetPasswordResponse(auth=auth_response, select_organization=select_org_response)

    @staticmethod
    async def _get_session_org_id(
        session_repo: SessionRepository,
        current_session_id: str | None,
    ) -> str | None:
        """Return the organization_id for the current session, if available."""
        if not current_session_id:
            return None
        ctx = await session_repo.get_valid_session_context(current_session_id)
        return ctx.get("organization_id") if ctx else None

    async def _set_password_and_get_email(
        self,
        *,
        admin_client: AsyncClient,
        user_id: str,
        password: str,
    ) -> str:
        """Update password via admin client and return the user's email."""
        updated_user = await update_password_with_link_identity(
            client=admin_client,
            user_id=user_id,
            password=password,
        )
        email = getattr(updated_user, "email", None) if updated_user else None
        if not updated_user or not email:
            raise BadRequestException(
                message_key="auth.errors.failed_to_set_password",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )
        return email

    @staticmethod
    async def _relogin_after_password_set(
        *,
        anon_client: AsyncClient,
        email: str,
        password: str,
        user_id: str,
    ) -> tuple[Any, Any]:
        """Sign in with the new password and return (session, user)."""
        try:
            login_result = await login_user(email=email, password=password, sb_client=anon_client)
        except Exception as login_error:
            logger.error(
                "Auto-login after set_password failed for user %s: %s",
                user_id,
                str(login_error),
            )
            raise UnauthorizedException(
                message_key="auth.errors.session_renewal_failed",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            ) from login_error

        session = getattr(login_result, "session", None)
        user = getattr(login_result, "user", None)
        if not session or not getattr(session, "access_token", None) or not user:
            logger.error(
                "Post-set-password sign-in returned incomplete session for user %s",
                user_id,
            )
            raise UnauthorizedException(
                message_key="auth.errors.session_renewal_failed",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )
        return session, user

    @staticmethod
    async def _carry_over_org_context(
        *,
        session_repo: SessionRepository,
        anon_client: AsyncClient,
        user_id: str,
        old_org_id: str | None,
        access_token: str,
    ) -> None:
        """Copy previous organization context to the newly issued session row."""
        if not old_org_id:
            return
        try:
            new_claims = await get_claims_from_token(access_token, anon_client)
            new_session_id = new_claims.get("session_id") if new_claims else None
            if new_session_id:
                await session_repo.update_session_organization_context(
                    session_id=new_session_id,
                    user_id=user_id,
                    organization_id=old_org_id,
                )
        except Exception as carry_error:
            logger.error(
                "Failed to carry organization context to new session for user %s: %s",
                user_id,
                str(carry_error),
            )

    async def _build_select_org_response(
        self,
        user_id: str,
        old_org_id: str | None,
    ) -> SelectOrganizationResponse | None:
        """Build select-organization payload when an old org context exists."""
        if not old_org_id:
            return None
        try:
            org_member_repo = OrganizationMemberRepository(db_connection=self.db_connection)
            isometrik_details = await get_isometrik_details(
                user_id=user_id,
                organization_id=old_org_id,
                organization_repository=self.organization_repository,
                organization_member_repository=org_member_repo,
            )
            return SelectOrganizationResponse(isometrik_details=isometrik_details)
        except Exception as exc:
            logger.warning(
                "Failed to build select-organization response for user %s org %s: %s",
                user_id,
                old_org_id,
                str(exc),
            )
            return None

    async def forgot_password(self, email: str) -> ForgotPasswordResponse:
        """Send password reset email to user (only if email exists in system).

        Args:
            email: Email address for password reset

        Returns:
            ForgotPasswordResponse: Success response if email exists

        Raises:
            NotFoundException: If email not found
        """
        user = await self.user_repository.get_auth_user_by_email(email)
        if not user:
            raise NotFoundException(
                message_key="auth.errors.email_not_found_in_system",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        await send_password_reset_email(email, self.supabase_client)
        return ForgotPasswordResponse(
            message="Password reset email sent successfully. Please check your email."
        )

    async def reset_password(self, user_id: str, new_password: str) -> PasswordResponse:
        """Reset user password using user_id.

        Args:
            user_id: User's ID
            new_password: New password

        Returns:
            PasswordResponse: Success response

        Raises:
            BadRequestException: If password is weak or reset tokens are invalid/expired
            InternalServerErrorException: If supabase client is not configured
        """
        self._validate_password_strength(new_password)

        # Use anon client for recovery token operations (standard Supabase flow)
        try:
            result = await update_password_by_user_id(
                user_id=user_id,
                new_password=new_password,
                sb_client=self.supabase_client,
            )
        except (AuthApiError, ValueError) as exc:
            logger.error("Password reset token validation failed: %s", str(exc))
            raise BadRequestException(
                message_key="auth.errors.invalid_or_expired_reset_token",
                custom_code=CustomStatusCode.BAD_REQUEST,
            ) from exc

        if not result.user:
            logger.error("Password update failed - no user in result")
            raise BadRequestException(
                message_key="auth.errors.failed_to_update_password",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Send password reset success email to user
        user_email = result.user.email if result.user else ""
        user_metadata = result.user.user_metadata or {}

        user_name = build_full_name(
            user_metadata.get("salutation"),
            user_metadata.get("first_name"),
            user_metadata.get("last_name"),
        )
        user_name = user_name or user_email.split("@")[0] if user_email else ""

        try:
            send_password_reset_success_email(email=user_email, user_name=user_name)
        except Exception as email_error:
            logger.error("Error sending password reset success email: %s", str(email_error))

        return PasswordResponse(
            message="Password reset successfully. You can now login with your new password."
        )

    async def signup(self, signup_data: SignupRequest) -> AuthResponse:
        """Handle user signup for both personal and business accounts.

        Args:
            signup_data: Signup data including user credentials and info

        Returns:
            AuthResponse: Success response with user data

        Raises:
            ValidationException: For validation errors
            InternalServerErrorException: For database or Supabase errors
        """
        self._validate_password_strength(signup_data.password)

        await self._validate_verification_code_for_signup(
            verification_id=signup_data.verification_id,
            email=signup_data.email,
            verification_code=signup_data.verification_code,
        )

        signup_result = await sign_up_supabase_user(signup_data, self.supabase_client)

        session = self._get_session_after_signup(signup_result=signup_result)

        if not session:
            raise InternalServerErrorException(
                message_key="auth.errors.session_not_created_after_signup",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        self._send_welcome_email_safely(email=signup_data.email, first_name=signup_data.first_name)

        user_metadata = signup_result.user.user_metadata or {}
        return AuthResponse(
            access_token=session.access_token,
            refresh_token=session.refresh_token,
            expires_in=session.expires_in,
            expires_at=session.expires_at,
            user=UserInfo(
                id=signup_result.user.id,
                email=signup_result.user.email,
                first_name=user_metadata.get("first_name", None),
                last_name=user_metadata.get("last_name", None),
                phone_number=user_metadata.get("phone_number", None),
                phone_isd_code=user_metadata.get("phone_isd_code", None),
                timezone=user_metadata.get("timezone", None),
            ),
        )

    async def delete_user(self, user_id: str) -> None:
        """Delete user directly from auth.users table without validation.

        Args:
            user_id: The ID of the user to delete

        Raises:
            NotFoundException: If user not found
        """
        result = await delete_user(sb_client=self.supabase_client, user_id=user_id)

        if result is None:
            raise NotFoundException(
                message_key="auth.errors.user_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def change_password(
        self,
        user_id: str,
        user_email: str,
        current_password: str,
        new_password: str,
        user_metadata: dict,
    ) -> ChangePasswordResponse:
        """Change user password endpoint.

        Args:
            user_id: User ID
            user_email: User email
            current_password: Current password
            new_password: New password
            user_metadata: User metadata

        Returns:
            ChangePasswordResponse: Success message

        Raises:
            BadRequestException: For invalid current password or validation errors
            ForbiddenException: If user account is restricted
            InternalServerErrorException: For server errors
            HTTPException: May be raised from Supabase for unexpected errors
        """
        # Validate new password strength
        self._validate_password_strength(new_password)

        # Step 1: Verify current password matches database password
        is_valid = await self.user_repository.verify_current_password(user_id, current_password)

        if not is_valid:
            raise BadRequestException(
                message_key="auth.errors.authentication_failed",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Step 2: Check if new password is same as current password
        if current_password == new_password:
            raise BadRequestException(
                message_key="auth.errors.new_password_must_be_different_from_current_password",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Step 3: Update password
        result = await update_password_with_link_identity(
            self.supabase_client, user_id, new_password
        )
        if not result:
            raise BadRequestException(
                message_key="auth.errors.failed_to_update_password",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Send password change success email
        user_name = (
            build_full_name(
                user_metadata.get("salutation"),
                user_metadata.get("first_name"),
                user_metadata.get("last_name"),
            )
            or user_email.split("@")[0]
            if user_email
            else ""
        )
        try:
            send_password_change_success_email(email=user_email, user_name=user_name)
        except Exception as email_error:
            logger.error("Error sending password change success email: %s", str(email_error))
            # Don't fail the operation if email fails

        return ChangePasswordResponse(message="Password changed successfully")

    async def validate_account(
        self,
        trigger: ValidateAccountTrigger,
        email: str,
        password: str | None = None,
    ) -> ValidateAccountResponse | None:
        """Check if 2FA is enabled for a user account.

        This method validates the user's credentials (email and password) and
        returns whether 2FA is enabled for their account.

        Args:
            trigger: Trigger for authentication
            email: User email
            password: User password

        Returns:
            ValidateAccountResponse: Response containing two_fa_enabled boolean

        Raises:
            NotFoundException: If email not registered
            BadRequestException: If credentials are invalid
            InternalServerErrorException: For internal server errors
            ConflictException: If email already registered
        """
        # Step 1: Check if user account exists
        auth_user = await self.user_repository.get_auth_user_by_email(email)
        if trigger == ValidateAccountTrigger.LOGIN:
            if auth_user is None:
                raise NotFoundException(
                    message_key="auth.errors.email_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
        else:
            if auth_user is not None:
                raise ConflictException(
                    message_key="auth.errors.email_already_registered",
                    custom_code=CustomStatusCode.CONFLICT,
                )

        # Step 2: Validate email and password are correct for LOGIN trigger
        if trigger == ValidateAccountTrigger.LOGIN and password is not None:
            is_valid = await self.user_repository._verify_credentials_by_email(
                email=email, password=password
            )
            if not is_valid:
                raise BadRequestException(
                    message_key="auth.errors.invalid_credentials",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )

        # Step 3: Check if 2FA is enabled for LOGIN trigger
        if trigger == ValidateAccountTrigger.LOGIN:
            raw_user_metadata = auth_user.get("raw_user_meta_data")
            is_enabled, _ = self._is_2fa_enabled(raw_user_metadata)
            return ValidateAccountResponse(two_fa_enabled=is_enabled)
        return None

    async def select_organization(
        self,
        user_id: str,
        session_id: str,
        organization_id: str,
        user_type: SelectOrganizationType = SelectOrganizationType.ORGANIZATION_MEMBER,
    ) -> SelectOrganizationResponse:
        """Select organization for a user session.

        This method validates that:
        1. User is a member of the organization (or has active client_user for org when type=client)
        2. Session is not already linked with an organization
        3. Updates the session with the selected organization_id
        4. Returns isometrik details for the organization

        Args:
            user_id: User ID from JWT token
            session_id: Session ID from JWT token
            organization_id: Organization ID to select
            user_type: Type of user to validate membership for (client or organization_member)

        Returns:
            SelectOrganizationResponse: Response containing isometrik details

        Raises:
            NotFoundException: If user is not a member of the organization
            ConflictException: If session already has an organization linked
            BadRequestException: If session is invalid or inactive
            InternalServerErrorException: For internal server errors
        """
        session_repository = SessionRepository(db_connection=self.db_connection)

        if user_type == SelectOrganizationType.CLIENT:
            contacts_repository = ContactsRepository(db_connection=self.db_connection)
            is_member = await contacts_repository.is_active_contact_user_for_organization(
                user_id=user_id,
                organization_id=organization_id,
            )
        else:
            organization_member_repository = OrganizationMemberRepository(
                db_connection=self.db_connection
            )
            # For select-org, suspended members should be treated as non-members.
            is_member = await organization_member_repository.check_user_membership_by_user_id(
                user_id=user_id,
                organization_id=organization_id,
                disallow_suspended=True,
            )
        if not is_member:
            raise NotFoundException(
                message_key="auth.errors.user_not_member_of_organization",
                custom_code=CustomStatusCode.NOT_FOUND,
                params={"organization_id": organization_id},
            )

        # Check if session already has an organization linked
        session_data = await session_repository.check_session_has_organization(
            session_id=session_id
        )

        if not session_data:
            raise BadRequestException(
                message_key="auth.errors.session_not_found",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        if session_data.get("organization_id") is None:
            # Session is not linked to an organization, update it
            # Update session with organization_id
            await session_repository.update_session_organization_context(
                session_id=session_id,
                user_id=user_id,
                organization_id=organization_id,
            )
        elif str(session_data.get("organization_id")) != organization_id:
            # Session is already linked to an organization,
            # but it's not the same as the selected organization
            raise ConflictException(
                message_key="auth.errors.session_already_has_organization",
                custom_code=CustomStatusCode.CONFLICT,
            )

        # Get isometrik details for the organization (best-effort; never blocks the API)
        isometrik_details = await get_isometrik_details(
            user_id=user_id,
            organization_id=organization_id,
            organization_repository=self.organization_repository,
            organization_member_repository=organization_member_repository
            if user_type != SelectOrganizationType.CLIENT
            else None,
        )

        return SelectOrganizationResponse(isometrik_details=isometrik_details)
