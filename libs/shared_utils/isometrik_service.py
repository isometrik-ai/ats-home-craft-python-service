"""Isometrik service module
This module provides integration with Isometrik API for application creation.
Handles creation of Isometrik applications.
"""

import time
import uuid
from typing import Any

import httpx
import jwt

from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    InternalServerErrorException,
    RateLimitExceededException,
    ServiceUnavailableException,
)
from libs.shared_utils.isometrik_strands_client import get_strands_http_client
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("isometrik_service")

# Default organization-level role used when interacting with Isometrik.
# Kept as a simple string to avoid coupling to application enums.
DEFAULT_ORG_ROLE = "owner"

# JWT aud/iss claim value for Isometrik access tokens (matches Go IsometrikAudience).
ISOMETRIK_AUDIENCE = "Isometrik"

_AI_AGENT_PATH = "/v1/ai-agent"


def create_isometrik_token() -> str:
    """Create a signed Isometrik access JWT for the configured client.

    Mirrors the Go ``CreateIsometrikToken`` helper: HS512 token with standard
    Isometrik claims (aud, iss, sub, typ, jti, etc.). The ``sub`` claim uses
    ``ISOMETRIK_CLIENT_NAME`` from settings.

    Returns:
        Encoded JWT string.

    Raises:
        InternalServerErrorException: If Isometrik token settings are missing.
    """
    iso = shared_settings.isometrik
    if not iso.private_key.strip() or not iso.client_name.strip():
        logger.error("Isometrik token settings are not configured")
        raise InternalServerErrorException(
            message_key="errors.internal_server_error",
            custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
        )

    now = int(time.time())
    payload = {
        "aud": ISOMETRIK_AUDIENCE,
        "exp": now + iso.token_exp_minutes * 60,
        "iat": now,
        "iss": ISOMETRIK_AUDIENCE,
        "jti": str(uuid.uuid4()),
        "nbf": now - 1,
        "sub": iso.client_name,
        "typ": "access",
    }
    return jwt.encode(payload, iso.private_key, algorithm="HS512")


def _handle_isometrik_error(
    e: Exception, operation: str, response: httpx.Response | None = None
) -> None:
    """Handle all Isometrik API errors in a unified way.

    Args:
        e: Exception from httpx or general exception
        operation: Description of the operation being performed (for logging)
        response: Optional httpx response object for JSON parsing errors

    Raises:
        ConflictException: If status code is 409
        BadRequestException: If status code is 400-499 (except 409, 429)
        RateLimitExceededException: If status code is 429
        ServiceUnavailableException: If connection error, 5xx status code, or JSON parsing error
        InternalServerErrorException: If unexpected error occurs
    """
    # Handle ValueError from JSON parsing
    if isinstance(e, ValueError) and response is not None:
        logger.error("Invalid JSON in Isometrik API response: %s", response.text)
        raise ServiceUnavailableException(
            message_key="errors.external_service_unavailable",
            custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
        ) from e

    # Handle HTTPStatusError (4xx, 5xx status codes)
    if isinstance(e, httpx.HTTPStatusError):
        logger.error("Isometrik API status code error: %s", str(e))
        status_code = e.response.status_code

        # Try to extract message from Isometrik response
        isometrik_message = None
        try:
            response_json = e.response.json()
            isometrik_message = response_json.get("message")
        except (ValueError, AttributeError):
            pass

        # Handle 409 Conflict
        if status_code == 409:
            message_key = isometrik_message or "errors.conflict"
            raise ConflictException(
                message_key=message_key,
                custom_code=CustomStatusCode.CONFLICT,
            ) from e

        # Check 429 BEFORE 400-499 range to handle rate limiting correctly
        if status_code == 429:
            message_key = isometrik_message or "errors.external_api_rate_limited"
            raise RateLimitExceededException(
                message_key=message_key,
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_RATE_LIMIT,
            ) from e

        if 400 <= status_code < 500:
            message_key = isometrik_message or "errors.external_api_bad_request"
            raise BadRequestException(
                message_key=message_key,
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_BAD_REQUEST,
            ) from e

        # 5xx errors
        message_key = isometrik_message or "errors.external_service_unavailable"
        raise ServiceUnavailableException(
            message_key=message_key,
            custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
        ) from e

    # Handle RequestError (connection errors, timeouts, etc.)
    if isinstance(e, httpx.RequestError):
        logger.error("Isometrik API connection error: %s", str(e))
        raise ServiceUnavailableException(
            message_key="errors.external_service_unavailable",
            custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
        ) from e

    # Handle all other unexpected errors
    logger.error("Unexpected error %s: %s", operation, str(e))
    raise InternalServerErrorException(
        message_key="errors.internal_server_error",
        custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
    ) from e


