"""
Organization Invite Utilities Module

This module provides utility functions for organization invitation management.
These utilities handle validation, processing, and common operations for invites.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Utilities Covered:
- Invitation validation
- Email processing
- Token validation
- Status management
- Organization member checks
"""

import re
import hashlib
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from fastapi import HTTPException, status
from apps.user_service.app.dependencies.common_utils import UserContext
from apps.user_service.app.dependencies.logger import get_logger

# Initialize logger
logger = get_logger("invite_utils")


# ============================================================================
# VALIDATION UTILITIES
# ============================================================================

def validate_email_format(email: str) -> bool:
    """
    Validate email format using regex.

    Args:
        email (str): Email address to validate

    Returns:
        bool: True if email format is valid
    """
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(email_pattern, email))


def validate_role(role: str) -> bool:
    """
    Validate invitation role.

    Args:
        role (str): Role to validate

    Returns:
        bool: True if role is valid
    """
    valid_roles = ["owner", "admin", "member"]
    return role.lower() in valid_roles


def validate_invite_status(status: str) -> bool:
    """
    Validate invitation status.

    Args:
        status (str): Status to validate

    Returns:
        bool: True if status is valid
    """
    valid_statuses = ["pending", "accepted", "rejected", "expired", "revoked"]
    return status.lower() in valid_statuses


def validate_expiration_days(days: int) -> bool:
    """
    Validate expiration days for invitation.

    Args:
        days (int): Number of days until expiration

    Returns:
        bool: True if days is valid
    """
    return 1 <= days <= 30


# ============================================================================
# INVITATION PROCESSING UTILITIES
# ============================================================================

def is_invite_expired(expires_at: str) -> bool:
    """
    Check if invitation is expired.

    Args:
        expires_at (str): ISO datetime string of expiration

    Returns:
        bool: True if invitation is expired
    """
    try:
        expiration_time = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        # If expiration_time is naive, make it timezone-aware
        if expiration_time.tzinfo is None:
            expiration_time = expiration_time.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expiration_time
    except (ValueError, AttributeError):
        logger.warning("Invalid expiration date format: %s", expires_at)
        return True


def can_resend_invite(invite_data: Dict[str, Any]) -> bool:
    """
    Check if invitation can be resent.

    Args:
        invite_data (dict): Invitation data

    Returns:
        bool: True if invitation can be resent
    """
    status = invite_data.get("status", "").lower()
    expires_at = invite_data.get("expires_at")

    # Can resend if status is pending and not expired
    if status == "pending" and expires_at:
        return not is_invite_expired(expires_at)

    return False


def can_revoke_invite(invite_data: Dict[str, Any]) -> bool:
    """
    Check if invitation can be revoked.

    Args:
        invite_data (dict): Invitation data

    Returns:
        bool: True if invitation can be revoked
    """
    status = invite_data.get("status", "").lower()
    return status in ["pending", "accepted"]


# ============================================================================
# INVITATION RESPONSE BUILDING
# ============================================================================

def build_invite_details_response(invite_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build invitation details response.

    Args:
        invite_data (dict): Invitation data from database

    Returns:
        dict: Formatted invitation details
    """
    if not invite_data:
        return {
            "valid": False,
            "error": "Invitation not found"
        }

    # Check if invitation is expired
    if is_invite_expired(invite_data.get("expires_at", "")):
        return {
            "valid": False,
            "error": "Invitation has expired"
        }

    # # Check if invitation is already used
    # status = invite_data.get("status", "").lower()
    # if status == "accepted":
    #     return {
    #         "valid": False,
    #         "error": "Invitation has already been accepted"
    #     }
    # elif status == "rejected":
    #     return {
    #         "valid": False,
    #         "error": "Invitation has been rejected"
    #     }
    # elif status == "revoked":
    #     return {
    #         "valid": False,
    #         "error": "Invitation has been revoked"
    #     }

    # Extract organization data
    org_data = invite_data.get("organizations", {})

    return {
        "valid": True,
        "email": invite_data.get("email"),
        "organization_name": org_data.get("name"),
        "organization_id": invite_data.get("organization_id"),
        "role": invite_data.get("role"),
        "invited_by": invite_data.get("invited_by"),
        "expires_at": invite_data.get("expires_at")
    }


def build_invite_list_item(invite_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build invitation list item for API response.

    Args:
        invite_data (dict): Invitation data from database

    Returns:
        dict: Formatted invitation list item
    """
    return {
        "invite_id": invite_data.get("id"),
        "email": invite_data.get("email"),
        "role_id": invite_data.get("role_id"),
        "status": invite_data.get("status"),
        "invited_by": invite_data.get("invited_by"),
        "expires_at": invite_data.get("expires_at"),
        "created_at": invite_data.get("created_at"),
        "updated_at": invite_data.get("updated_at")
    }


# ============================================================================
# ERROR HANDLING UTILITIES
# ============================================================================

def handle_invite_validation_error(field: str, value: Any, reason: str) -> None:
    """
    Handle invitation validation errors.

    Args:
        field (str): Field name that failed validation
        value (Any): Value that failed validation
        reason (str): Reason for validation failure

    Raises:
        HTTPException: Validation error
    """
    logger.warning("Invitation validation failed - Field: %s, Value: %s, Reason: %s",
                   field, value, reason)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"Invalid {field}: {reason}"
    )


