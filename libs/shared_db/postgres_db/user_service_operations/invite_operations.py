"""Organization Invite Database Operations Module
This module contains all organization invitation-related database operations.
All SQL queries for invitation management should be centralized here.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from apps.user_service.app.config.app_settings import app_settings
from apps.user_service.app.utils.invite_utils import hash_token
from libs.shared_db.postgres_db.user_service_operations.user_operations import (
    create_new_user,
)
from libs.shared_db.supabase_db.db import (
    get_fresh_supabase_admin_client,
    get_supabase_admin_client,
)
from libs.shared_utils.http_exceptions import ServiceUnavailableException
from libs.shared_utils.isometrik_service import create_isometrik_user
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("invite_operations")


async def create_organization_invite(invite_data: dict[str, Any]) -> dict[str, Any]:
    """Create a new organization invitation.
    Args:
        invite_data: Invite data
    Returns:
        dict containing the new organization invitation
    """
    supabase = await get_supabase_admin_client()

    invite_token = secrets.token_urlsafe(32)
    token_hash = hash_token(invite_token)

    expires_at = datetime.now() + timedelta(days=app_settings.invite_expiry_days)

    invite_record = {
        "organization_id": invite_data["organization_id"],
        "email": invite_data["email"],
        "role_id": str(invite_data["role_id"]),
        "token_hash": token_hash,
        "invited_by": invite_data["invited_by"],
        "status": "pending",
        "expires_at": expires_at.isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "metadata": {
            "first_name": invite_data["first_name"],
            "last_name": invite_data["last_name"],
            "phone": invite_data["phone"],
            "salutation": invite_data["salutation"],
        },
    }

    table = supabase.table("organization_invites")
    insert_query = table.insert(invite_record)
    result = await insert_query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


async def get_invite_by_token(token: str) -> dict[str, Any] | None:
    """Get invitation details by token.
    Args:
        token: Token
    Returns:
        dict containing the invitation details or None if not found
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    select_query = table.select(
        "id, organization_id, email, role_id, token_hash, invited_by, "
        "status, expires_at, created_at, updated_at, metadata, "
        "organizations(name, slug, domain)"
    )
    eq_query = select_query.eq("token_hash", token)
    limit_query = eq_query.limit(1)
    result = await limit_query.execute()

    if not result.data or len(result.data) == 0:
        return None

    return result.data[0]


async def get_invite_by_id(invite_id: str) -> dict[str, Any] | None:
    """Get invitation details by ID.
    Args:
        invite_id: Invite ID
    Returns:
        dict containing the invitation details or None if not found
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    select_query = table.select(
        "id, organization_id, email, role_id, invited_by, "
        "token_hash, status, expires_at, created_at, updated_at, "
        "organizations(name, slug, domain), metadata"
    )
    eq_query = select_query.eq("id", invite_id)
    limit_query = eq_query.limit(1)
    result = await limit_query.execute()

    if not result.data or len(result.data) == 0:
        return None

    return result.data[0]


async def get_organization_invites(
    organization_id: str, limit: int = 20, offset: int = 0, status: str | None = None
) -> list[dict[str, Any]]:
    """Get all invitations for an organization with optional filtering.
    Args:
        organization_id: Organization ID
        limit: Limit
        offset: Offset
        status: Status
    Returns:
        list of invitations
    """
    supabase = await get_fresh_supabase_admin_client()

    table = supabase.table("organization_invites")
    select_query = table.select(
        "id, organization_id, email, role_id, invited_by, "
        "status, expires_at, created_at, updated_at, metadata"
    )
    eq_query = select_query.eq("organization_id", organization_id)

    if status:
        eq_query = eq_query.eq("status", status)

    order_query = eq_query.order("created_at", desc=True)
    limit_query = order_query.limit(limit)
    offset_query = limit_query.offset(offset)
    result = await offset_query.execute()

    return result.data if result.data else []


async def get_organization_invites_count(organization_id: str, status: str | None = None) -> int:
    """Get count of invitations for an organization.
    Args:
        organization_id: Organization ID
        status: Status
    Returns:
        int: Total count of invitations
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    count_query = table.select("id", count="exact")
    eq_query = count_query.eq("organization_id", organization_id)

    if status:
        eq_query = eq_query.eq("status", status)

    result = await eq_query.execute()
    return result.count if result.count else 0


async def update_invite_status(invite_id: str, status: str, accepted_by: str | None = None) -> bool:
    """Update invitation status.
    Args:
        invite_id: Invite ID
        status: Status
        accepted_by: Accepted by
    Returns:
        bool: True if invitation status was updated successfully, False otherwise
    """
    supabase = await get_fresh_supabase_admin_client()

    update_data = {"status": status, "updated_at": datetime.now(UTC).isoformat()}

    if accepted_by:
        update_data["accepted_by"] = accepted_by
        update_data["accepted_at"] = datetime.now(UTC).isoformat()

    table = supabase.table("organization_invites")
    update_query = table.update(update_data)
    eq_query = update_query.eq("id", invite_id)
    result = await eq_query.execute()

    return result.data is not None and len(result.data) > 0


async def delete_invite(invite_id: str) -> bool:
    """Delete an invitation.
    Args:
        invite_id: Invite ID
    Returns:
        bool: True if invitation was deleted successfully, False otherwise
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    delete_query = table.delete()
    eq_query = delete_query.eq("id", invite_id)
    result = await eq_query.execute()

    return result.data is not None and len(result.data) > 0


async def check_existing_invite(
    organization_id: str, email: str, status: str | None = None
) -> dict[str, Any] | None:
    """Check if an invitation already exists for the email and organization.
    Args:
        organization_id: Organization ID
        email: Email
        status: Status
    Returns:
        dict containing the invitation details or None if not found
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    select_query = table.select("*")
    eq_query = select_query.eq("organization_id", organization_id).eq("email", email)

    if status:
        eq_query = eq_query.eq("status", status)

    limit_query = eq_query.limit(1)
    result = await limit_query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


async def check_user_membership(organization_id: str, email: str) -> dict[str, Any] | None:
    """Check if user is already a member of the organization.
    Args:
        organization_id: Organization ID
        email: Email
    Returns:
        dict containing the user membership details or None if not found
    """
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_members")
    select_query = table.select("*")
    eq_query = select_query.eq("organization_id", organization_id).eq("email", email)
    limit_query = eq_query.limit(1)
    result = await limit_query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


async def add_user_to_organization(
    organization_id: str,
    invite_data: dict[str, Any],
    email: str,
    role_id: str,
    role_name: str,
    invited_by: str,
    isometrik_credentials: dict[str, Any],
) -> dict[str, Any]:
    """Add user to organization as a member.
    Args:
        organization_id: Organization ID
        invite_data: Invite data
        email: Email
        role_id: Role ID
        role_name: Role name
        invited_by: Invited by
        isometrik_credentials: Isometrik credentials
    Returns:
        dict containing the new user
    """

    isometrik_user_id = None
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
        "organization_id": organization_id,
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
        "joined_at": datetime.now(UTC).isoformat(),
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }

    result = await create_new_user(member_record)

    return result
