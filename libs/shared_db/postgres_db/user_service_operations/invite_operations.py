"""
Organization Invite Database Operations Module

This module contains all organization invitation-related database operations.
All SQL queries for invitation management should be centralized here.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Operations Covered:
- Invitation CRUD operations
- Invitation status management
- Invitation validation operations
- Invitation search and filtering
- Invitation token management
"""

import secrets
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.dependencies.invite_utils import hash_token
from libs.shared_db.supabase_db.db import get_supabase_admin_client
from libs.shared_db.postgres_db.user_service_operations.exception_handling import (
    handle_database_errors, create_error_messages
)
from libs import NOW_CONSTANT
from libs.shared_db.postgres_db.user_service_operations.user_operations import create_new_user
from libs.shared_db.supabase_db.admin_operations.user import get_user_by_id

# Initialize logger
logger = get_logger("invite_operations")


# ============================================================================
# INVITATION CRUD OPERATIONS
# ============================================================================

@handle_database_errors(
    "create_organization_invite",
    custom_messages=create_error_messages("create_organization_invite", "creating"))
async def create_organization_invite(invite_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new organization invitation."""
    supabase = await get_supabase_admin_client()

    # Generate secure token
    invite_token = secrets.token_urlsafe(32)
    token_hash = hash_token(invite_token)

    # Calculate expiration date
    expires_at = datetime.now() + timedelta(days=invite_data.get("expires_in_days", 7))

    invite_record = {
        "organization_id": invite_data["organization_id"],
        "email": invite_data["email"],
        "role_id": str(invite_data["role_id"]),
        "token_hash": token_hash,
        "invited_by": invite_data["invited_by"],
        "status": "pending",
        "expires_at": expires_at.isoformat(),
        "created_at": NOW_CONSTANT,
        "updated_at": NOW_CONSTANT
    }

    table = supabase.table("organization_invites")
    insert_query = table.insert(invite_record)
    result = await insert_query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return {}


@handle_database_errors(
    "get_invite_by_token",
    custom_messages=create_error_messages("get_invite_by_token", "getting"))
async def get_invite_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Get invitation details by token."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    select_query = table.select(
        "id, organization_id, email, role_id, token_hash, invited_by, "
        "status, expires_at, created_at, updated_at, "
        "organizations(name, slug, domain)"
    )
    eq_query = select_query.eq("token_hash", token)
    limit_query = eq_query.limit(1)
    result = await limit_query.execute()

    if not result.data or len(result.data) == 0:
        return None

    return result.data[0]


@handle_database_errors(
    "get_invite_by_id",
    custom_messages=create_error_messages("get_invite_by_id", "getting"))
async def get_invite_by_id(invite_id: str) -> Optional[Dict[str, Any]]:
    """Get invitation details by ID."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    select_query = table.select(
        "id, organization_id, email, role_id, invited_by, "
        "token_hash, status, expires_at, created_at, updated_at, "
        "organizations(name, slug, domain)"
    )
    eq_query = select_query.eq("id", invite_id)
    limit_query = eq_query.limit(1)
    result = await limit_query.execute()

    if not result.data or len(result.data) == 0:
        return None

    return result.data[0]


@handle_database_errors(
    "get_organization_invites",
    custom_messages=create_error_messages("get_organization_invites", "getting"))
async def get_organization_invites(
    organization_id: str,
    limit: int = 20,
    offset: int = 0
) -> List[Dict[str, Any]]:
    """Get all invitations for an organization with optional filtering."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    select_query = table.select(
        "id, organization_id, email, role_id, invited_by, "
        "status, expires_at, created_at, updated_at"
    )
    eq_query = select_query.eq("organization_id", organization_id)

    order_query = eq_query.order("created_at", desc=True)
    limit_query = order_query.limit(limit)
    offset_query = limit_query.offset(offset)
    result = await offset_query.execute()

    return result.data if result.data else []

@handle_database_errors(
    "get_organization_invites_count",
    custom_messages=create_error_messages("get_organization_invites_count", "getting"))
async def get_organization_invites_count(
    organization_id: str
) -> int:
    """Get count of invitations for an organization."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    count_query = table.select("id", count="exact")
    eq_query = count_query.eq("organization_id", organization_id)

    result = await eq_query.execute()
    return result.count if result.count else 0


