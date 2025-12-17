"""Organization Invite Utilities Module.

This module provides utility functions for organization invitation management.
These utilities handle validation, processing, and common operations for invites.
"""

import hashlib
from typing import Any

from apps.user_service.app.dependencies.logger import get_logger

# Initialize logger
logger = get_logger("invite_utils")


def build_invite_list_item(invite_data: dict[str, Any]) -> dict[str, Any]:
    """Build invitation list item for API response.

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
        "updated_at": invite_data.get("updated_at"),
        "salutation": invite_data.get("metadata", {}).get("salutation", None),
        "first_name": invite_data.get("metadata", {}).get("first_name", None),
        "last_name": invite_data.get("metadata", {}).get("last_name", None),
        "phone": invite_data.get("metadata", {}).get("phone", None),
    }


def hash_token(token: str) -> str:
    """Hash token using SHA256 for secure storage"""
    return hashlib.sha256(token.encode()).hexdigest()
