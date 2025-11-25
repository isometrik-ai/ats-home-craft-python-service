"""
Permmissions Management Utilities Module
"""
from datetime import datetime
from typing import Optional, List, Union, Dict, Any

from apps.user_service.app.dependencies.logger import get_logger  # Logger import
from apps.user_service.app.schemas.users import (
    RoleInfo,
    PermissionInfo,
    RoleInfoWithDescription,
    UserProfileData,
    VerificationPreference
)

# Initialize logger
logger = get_logger("user-utils")

def create_user_profile_data(
    user_profile: Dict[str, Any],
    user_type: str = "organization_member",
    role_info: Optional[Union[RoleInfo, RoleInfoWithDescription]] = None,
    permissions: Optional[List[PermissionInfo]] = None,
) -> UserProfileData:
    """
    Creates a UserProfileData object from user profile data.
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
        try:
            verification_preference = VerificationPreference(
                enabled=verification_pref_data.get("enabled", False),
                type=verification_pref_data.get("type", "")
            )
        except Exception as e:
            logger.warning("Failed to parse verification_preference: %s", str(e))
            verification_preference = None

    return UserProfileData(
        user_id=str(user_profile["user_id"]),
        email=user_profile["email"],
        full_name=user_profile["full_name"],
        first_name=user_profile["first_name"],
        last_name=user_profile["last_name"],
        avatar_url=user_profile["avatar_url"],
        phone=user_profile["phone"],
        timezone=user_profile["timezone"] or "UTC",
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
        verification_preference=verification_preference
    )
