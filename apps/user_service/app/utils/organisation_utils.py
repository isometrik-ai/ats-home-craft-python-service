"""Organisation Management Utilities Module.

This module provides specialized utility functions for organisation management operations.
These utilities handle organisation-specific validations, database operations, and business logic.

Organisation-Specific Operations Covered:
1. Organisation existence checking
2. Organisation slug uniqueness validation
3. Organisation status validation
4. Organisation creation helpers
5. Organisation query building
6. Default permissions and roles setup
"""

from datetime import datetime, timezone
from typing import Any

from apps.user_service.app.dependencies.logger import get_logger

# Local imports
from apps.user_service.app.utils.common_utils import ORG_STATUSES
from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    add_member_to_organisation,
    assign_all_permissions_to_role,
    create_default_permissions_for_organisation,
    create_new_organisation,
    create_super_admin_role,
    get_organisation_members_count,
)
from libs.shared_utils.http_exceptions import (
    ConflictException,
    ForbiddenException,
    ServiceUnavailableException,
    ValidationException,
)
from libs.shared_utils.isometrik_service import (
    create_isometrik_application,
    is_isometrik_enabled,
)
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("organisation_utils")


async def _create_isometrik_application_if_enabled(
    organization_name: str,
) -> dict[str, Any] | None:
    """Create Isometrik application if Isometrik is enabled.

    Args:
        organization_name: Name of the organization

    Returns:
        Isometrik response data if enabled and successful, None otherwise

    Raises:
        ServiceUnavailableException: If Isometrik application creation fails
        Exception: If any other exception occurs
    """
    if not is_isometrik_enabled():
        return None
    try:
        isometrik_response = await create_isometrik_application(
            organization_name=organization_name, product_types=["chat", "video"], plan="basic"
        )
        if not isometrik_response or not isometrik_response.get("data"):
            raise ServiceUnavailableException(
                message_key="errors.isometrik.failed_to_create_application",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )
        return isometrik_response
    except Exception as error:
        raise error


def validate_organisation_status(org_status: str) -> None:
    """Validate organisation status against allowed values.

    Args:
        org_status (str): Organisation status to validate

    Raises:
        ValidationException: 422 for invalid organisation status

    Usage:
        validate_organisation_status(body.status)
    """
    if org_status not in ORG_STATUSES:
        raise ValidationException(
            message_key="organisations.errors.invalid_organisation_status",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"allowed_statuses": ", ".join(ORG_STATUSES)},
        )


async def create_organisation_with_super_admin(org_data: dict[str, Any]) -> None:
    """Create a new organisation with super admin role and default permissions.

    This function performs a multi-step operation:
    1. If Isometrik is enabled, attempt to create Isometrik application
       (non-blocking for connection errors)
    2. Creates the organization record
    3. Creates a super admin role for the organization
    4. Creates default permissions for the organization
    5. Assigns all permissions to the super admin role
    6. Adds the user as an organization member with super admin role
    7. If Isometrik is enabled and application was created, save Isometrik data

    Args:
        org_data: Dictionary containing organization and user data

    Raises:
        Exception: Re-raises any exception that occurs during the operation
        HTTPException:
            - 409: If organization slug already exists (duplicate key violation)
            - 500: For RLS policy violations or other database errors
            - 500: If Isometrik API returns 4xx/5xx errors (configuration issues)
    """
    # Step 0: Create Isometrik application first if enabled (non-blocking for connection errors)
    organization_name = org_data.get("name", "Unknown Organization")
    isometrik_response = await _create_isometrik_application_if_enabled(organization_name)

    isometrik_credentials = isometrik_response.get("data", {}) if isometrik_response else {}

    org_data["isometrik_application_details"] = isometrik_credentials
    # Step 1: Create the organization record
    await create_new_organisation(org_data)

    # Step 2: Create Super Admin role
    super_admin_role_result = await create_super_admin_role(org_data["organization_id"])
    super_admin_role_id = super_admin_role_result["id"]

    # Step 3: Create default permissions
    await create_default_permissions_for_organisation(org_data["organization_id"])

    # Step 4: Assign all permissions to Super Admin role
    await assign_all_permissions_to_role(super_admin_role_id, org_data["organization_id"])

    # Step 5: Add user as organization member
    await add_member_to_organisation(
        organization_id=org_data["organization_id"],
        member_data={
            "user_id": org_data["user_id"],
            "email": org_data["email"],
            "first_name": org_data.get("first_name", None),
            "last_name": org_data.get("last_name", None),
            "phone": org_data.get("phone", None),
            "timezone": org_data.get("timezone", "UTC"),
            "role_id": super_admin_role_id,
            "status": "active",
            "role": "owner",
            "logo_url": org_data.get("logo_url", None),
        },
        isometrik_credentials=isometrik_credentials,
    )


async def validate_organization_subscription(organization_data: dict[str, Any]) -> bool:
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
    subscription = organization_data.get("subscription")

    if not subscription:
        raise ForbiddenException(
            message_key="invitations.errors.organization_subscription_missing",
            custom_code=CustomStatusCode.FORBIDDEN,
        )

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

    total_members = await get_organisation_members_count(organization_id)

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
