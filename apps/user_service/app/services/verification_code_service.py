"""Verification Code Service Module

This module provides business logic for verification code operations.
All business logic for verification code endpoints is centralized here.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19
"""

import ipaddress
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import asyncpg
import supabase
from fastapi import Request
from supabase.lib.client_options import AsyncClientOptions
from supabase_auth.helpers import model_dump_json
from supabase_auth.types import Session as SupabaseSession

from apps.user_service.app.config.app_settings import app_settings

# Database operations imports
from apps.user_service.app.db.repositories import (
    OrganizationMemberRepository,
    UserRepository,
    VerificationCodeRepository,
)

# Schema imports
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    VerificationTrigger,
    VerificationType,
    VerifyVerificationCodeRequest,
)
from apps.user_service.app.utils.email_utils import send_verification_code_email

# Shared library imports
from libs.shared_db.supabase_db.auth_repository import get_user_by_id
from libs.shared_middleware.jwt_auth import get_claims_from_token
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    GoneException,
    InternalServerErrorException,
    TooManyRequestsException,
    UnauthorizedException,
)

# Utility imports
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

# Initialize logger
logger = get_logger("verification-code-service")


class VerificationCodeService:
    """Service for verification code business logic.
    Handles all verification code operations including send and verify.
    """

    def __init__(
        self, db_connection: asyncpg.Connection, sb_client: supabase.AsyncClient | None = None
    ):
        """Initialize VerificationCodeService with database connection.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection
        self.user_repository = UserRepository(db_connection=db_connection)
        self.verification_code_repository = VerificationCodeRepository(db_connection=db_connection)
        self.organization_member_repository = OrganizationMemberRepository(
            db_connection=db_connection
        )
        self.supabase_client = sb_client

    # UTILITY METHODS
    @staticmethod
    def _sanitize_ip(candidate: str | None) -> str | None:
        """Validate and sanitize IP address strings.
        Returns a valid IP string or None if invalid.

        Args:
            candidate: IP address candidate string

        Returns:
            Valid IP string or None if invalid
        """
        if not candidate:
            return None
        candidate = candidate.split(",")[0].strip()
        try:
            # Validate IPv4/IPv6
            ipaddress.ip_address(candidate)
            return candidate
        except ValueError:
            logger.debug("Invalid IP address detected: %s", candidate)
            return None

    @staticmethod
    def get_client_ip(request: Request) -> str:
        """Extract client IP address from request.

        Ensures that the returned value is a valid IPv4/IPv6 string to avoid
        database errors when storing as inet type.

        Args:
            request: FastAPI request object

        Returns:
            Client IP address string
        """
        forwarded_for = request.headers.get("X-Forwarded-For")
        if ip := VerificationCodeService._sanitize_ip(forwarded_for):
            return ip

        real_ip = request.headers.get("X-Real-IP")
        if ip := VerificationCodeService._sanitize_ip(real_ip):
            return ip

        client_host = request.client.host if request.client else None
        if client_host:
            sanitized_host = VerificationCodeService._sanitize_ip(client_host)
            return sanitized_host if sanitized_host else client_host

        logger.debug("Unable to determine client IP address; returning 'unknown'")
        return "unknown"

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Normalize phone number by removing '+' sign for comparison.
        Supabase phone field may not preserve '+' sign, so we normalize for matching.

        Args:
            phone: Phone number to normalize

        Returns:
            Normalized phone number (without '+')
        """
        if not phone:
            return phone
        # Remove '+' sign if present for comparison
        return phone.lstrip("+")

    @staticmethod
    def _combine_phone(phone_number: str | None, phone_isd_code: str | None) -> str | None:
        """Combine phone_number and phone_isd_code into full phone number.

        Args:
            phone_number: Phone number without ISD code
            phone_isd_code: ISD code (e.g., '+91')

        Returns:
            Combined phone number or None if either is missing
        """
        if not phone_number or not phone_isd_code:
            return None
        return f"{phone_isd_code}{phone_number}"

    # VALIDATION METHODS
    async def _validate_email_for_update(
        self, email: str, user_id: str, current_user_email: str
    ) -> None:
        """Validate email for authenticated user update.

        Args:
            email: Email to validate
            user_id: Current user ID
            current_user_email: Current user's email

        Raises:
            BadRequestException: If email is same as current
            ConflictException: If email already registered
        """
        entered_email = email.lower()

        # Check if entered email is same as current email
        if entered_email == current_user_email:
            raise BadRequestException(
                message_key="verification_codes.errors.email_same_as_current",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Check if email already exists for another user
        existing_user = await self.user_repository.get_auth_user_by_email(email)
        if existing_user:
            existing_user_id = existing_user.get("id")
            if existing_user_id and existing_user_id != user_id:
                raise ConflictException(
                    message_key="verification_codes.errors.email_already_registered",
                    custom_code=CustomStatusCode.CONFLICT,
                )

    async def _check_phone_exists_for_other_user(self, phone: str, user_id: str) -> None:
        """Check if phone number exists for another user using repo method.
        Normalizes input to match DB storage format before exact-match.

        Args:
            phone: Phone number to check
            user_id: Current user ID

        Raises:
            ConflictException: If phone exists for another user
        """
        # Normalize phone to match DB format
        normalized_input_phone = self._normalize_phone(phone)

        # Use repo exact-match method
        exists = await self.user_repository.phone_exists_for_other_user(
            normalized_input_phone, user_id
        )

        if exists:
            raise ConflictException(
                message_key="verification_codes.errors.phone_already_registered",
                custom_code=CustomStatusCode.CONFLICT,
            )

    async def _check_auth_user_exists_by_phone(self, phone: str) -> bool:
        """Check if phone number already exists in auth.users.
        Uses repository for DB check and normalizes phone for comparison.

        Args:
            phone: Phone number to check

        Returns:
            True if phone exists in auth.users, False otherwise
        """
        # Normalize input phone
        normalized_input_phone = self._normalize_phone(phone)

        # Use repo method to check existence (checks all users)
        exists = await self.user_repository.phone_exists_for_other_user(
            normalized_input_phone, user_id=None
        )

        return exists

    async def _validate_phone_for_update(self, phone: str, user_id: str) -> None:
        """Validate phone number for authenticated user update.
        Normalizes phone numbers (removes '+') for comparison.

        Args:
            phone: Phone number to validate
            user_id: Current user ID

        Raises:
            BadRequestException: If phone is same as current
            ConflictException: If phone already registered
        """
        user_data = await get_user_by_id(self.supabase_client, user_id)
        if user_data and hasattr(user_data, "user") and user_data.user:
            # Get phone from user_metadata
            user_metadata = user_data.user.user_metadata or {}
            current_phone_number = user_metadata.get("phone_number")
            current_phone_isd_code = user_metadata.get("phone_isd_code")

            # Combine current phone if available
            current_user_phone = None
            if current_phone_number and current_phone_isd_code:
                current_user_phone = f"{current_phone_isd_code}{current_phone_number}"

            # Normalize both phones for comparison (remove '+' if present)
            normalized_input_phone = self._normalize_phone(phone)

            normalized_current_phone = None
            if current_user_phone:
                normalized_current_phone = self._normalize_phone(current_user_phone)

            # Check if entered phone is same as current phone (after normalization)
            if normalized_current_phone and normalized_input_phone == normalized_current_phone:
                raise BadRequestException(
                    message_key="verification_codes.errors.phone_same_as_current",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )

            # Check if phone already exists for another user
            await self._check_phone_exists_for_other_user(phone, user_id)

    def _validate_verification_record(
        self, verification_record: dict, data: VerifyVerificationCodeRequest
    ) -> str:
        """Validate verification record and return given_input.

        Args:
            verification_record: The verification code record
            data: Request data containing type and email/phoneNumber

        Returns:
            The given_input value (email or phoneNumber)

        Raises:
            BadRequestException: If validation fails
            GoneException: If verification code expired
        """
        if not verification_record:
            raise BadRequestException(
                message_key="verification_codes.errors.verification_code_not_found",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Check if already verified
        if verification_record.get("verified", False):
            raise BadRequestException(
                message_key="verification_codes.errors.verification_code_already_verified",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Check expiry
        current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        expiry_at = verification_record.get("expiry_at", 0)

        if expiry_at < current_time_ms:
            raise GoneException(
                message_key="verification_codes.errors.verification_code_expired",
                custom_code=CustomStatusCode.GONE,
            )

        # Validate given input matches
        if data.type == VerificationType.EMAIL:
            given_input = data.email
        else:  # PHONE_NUMBER
            given_input = self._combine_phone(data.phone_number, data.phone_isd_code)

        stored_given_input = verification_record.get("given_input")
        if stored_given_input != given_input:
            raise BadRequestException(
                message_key="verification_codes.errors.verification_code_invalid",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        return given_input

    def _check_verification_code_ownership(
        self, verification_record: dict, current_user: dict | None
    ) -> None:
        """Check if authenticated user owns the verification code.

        Args:
            verification_record: The verification code record
            current_user: Optional authenticated user

        Raises:
            ForbiddenException: If user doesn't own the verification code
        """
        stored_user_id = verification_record.get("user_id")
        if not current_user:
            return

        # JWT tokens use "sub" for user ID
        current_user_id = current_user.get("sub")

        # If verification code has a user_id, it must match the current user
        if stored_user_id and current_user_id and str(stored_user_id) != str(current_user_id):
            raise ForbiddenException(
                message_key="verification_codes.errors.verification_code_ownership_mismatch",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

    # VERIFICATION CODE OPERATIONS
    async def _verify_code_and_update_record(
        self, verification_record: dict, verification_code: str, verification_id: str
    ) -> bool:
        """Verify the code and update the verification record.

        Note: There is no limit on verification attempts.
        Users can verify as many times as they want.

        Args:
            verification_record: The verification code record
            verification_code: The code to verify
            verification_id: The verification code ID

        Returns:
            True if code matches, False otherwise

        Raises:
            BadRequestException: If code doesn't match
        """
        # Get existing attempts
        attempts = verification_record.get("attempts", [])
        if not isinstance(attempts, list):
            attempts = []

        # Create attempt record
        attempt_record = {
            "entered_value": verification_code,
            "verified_on": int(datetime.now(timezone.utc).timestamp() * 1000),
            "matched": False,
            "success": False,
        }

        # Check if code matches
        stored_code = verification_record.get("verification_code")
        code_matched = verification_code == stored_code

        # Update attempt record
        attempt_record["matched"] = code_matched
        attempt_record["success"] = code_matched

        # Add attempt to attempts array
        attempts.append(attempt_record)

        # Update verification record
        verified = code_matched

        # Update database
        await self.verification_code_repository.update_verification_code(
            verification_id=verification_id, verified=verified, attempts=attempts
        )

        # If not matched, reject with simple error message (no attempt limit)
        if not code_matched:
            raise BadRequestException(
                message_key="verification_codes.errors.verification_code_invalid",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        return code_matched

    async def _get_supabase_client_with_token(self, access_token: str) -> Any:
        """Create a Supabase client with user's access token.

        Args:
            access_token: User's JWT access token

        Returns:
            Supabase AsyncClient configured with user's token

        Raises:
            InternalServerErrorException: If Supabase configuration is missing
        """
        supabase_url = app_settings.shared_settings.supabase.url
        supabase_anon_key = app_settings.shared_settings.supabase.anon_key

        if not supabase_url or not supabase_anon_key:
            raise InternalServerErrorException(
                message_key="errors.missing_configuration",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        # Create client with user's access token in headers
        options = AsyncClientOptions(headers={"Authorization": f"Bearer {access_token}"})

        client = await supabase.create_async_client(supabase_url, supabase_anon_key, options)
        return client

    async def _validate_and_set_session(self, access_token: str) -> None:
        """Validate access token and set session in Supabase client.

        Args:
            access_token: User's JWT access token

        Raises:
            UnauthorizedException: If token is invalid or expired
            InternalServerErrorException: If configuration is missing
        """
        # Create Supabase client with user's access token
        supabase = await self._get_supabase_client_with_token(access_token)

        # Get user first to validate token and get user info
        user_response = await supabase.auth.get_user(access_token)
        if not user_response or not user_response.user:
            raise UnauthorizedException(
                message_key="errors.invalid_access_token_or_user_not_found",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )

        # Decode JWT to get expiration time using get_claims
        if not self.supabase_client:
            raise InternalServerErrorException(
                message_key="errors.missing_configuration",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            )

        decoded = await get_claims_from_token(access_token, self.supabase_client)
        exp = decoded.get("exp", 0)
        current_time = int(time.time())

        if 0 < exp <= current_time:
            raise UnauthorizedException(
                message_key="errors.access_token_expired",
                custom_code=CustomStatusCode.UNAUTHORIZED,
            )

        # Calculate expires_in
        expires_in = max(exp - current_time, 3600) if exp > 0 else 3600
        expires_at = exp if exp > 0 else current_time + expires_in
        # Create session with access token and a placeholder refresh token
        # The refresh token won't be used if the access token is still valid
        session = SupabaseSession(
            access_token=access_token,
            refresh_token="placeholder_refresh_token",
            expires_in=expires_in,
            expires_at=expires_at,
            token_type="bearer",
            user=user_response.user,
        )

        # Save session directly to storage and set in-memory session
        # This mimics what _save_session() does internally
        storage_key = supabase.auth._storage_key
        session_json = model_dump_json(session)

        # Set in-memory session first
        supabase.auth._in_memory_session = session

        # Save to storage if persist_session is enabled
        if supabase.auth._persist_session:
            await supabase.auth._storage.set_item(storage_key, session_json)

    async def _update_user_email(self, user_id: str, email: str) -> bool:
        """Update user email using Supabase admin API.

        Args:
            user_id: User ID to update
            email: New email address

        Returns:
            True if email was updated successfully

        Raises:
            InternalServerErrorException: If update fails
        """
        current_time = datetime.now(timezone.utc).isoformat()
        user_data = await self.supabase_client.auth.admin.get_user_by_id(user_id)
        existing_metadata = user_data.user.user_metadata or {}
        updated_metadata = {**existing_metadata, "email": email}

        response = await self.supabase_client.auth.admin.update_user_by_id(
            user_id, {"email": email, "email_confirmed_at": current_time}
        )

        await self.supabase_client.auth.admin.update_user_by_id(
            user_id,
            {"user_metadata": updated_metadata},
        )

        if response and response.user:
            # Verify email was actually updated
            updated_user = await self.supabase_client.auth.admin.get_user_by_id(user_id)
            if updated_user and updated_user.user:
                if updated_user.user.email != email:
                    # Retry update if email doesn't match
                    await self.supabase_client.auth.admin.update_user_by_id(
                        user_id,
                        {
                            "email": email,
                            "email_confirmed_at": current_time,
                            "user_metadata": updated_metadata,
                        },
                    )
            # Also update organization_members table
            await self.organization_member_repository.update_user_email_by_user_id(user_id, email)
            return True

        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )

    async def _update_user_phone(
        self, user_id: str, phone_number: str, phone_isd_code: str
    ) -> bool:
        """Update user phone number using Supabase admin API.

        Args:
            user_id: User ID to update
            phone_number: New phone number (without ISD code)
            phone_isd_code: New phone ISD code (e.g., '+91')

        Returns:
            True if phone was updated successfully

        Raises:
            InternalServerErrorException: If update fails
        """
        user_data = await self.supabase_client.auth.admin.get_user_by_id(user_id)
        existing_metadata = {}
        if user_data and user_data.user:
            existing_metadata = user_data.user.user_metadata or {}

        updated_metadata = dict(existing_metadata)
        # Store phone_number and phone_isd_code separately in user_metadata
        updated_metadata["phone_number"] = phone_number
        updated_metadata["phone_isd_code"] = phone_isd_code

        response = await self.supabase_client.auth.admin.update_user_by_id(
            user_id,
            {"user_metadata": updated_metadata},
        )

        if response and response.user:
            # Verify the phone was actually updated in user_metadata
            updated_metadata_check = response.user.user_metadata or {}
            updated_phone_number = updated_metadata_check.get("phone_number")
            updated_phone_isd_code = updated_metadata_check.get("phone_isd_code")

            # Verify both phone_number and phone_isd_code were updated
            if updated_phone_number != phone_number or updated_phone_isd_code != phone_isd_code:
                # Retry update if phone doesn't match
                await self.supabase_client.auth.admin.update_user_by_id(
                    user_id,
                    {"user_metadata": updated_metadata},
                )
            # Also update organization_members table with separate phone_number and phone_isd_code
            await self.organization_member_repository.update_user_phone_by_user_id(
                user_id, phone_number, phone_isd_code
            )
            return True

        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )

    async def _update_email_or_phone(
        self,
        user_id: str,
        given_input: str,
        triggered_text: str,
        access_token: str,
        phone_number: str | None = None,
        phone_isd_code: str | None = None,
    ) -> tuple[bool, bool]:
        """Update email or phone number using Supabase auth.update_user() with user's token.

        This function uses the authenticated user's token to update their own email/phone,
        following Supabase's recommended approach for user updates.

        Args:
            user_id: User ID to update
            given_input: Email or phone number to set
            triggered_text: The trigger type from verification record
            access_token: User's JWT access token for authentication
            phone_number: Phone number without ISD code (required for PHONE_NUMBER_UPDATE)
            phone_isd_code: Phone ISD code (required for PHONE_NUMBER_UPDATE)

        Returns:
            Tuple of (email_updated, phone_updated)
        """
        try:
            # Validate token and set session
            await self._validate_and_set_session(access_token)

            # Update based on trigger type
            if triggered_text == VerificationTrigger.EMAIL_UPDATE.value:
                email_updated = await self._update_user_email(user_id, given_input)
                return email_updated, False
            if triggered_text == VerificationTrigger.PHONE_NUMBER_UPDATE.value:
                if not phone_number or not phone_isd_code:
                    raise BadRequestException(
                        message_key="verification_codes.errors.phoneNumber_required",
                        custom_code=CustomStatusCode.BAD_REQUEST,
                    )
                phone_updated = await self._update_user_phone(user_id, phone_number, phone_isd_code)
                return False, phone_updated
            return False, False

        except (UnauthorizedException, InternalServerErrorException):
            # Re-raise custom exceptions as-is
            raise
        except Exception as e:
            logger.error("Error updating user data: %s", str(e), exc_info=True)
            raise InternalServerErrorException(
                message_key="errors.internal_server_error",
                custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
            ) from e

    def _determine_triggered_text(
        self, data: SendVerificationCodeRequest, current_user: dict | None
    ) -> str:
        """Determine the triggered_text based on authentication status and type.

        Args:
            data: Request data containing type and email/phoneNumber
            current_user: Optional authenticated user

        Returns:
            Triggered text value for the verification code
        """
        if current_user:
            # Authenticated user - change operation
            if data.type == VerificationType.EMAIL:
                return VerificationTrigger.EMAIL_UPDATE.value
            # PHONE_NUMBER
            return VerificationTrigger.PHONE_NUMBER_UPDATE.value
        # Unauthenticated user - signup operation
        if data.type == VerificationType.EMAIL:
            return VerificationTrigger.SIGNUP_EMAIL_VERIFICATION.value
        # PHONE_NUMBER
        return VerificationTrigger.SIGNUP_PHONE_VERIFICATION.value

    async def _validate_authenticated_user_input(
        self, data: SendVerificationCodeRequest, current_user: dict | None
    ) -> tuple[str, str]:
        """Validate input for authenticated user and return user_id and triggered_text.

        Args:
            data: Request data containing type and email/phoneNumber
            current_user: Authenticated user dict

        Returns:
            Tuple of (user_id, triggered_text)

        Raises:
            BadRequestException: If validation fails
        """
        user_id = current_user.get("sub") if current_user else None

        # Get current email from Supabase auth
        current_user_email = ""
        if user_id:
            user_data = await get_user_by_id(self.supabase_client, user_id)
            if user_data and hasattr(user_data, "user") and user_data.user:
                # Get the actual email field, not user_metadata
                if hasattr(user_data.user, "email") and user_data.user.email:
                    current_user_email = user_data.user.email.lower()

        if data.type == VerificationType.EMAIL:
            await self._validate_email_for_update(data.email, user_id, current_user_email)
            triggered_text = VerificationTrigger.EMAIL_UPDATE.value
        else:  # PHONE_NUMBER
            full_phone = self._combine_phone(data.phone_number, data.phone_isd_code)
            if not full_phone:
                raise BadRequestException(
                    message_key="verification_codes.errors.phoneNumber_required",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )
            await self._validate_phone_for_update(full_phone, user_id)
            triggered_text = VerificationTrigger.PHONE_NUMBER_UPDATE.value

        return user_id, triggered_text

    async def _validate_unauthenticated_user_input(
        self, data: SendVerificationCodeRequest, skip_existence_check: bool = False
    ) -> None:
        """Validate input for unauthenticated user (signup flow).

        Checks if email/phone already exists in the system.

        Args:
            data: Request data containing type and email/phoneNumber
            skip_existence_check: If True, skip the existence check (e.g., for TWO_FACTOR_AUTH)

        Raises:
            BadRequestException: If email/phone already registered
        """
        if skip_existence_check:
            return

        if data.type == VerificationType.EMAIL:
            existing_auth_user = await self.user_repository.get_auth_user_by_email(data.email)
            if existing_auth_user:
                raise BadRequestException(
                    message_key="verification_codes.errors.email_already_registered",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )
        else:  # PHONE_NUMBER
            full_phone = self._combine_phone(data.phone_number, data.phone_isd_code)
            if not full_phone:
                raise BadRequestException(
                    message_key="verification_codes.errors.phoneNumber_required",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )
            phone_exists = await self._check_auth_user_exists_by_phone(full_phone)
            if phone_exists:
                raise BadRequestException(
                    message_key="verification_codes.errors.phone_already_registered",
                    custom_code=CustomStatusCode.BAD_REQUEST,
                )

    async def _determine_user_context(
        self, data: SendVerificationCodeRequest, current_user: dict | None
    ) -> tuple[str | None, str]:
        """Determine user_id and triggered_text based on authentication status and request data.

        Args:
            data: Request data containing type, email/phoneNumber, and optional verification_method
            current_user: Optional authenticated user

        Returns:
            Tuple of (user_id, triggered_text)

        Raises:
            BadRequestException: If validation fails
        """
        # If verification_method is provided, use it as triggered_text
        if data.verification_method:
            triggered_text = data.verification_method
            if current_user:
                user_id, _ = await self._validate_authenticated_user_input(data, current_user)
            else:
                # Skip existence check for TWO_FACTOR_AUTH
                skip_check = data.verification_method.upper() == "TWO_FACTOR_AUTH"
                await self._validate_unauthenticated_user_input(
                    data, skip_existence_check=skip_check
                )
                user_id = None
            return user_id, triggered_text

        # Default behavior: determine triggered_text based on auth status
        if current_user:
            user_id, triggered_text = await self._validate_authenticated_user_input(
                data, current_user
            )
        else:
            await self._validate_unauthenticated_user_input(data)
            user_id = None
            triggered_text = self._determine_triggered_text(data, current_user)

        return user_id, triggered_text

    async def _check_rate_limit(self, verification_type: str, given_input: str) -> int:
        """Check rate limiting and return remaining attempts.

        Args:
            verification_type: Type of verification (EMAIL or PHONE_NUMBER)
            given_input: Email or phone number

        Returns:
            Number of attempts remaining after creating new code

        Raises:
            TooManyRequestsException: If max attempts reached
        """
        recent_codes = await self.verification_code_repository.get_recent_verification_codes(
            type_text=verification_type,
            given_input=given_input,
            limit=app_settings.two_fa_settings.max_attempt_verification,
            window_hours=app_settings.two_fa_settings.verification_attempt_window_hours,
        )

        unverified_count = sum(1 for code in recent_codes if not code.get("verified", False))

        if unverified_count >= app_settings.two_fa_settings.max_attempt_verification:
            raise TooManyRequestsException(
                message_key="verification_codes.errors.maximum_send_otp_attempts_reached",
                custom_code=CustomStatusCode.RATE_LIMIT_EXCEEDED,
                params={"max_attempts": app_settings.two_fa_settings.max_attempt_verification},
            )

        # Calculate remaining attempts after creating new code
        attempts_left = app_settings.two_fa_settings.max_attempt_verification - unverified_count - 1
        return attempts_left

    async def create_verification_code(
        self,
        type_text: str,
        given_input: str,
        triggered_text: str,
        user_id: str | None,
        ip_address: str | None,
    ) -> dict:
        """Helper function to handle OTP generation and business logic,
        then delegate DB insertion to the repository.

        Args:
            type_text: Type of verification (EMAIL or PHONE_NUMBER)
            given_input: Email or phone number
            triggered_text: Trigger type (e.g., EMAIL_UPDATE, SIGNUP_EMAIL_VERIFICATION)
            user_id: Optional user ID
            ip_address: Optional IP address

        Returns:
            Inserted verification code record as dict
        """
        # Determine OTP
        type_upper = type_text.upper()
        if type_upper == "EMAIL":
            otp_enabled = app_settings.two_fa_settings.email_otp_enabled
            default_otp = app_settings.two_fa_settings.email_default_otp
        else:  # PHONE_NUMBER
            otp_enabled = app_settings.two_fa_settings.phone_otp_enabled
            default_otp = app_settings.two_fa_settings.phone_default_otp

        if otp_enabled:
            # Generate cryptographically secure random 4-digit code (1000-9999)
            # Using secrets.randbelow for secure random number generation
            verification_code = str(secrets.randbelow(9000) + 1000)
        else:
            # Use default OTP from config
            verification_code = default_otp

        # Expiry time in milliseconds
        current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        expiry_at = current_time_ms + (
            app_settings.two_fa_settings.verification_code_expiry_minutes * 60 * 1000
        )

        verification_data = {
            "type_text": type_text,
            "given_input": given_input,
            "triggered_text": triggered_text,
            "verification_code": verification_code,
            "verified": False,
            "expiry_at": expiry_at,
            "attempts": [],
            "user_id": user_id,
            "ip_address": ip_address,
        }

        # Insert into DB
        inserted_record = await self.verification_code_repository.insert_verification_code(
            verification_data
        )

        return inserted_record

    # MAIN SERVICE METHODS
    async def send_verification_code(
        self, request: Request, data: SendVerificationCodeRequest, current_user: dict | None
    ) -> dict:
        """Send verification code business logic.

        Args:
            request: FastAPI request object
            data: SendVerificationCodeRequest containing type and email/phoneNumber
            current_user: Optional authenticated user

        Returns:
            Dictionary with verification_id, expiryAt, and attemptsLeft

        Raises:
            BadRequestException: For validation errors
            ConflictException: If email/phone already registered
            TooManyRequestsException: For rate limiting
            InternalServerErrorException: If email not sent
        """
        # Extract given input (email or phone number)
        if data.type == VerificationType.EMAIL:
            given_input = data.email
        else:  # PHONE_NUMBER
            given_input = self._combine_phone(data.phone_number, data.phone_isd_code)

        # Determine user context (user_id and triggered_text) and validate input
        user_id, triggered_text = await self._determine_user_context(data, current_user)

        # Check rate limiting and get remaining attempts
        attempts_left = await self._check_rate_limit(data.type.value, given_input)

        # Get client IP address
        ip_address = self.get_client_ip(request)

        # Create verification code record
        verification_record = await self.create_verification_code(
            type_text=data.type.value,
            given_input=given_input,
            triggered_text=triggered_text,
            user_id=user_id,
            ip_address=ip_address,
        )

        # Send verification code email if type is EMAIL
        if data.type == VerificationType.EMAIL:
            verification_code = verification_record.get("verification_code")
            email_sent = send_verification_code_email(
                email=given_input,
                otp_code=verification_code,
                expiry_minutes=app_settings.two_fa_settings.verification_code_expiry_minutes,
            )
            if not email_sent:
                raise InternalServerErrorException(
                    message_key="errors.internal_server_error",
                    custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
                )

        # Return response data
        return {
            "verification_id": str(verification_record["id"]),
            "expiryAt": verification_record["expiry_at"],
            "attemptsLeft": attempts_left,
        }

    async def verify_verification_code(
        self, request: Request, data: VerifyVerificationCodeRequest, current_user: dict | None
    ) -> dict:
        """Verify verification code business logic.

        Args:
            request: FastAPI request object
            data: VerifyVerificationCodeRequest containing type,
                  verification ID, OTP code, and email/phoneNumber
            current_user: Optional authenticated user

        Returns:
            Dictionary with verified field

        Raises:
            BadRequestException: For validation errors
            GoneException: If verification code expired
            ForbiddenException: If user doesn't own the verification code
            UnauthorizedException: If access token is missing
        """
        # Get verification code record
        verification_record = await self.verification_code_repository.get_verification_code_by_id(
            data.verification_id
        )

        # Validate verification record and get given_input
        given_input = self._validate_verification_record(verification_record, data)

        # Security check: If authenticated user, verify they own the verification code
        self._check_verification_code_ownership(verification_record, current_user)

        # Verify code and update record
        await self._verify_code_and_update_record(
            verification_record, data.verification_code, data.verification_id
        )

        # After successful verification, check if we need to update email/phone
        triggered_text = verification_record.get("triggered_text", "")
        stored_user_id = verification_record.get("user_id")

        # Determine user_id for update (from token or stored_user_id)
        user_id = None
        if current_user:
            user_id = current_user.get("sub")
        elif stored_user_id:
            user_id = stored_user_id

        # Update email/phone if needed (only with token)
        if (
            current_user
            and user_id
            and triggered_text
            in [
                VerificationTrigger.EMAIL_UPDATE.value,
                VerificationTrigger.PHONE_NUMBER_UPDATE.value,
            ]
        ):
            # Get access token from request state (set by JWT middleware)
            access_token = getattr(request.state, "access_token", None)
            if not access_token:
                raise UnauthorizedException(
                    message_key="errors.unauthorized",
                    custom_code=CustomStatusCode.UNAUTHORIZED,
                )

            # For phone updates, pass phone_number and phone_isd_code separately
            if triggered_text == VerificationTrigger.PHONE_NUMBER_UPDATE.value:
                await self._update_email_or_phone(
                    user_id,
                    given_input,
                    triggered_text,
                    access_token,
                    phone_number=data.phone_number,
                    phone_isd_code=data.phone_isd_code,
                )
            else:
                await self._update_email_or_phone(
                    user_id, given_input, triggered_text, access_token
                )

        # Return success response
        return {"verified": True}