def handle_invite_not_found_error(invite_id: str) -> None:
    """
    Handle invitation not found errors.

    Args:
        invite_id (str): Invitation ID that was not found

    Raises:
        HTTPException: Not found error
    """
    logger.warning("Invitation not found - ID: %s", invite_id)
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Invitation not found"
    )


def handle_invite_permission_error(action: str) -> None:
    """
    Handle invitation permission errors.

    Args:
        action (str): Action that was denied

    Raises:
        HTTPException: Permission error
    """
    logger.warning("Invitation permission denied - Action: %s", action)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Insufficient permissions to {action} invitations"
    )


# ============================================================================
# INVITATION URL GENERATION
# ============================================================================

def generate_invite_url(base_url: str, token: str) -> str:
    """
    Generate invitation acceptance URL.

    Args:
        base_url (str): Base URL of the application
        token (str): Invitation token

    Returns:
        str: Complete invitation URL
    """
    return f"{base_url.rstrip('/')}/invite/accept/{token}"


def extract_token_from_url(url: str) -> Optional[str]:
    """
    Extract token from invitation URL.

    Args:
        url (str): Invitation URL

    Returns:
        Optional[str]: Extracted token or None if not found
    """
    # Simple token extraction from URL pattern
    # Assumes URL format: /invite/accept/{token}
    parts = url.split("/")
    if len(parts) >= 4 and parts[-3] == "invite" and parts[-2] == "accept" and parts[-1]:
        return parts[-1]
    return None


# ============================================================================
# ORGANIZATION MEMBER UTILITIES
# ============================================================================

def check_organization_capacity(organization_data: Dict[str, Any]) -> bool:
    """
    Check if organization has capacity for new members.

    Args:
        organization_data (dict): Organization data

    Returns:
        bool: True if organization has capacity
    """
    max_users = organization_data.get("max_users", 0)
    current_members = organization_data.get("member_count", 0)

    return current_members < max_users


async def validate_organization_access(user_context, organization_id: str) -> bool:
    """
    Validate if user has access to organization.

    Args:
        user_context (dict): User context from JWT
        organization_id (str): Organization ID

    Returns:
        bool: True if user has access
    """
    user_org_id = user_context if isinstance(user_context, UserContext) else await user_context
    return user_org_id.organization_id == organization_id


# ============================================================================
# INVITATION STATUS TRANSITIONS
# ============================================================================

def get_valid_status_transitions(current_status: str) -> list:
    """
    Get valid status transitions for invitation.

    Args:
        current_status (str): Current invitation status

    Returns:
        list: List of valid next statuses
    """
    transitions = {
        "pending": ["accepted", "rejected", "expired", "revoked"],
        "accepted": ["revoked"],  # Can only revoke accepted invitations
        "rejected": [],  # No transitions from rejected
        "expired": [],  # No transitions from expired
        "revoked": []  # No transitions from revoked
    }

    return transitions.get(current_status.lower(), [])


def is_valid_status_transition(current_status: str, new_status: str) -> bool:
    """
    Check if status transition is valid.

    Args:
        current_status (str): Current invitation status
        new_status (str): New invitation status

    Returns:
        bool: True if transition is valid
    """
    valid_transitions = get_valid_status_transitions(current_status)
    return new_status.lower() in valid_transitions


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def hash_token(token: str) -> str:
    """Hash token using SHA256 for secure storage"""
    return hashlib.sha256(token.encode()).hexdigest()
