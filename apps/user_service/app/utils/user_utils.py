"""Permmissions Management Utilities Module."""

from datetime import datetime
from typing import Any

from apps.user_service.app.schemas.users import (
    PermissionInfo,
    RoleInfo,
    RoleInfoWithDescription,
    UserProfileData,
    VerificationPreference,
)
from libs.shared_utils.logger import get_logger  # Logger import

# Initialize logger
logger = get_logger("user-utils")


def build_full_name(*parts: str) -> str:
    """Build a full name from parts.

    Args:
        *parts: Parts of the full name

    Returns:
        str: Full name
    """
    return " ".join(filter(None, parts))


def create_user_profile_data(
    user_profile: dict[str, Any],
    user_type: str = "organization_member",
    role_info: RoleInfo | RoleInfoWithDescription | None = None,
    permissions: list[PermissionInfo] | None = None,
) -> UserProfileData:
    """Creates a UserProfileData object from user profile data.
    This is the single source of truth for creating user profile responses.

    Args:
        user_profile: User profile data from database
        user_type: Type of user (default: organization_member)
        role_info: Optional role information
        permissions: Optional list of permissions

    Returns:
        UserProfileData object with formatted user profile
    """
    # Extract verification_preference from user_profile dict
    verification_preference = None
    verification_pref_data = user_profile.get("verification_preference")
    if verification_pref_data and isinstance(verification_pref_data, dict):
        verification_preference = VerificationPreference(
            two_fa_enabled=verification_pref_data.get("enabled", False),
            verification_method=verification_pref_data.get("type", ""),
        )

    return UserProfileData(
        user_id=str(user_profile["user_id"]),
        email=user_profile["email"],
        full_name=user_profile["full_name"],
        first_name=user_profile["first_name"],
        last_name=user_profile["last_name"],
        avatar_url=user_profile["avatar_url"],
        phone=user_profile["phone"],
        timezone=user_profile["timezone"] or "UTC",
        salutation=user_profile.get("salutation", None),
        status=user_profile["status"],
        joined_at=(
            user_profile["joined_at"].isoformat()
            if user_profile["joined_at"] and isinstance(user_profile["joined_at"], datetime)
            else datetime.now().isoformat()
        ),
        last_active_at=(
            user_profile["last_active_at"].isoformat()
            if user_profile["last_active_at"]
            and isinstance(user_profile["last_active_at"], datetime)
            else user_profile["last_active_at"]
        ),
        organization_id=str(user_profile["organization_id"]),
        user_type=user_type,
        role=role_info,
        permissions=permissions or [],
        identities=user_profile["identities"],
        verification_preference=verification_preference,
    )