@handle_database_errors(
    "update_invite_status",
    custom_messages=create_error_messages("update_invite_status", "updating"))
async def update_invite_status(
    invite_id: str,
    status: str,
    accepted_by: Optional[str] = None
) -> bool:
    """Update invitation status."""
    supabase = await get_supabase_admin_client()

    update_data = {
        "status": status,
        "updated_at": NOW_CONSTANT
    }

    if accepted_by:
        update_data["accepted_by"] = accepted_by

    table = supabase.table("organization_invites")
    update_query = table.update(update_data)
    eq_query = update_query.eq("id", invite_id)
    result = await eq_query.execute()

    return result.data is not None and len(result.data) > 0


@handle_database_errors(
    "delete_invite",
    custom_messages=create_error_messages("delete_invite", "deleting"))
async def delete_invite(invite_id: str) -> bool:
    """Delete an invitation."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    delete_query = table.delete()
    eq_query = delete_query.eq("id", invite_id)
    result = await eq_query.execute()

    return result.data is not None and len(result.data) > 0


@handle_database_errors(
    "check_existing_invite",
    custom_messages=create_error_messages("check_existing_invite", "checking"))
async def check_existing_invite(
    organization_id: str,
    email: str,
    status: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Check if an invitation already exists for the email and organization."""
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


@handle_database_errors(
    "check_user_membership",
    custom_messages=create_error_messages("check_user_membership", "checking"))
async def check_user_membership(
    organization_id: str,
    email: str
) -> Optional[Dict[str, Any]]:
    """Check if user is already a member of the organization."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_members")
    select_query = table.select("*")
    eq_query = select_query.eq("organization_id", organization_id).eq("email", email)
    limit_query = eq_query.limit(1)
    result = await limit_query.execute()

    if result.data and len(result.data) > 0:
        return result.data[0]
    return None


@handle_database_errors(
    "add_user_to_organization",
    custom_messages=create_error_messages("add_user_to_organization", "adding"))
async def add_user_to_organization(
    organization_id: str,
    user_id: str,
    email: str,
    role_id: str,
    role_name: str,
    invited_by: str
) -> Dict[str, Any]:
    """Add user to organization as a member."""
    user_data = await get_user_by_id(user_id)

    member_record = {
        "organization_id": organization_id,
        "user_id": user_id,
        "email": email,
        "full_name": user_data.user.user_metadata.get("full_name", None),
        "first_name": user_data.user.user_metadata.get("first_name", None),
        "last_name": user_data.user.user_metadata.get("last_name", None),
        "phone": user_data.user.user_metadata.get("phone", None),
        "timezone": user_data.user.user_metadata.get("timezone", "UTC"),
        "role_id": role_id,
        "role": role_name,
        "status": "active",
        "invited_by": invited_by,
        "joined_at": NOW_CONSTANT,
        "created_at": NOW_CONSTANT,
        "updated_at": NOW_CONSTANT
    }

    result = await create_new_user(member_record)

    return result


@handle_database_errors(
    "get_expired_invites",
    custom_messages=create_error_messages("get_expired_invites", "getting"))
async def get_expired_invites() -> List[Dict[str, Any]]:
    """Get all expired invitations."""
    supabase = await get_supabase_admin_client()

    current_time = datetime.now().isoformat()

    table = supabase.table("organization_invites")
    select_query = table.select("*")
    lt_query = select_query.lt("expires_at", current_time)
    eq_query = lt_query.eq("status", "pending")
    result = await eq_query.execute()

    return result.data or []


@handle_database_errors(
    "cleanup_expired_invites",
    custom_messages=create_error_messages("cleanup_expired_invites", "cleaning"))
async def cleanup_expired_invites() -> int:
    """Mark expired invitations as expired."""
    supabase = await get_supabase_admin_client()

    table = supabase.table("organization_invites")
    update_query = table.update({"status": "expired", "updated_at": NOW_CONSTANT})
    lt_query = update_query.lt("expires_at", NOW_CONSTANT)
    eq_query = lt_query.eq("status", "pending")
    result = await eq_query.execute()

    return len(result.data) if result.data else 0