def get_isometrik_data_from_settings(
    organization_settings: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Extract Isometrik application data from organization settings."""
    if not organization_settings:
        return None

    application_details = organization_settings.get("isometrik_application_details")
    if application_details and isinstance(application_details, dict):
        return application_details

    return organization_settings.get("isometrik")


async def create_isometrik_application(
    organization_name: str, product_types: list[str] | None = None, plan: str = "basic"
) -> dict[str, Any]:
    """Create a new Isometrik application for an organization.

    Args:
        organization_name (str): Name of the organization
        product_types (Optional[list]): List of product types (default: ["chat", "video"])
        plan (str): Plan type (default: "basic")

    Returns:
        dict[str, Any]: Response from Isometrik API containing application details

    Raises:
        ConflictException: If API call returns 409 status code
        BadRequestException: If API call returns 400 status code
        RateLimitExceededException: If API call returns 429 status code
        ServiceUnavailableException: If API call returns 5xx status code
        InternalServerErrorException: If unexpected error occurs
    """
    try:
        # Default product types
        if product_types is None:
            product_types = ["chat", "video"]

        # Prepare request payload
        payload = {
            "clientName": shared_settings.isometrik.client_name,
            "name": organization_name,
            "productType": product_types,
            "regionId": shared_settings.isometrik.region_id,
            "plan": plan,
            "isHighLimit": True,
        }

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {shared_settings.isometrik.auth_token}",
        }

        # Make API call
        url = f"{shared_settings.isometrik.admin_api_url}/v1/intr/application"

        response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    except Exception as e:
        _handle_isometrik_error(e, "creating Isometrik application", response)


async def create_isometrik_user(
    user: dict[str, Any],
    isometrik_credentials: dict[str, Any],
) -> dict[str, Any]:
    """Create a new Isometrik user for an organization.

    Args:
        user (dict[str, Any]): User data containing:
            - ``user_id`` (str): User ID.
            - ``email`` (str): User's email address.
            - ``organization_id`` (str): Organization ID.
            - ``role`` (str): Role of the user.
            - ``first_name`` (str | None): User's first name (optional).
            - ``last_name`` (str | None): User's last name (optional).
        isometrik_credentials (dict[str, Any]): Isometrik credentials from settings.
            Should contain: ``userSecret``, ``licenseKey``, ``appSecret``.
    Returns:
        dict[str, Any]: Response from Isometrik API containing user details

    Raises:
        ConflictException: If API call returns 409 status code
        BadRequestException: If API call returns 400 status code
        RateLimitExceededException: If API call returns 429 status code
        ServiceUnavailableException: If API call returns 5xx status code
        InternalServerErrorException: If unexpected error occurs
    """
    try:
        user_id = str(user["user_id"])
        email = user["email"]
        organization_id = str(user["organization_id"])
        role = user["role"]
        first_name = user.get("first_name")
        last_name = user.get("last_name")
        avatar_url = user.get("avatar_url", "https://example.com/default-avatar.jpg")

        user_name = " ".join(filter(None, [first_name, last_name])) or email.split("@")[0]

        password = user_id.replace("-", "")[:12] + "Ai$"

        payload = {
            "userName": user_name,
            "userIdentifier": str(user_id),
            "userProfileImageUrl": avatar_url,
            "password": password,
            "metaData": {
                "user_id": str(user_id),
                "role": role,
                "organization_id": str(organization_id),
            },
            "messageNotificationEmail": email,
            "emailNotifications": True,
            "clubEmailNotifications": False,
        }

        headers = {
            "Content-Type": "application/json",
            "userSecret": isometrik_credentials.get("userSecret", ""),
            "licenseKey": isometrik_credentials.get("licenseKey", ""),
            "appSecret": isometrik_credentials.get("appSecret", ""),
        }

        url = f"{shared_settings.isometrik.api_url}/chat/user"

        response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    except Exception as e:
        _handle_isometrik_error(e, "creating Isometrik chat user", response)


async def login_to_isometrik(
    user_id: str,
    isometrik_credentials: dict[str, Any],
) -> dict[str, Any]:
    """Login to Isometrik.

    Args:
        user_id (str): User ID
        isometrik_credentials (dict[str, Any]): Isometrik credentials from settings
            Should contain: userSecret, licenseKey, appSecret
    Returns:
        dict[str, Any]: Response from Isometrik API containing login details

    Raises:
        ConflictException: If API call returns 409 status code
        BadRequestException: If API call returns 400 status code
        RateLimitExceededException: If API call returns 429 status code
        ServiceUnavailableException: If API call returns 5xx status code
        InternalServerErrorException: If unexpected error occurs
    """
    try:
        password = user_id.replace("-", "")[:12] + "Ai$"

        payload = {
            "userIdentifier": str(user_id),
            "password": password,
        }

        headers = {
            "Content-Type": "application/json",
            "userSecret": isometrik_credentials.get("userSecret", ""),
            "licenseKey": isometrik_credentials.get("licenseKey", ""),
            "appSecret": isometrik_credentials.get("appSecret", ""),
        }

        url = f"{shared_settings.isometrik.api_url}/chat/user/authenticate"

        response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    except Exception as e:
        _handle_isometrik_error(e, "logging in to Isometrik", response)


async def update_isometrik_user(
    isometrik_credentials: dict[str, Any],
    user_name: str | None = None,
    user_profile_image_url: str | None = None,
) -> dict[str, Any]:
    """Update an existing Isometrik user.

    Args:
        isometrik_credentials (dict[str, Any]): Isometrik credentials from settings
            Should contain: userToken, licenseKey, appSecret
        user_name (str | None): Updated user name (optional)
        user_profile_image_url (str | None): Updated user profile image URL (optional)

    Returns:
        dict[str, Any]: Response from Isometrik API containing updated user details

    Raises:
        ConflictException: If API call returns 409 status code
        BadRequestException: If API call returns 400 status code
        RateLimitExceededException: If API call returns 429 status code
        ServiceUnavailableException: If API call returns 5xx status code
        InternalServerErrorException: If unexpected error occurs
    """
    try:
        # Build payload with only provided fields
        payload: dict[str, Any] = {}
        if user_name is not None:
            payload["userName"] = user_name
        if user_profile_image_url is not None:
            payload["userProfileImageUrl"] = user_profile_image_url

        headers = {
            "Content-Type": "application/json",
            "userToken": isometrik_credentials.get("userToken", ""),
            "licenseKey": isometrik_credentials.get("licenseKey", ""),
            "appSecret": isometrik_credentials.get("appSecret", ""),
        }

        url = f"{shared_settings.isometrik.api_url}/chat/user"

        response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.patch(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

    except Exception as e:
        _handle_isometrik_error(e, "updating Isometrik user", response)


async def create_isometrik_ai_agent(
    *,
    payload: dict[str, Any],
    app_secret: str,
    license_key: str,
) -> dict[str, Any]:
    """POST to Isometrik admin ``/v1/ai-agent`` using org application credentials."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "appsecret": app_secret,
        "licensekey": license_key,
    }
    response: httpx.Response | None = None
    try:
        client = await get_strands_http_client()
        response = await client.post(_AI_AGENT_PATH, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("isometrik ai-agent response must be a JSON object")
        return body
    except Exception as e:
        _handle_isometrik_error(e, "creating Isometrik AI agent", response)
