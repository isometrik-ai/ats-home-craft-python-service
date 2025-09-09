"""
Permmissions Management Utilities Module
"""
from datetime import datetime
from typing import Optional, Tuple, List, Union, Dict, Any

import asyncpg
from fastapi import HTTPException, status
from asyncpg import Record

from apps.user_service.app.dependencies.logger import get_logger  # Logger import
from apps.user_service.app.schemas.users import (
    RoleInfo,
    PermissionInfo,
    UserListItem,
    RoleInfoWithDescription,
    UserProfileData,
    UpdateUserRequest,
)
from libs.shared_utils.email_utils import send_email

# Initialize logger
logger = get_logger("user-utils")


def generate_magic_link(supabase_client, email: str) -> Optional[str]:
    """
    Generate a magic link using Supabase Auth Admin API generateLink.

    Args:
        supabase_client: Supabase client instance
        email (str): User's email address

    Returns:
        Optional[str]: Generated magic link URL or None if failed
    """
    try:
        response = supabase_client.auth.admin.generate_link(
            {
                "type": "magiclink",
                "email": email,
            }
        )

        logger.debug("Magic link generation response: %s", response)

        if response and hasattr(response, "properties") and response.properties:
            magic_link = response.properties.action_link
            logger.debug("Generated magic link: %s", magic_link)
            if magic_link:
                logger.info(
                    "Magic link generated successfully using Supabase client for %s",
                    email,
                )
                return magic_link

        logger.error("Magic link not found in Supabase client response")
        return None

    except (ValueError, AttributeError) as error:
        logger.error("Error generating magic link with Supabase client: %s", str(error))
        return None
    except Exception as error:
        logger.error("Unexpected error generating magic link: %s", str(error))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate magic link"
        ) from error


def create_admin_update_email_content(user: dict, magic_link: str) -> Tuple[str, str]:
    """
    Create email subject and content for admin update notification with magic link.

    Args:
        user (dict): User information containing full_name, email
        magic_link (str): Generated magic link for authentication

    Returns:
        Tuple[str, str]: Email subject and HTML message content
    """
    full_name = user.get("full_name", "")

    subject = "Your Email Has Been Updated - XQtiv"

    html_message = f"""
    <div style="font-family: Arial, sans-serif !important; font-size: 14px !important; color: #333333 !important; line-height: 1.6 !important; max-width: 600px !important;">
        <p style="margin: 0 0 16px 0 !important; color: #333333 !important;">
            Hello {full_name},
        </p>

        <p style="margin: 0 0 16px 0 !important; color: #333333 !important;">
            Your email id has been updated by the admin.
        </p>

        <p style="margin: 0 0 16px 0 !important; color: #333333 !important;">
            Login using the link below:
        </p>

        <div style="text-align: center !important; margin: 24px 0 !important;">
            <a href="{magic_link}"
               style="background-color: #3498db !important; color: white !important; padding: 12px 24px !important; text-decoration: none !important; border-radius: 6px !important; display: inline-block !important; font-weight: bold !important;">
                Magic Link
            </a>
        </div>

        <p style="margin: 0 0 16px 0 !important; color: #333333 !important;">
            If the button doesn't work, you can copy and paste this link into your browser:
        </p>

        <p style="margin: 0 0 16px 0 !important; color: #3498db !important; word-break: break-all !important;">
            {magic_link}
        </p>

        <p style="margin: 0 0 16px 0 !important; color: #333333 !important;">
            Best regards,<br>
            Team XQtiv
        </p>

        <hr style="border: none !important; border-top: 1px solid #dee2e6 !important; margin: 24px 0 !important;">

        <p style="font-size: 11px !important; color: #868e96 !important; margin: 0 !important;">
            This is an automated notification from XQtiv. Please do not reply to this email.
        </p>
    </div>
    """

    return subject, html_message.strip()


def send_admin_update_email(supabase_client, user: dict) -> bool:
    """
    Send admin update notification email with magic link.

    Args:
        supabase_client: Supabase client instance
        user (dict): User information containing id, full_name, email

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    try:
        # Generate magic link using Supabase Auth Admin API
        magic_link = generate_magic_link(supabase_client, user.get("email"))
        logger.debug("Generated magic link for email update: %s", magic_link)

        if magic_link is None:
            logger.error("Failed to generate magic link for %s", user.get("email"))
            return False

        # Create email content with the actual magic link
        subject, html_message = create_admin_update_email_content(user, magic_link)

        # Send email with HTML content
        email_sent = send_email(
            user.get("email"),
            subject,
            "Please check the HTML version of this email.",
            html_message,
        )

        if email_sent:
            logger.info("Admin update email sent successfully to %s", user.get("email"))
            return True

        logger.error("Failed to send admin update email to %s", user.get("email"))
        return False

    except (ValueError, AttributeError) as error:
        logger.error("Error sending admin update email - validation error: %s", str(error))
        return False
    except Exception as error:
        logger.error("Unexpected error sending admin update email: %s", str(error))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to send admin update email"
        ) from error


def build_user_query(organization_id, search, page_size, offset):
    """
    Build Query Get User Lists and Get count Query
    """
    base_query = """
        SELECT
            om.user_id, om.email, om.full_name, om.status, om.first_name,
            om.last_name, om.phone, om.joined_at, om.last_active_at,
            r.name as role_name, r.id as role_id
        FROM public.organization_members om
        INNER JOIN public.roles r
            ON om.role_id = r.id AND om.organization_id = r.organization_id
        WHERE om.organization_id = $1 AND r.name != 'Super Admin'
    """

    count_query = """
        SELECT COUNT(*)
        FROM public.organization_members om
        INNER JOIN public.roles r
            ON om.role_id = r.id AND om.organization_id = r.organization_id
        WHERE om.organization_id = $1 AND r.name != 'Super Admin'
    """

    query_args = [organization_id]

    if search:
        base_query += """
            AND (
                om.full_name ILIKE $2 OR
                om.first_name ILIKE $2 OR
                om.last_name ILIKE $2
            )
        """
        count_query += """
            AND (
                om.full_name ILIKE $2 OR
                om.first_name ILIKE $2 OR
                om.last_name ILIKE $2
            )
        """
        query_args.append(f"%{search}%")

    limit_index = len(query_args) + 1
    offset_index = len(query_args) + 2
    base_query += (
        f" ORDER BY om.joined_at DESC LIMIT ${limit_index} OFFSET ${offset_index};"
    )

    return {
        "base_query": base_query,
        "count_query": count_query,
        "query_args": query_args,
        "limit_offset_args": [page_size, offset],
    }


async def transform_users(users_data, organization_id, db_conn):
    """
    Build Proper response for User list
    """
    if not users_data:
        return []

    # Use role_id from first user for permission count
    role_id = users_data[0]["role_id"]
    result = await db_conn.fetchrow(
        """
        SELECT COUNT(*)
        FROM public.role_permissions
        WHERE organization_id = $1 AND role_id = $2;
        """,
        organization_id,
        role_id,
    )
    permissions_count = result["count"] if result else 0

    # Convert DB rows to response objects
    return [
        UserListItem(
            user_id=str(u["user_id"]),
            email=u["email"],
            full_name=u["full_name"],
            first_name=u["first_name"],
            last_name=u["last_name"],
            phone=u["phone"],
            role_name=u["role_name"],
            role_id=str(u["role_id"]),
            status=u["status"],
            joined_at=(
                u["joined_at"].isoformat()
                if u["joined_at"]
                else datetime.now().isoformat()
            ),
            last_active_at=(
                u["last_active_at"].isoformat() if u["last_active_at"] else None
            ),
            permissions_count=permissions_count,
        )
        for u in users_data
    ]


async def update_supabase_user_email(
    user_id: str, organization_id: str, email: str, supabase_client, db_conn
):
    """
    Update user email and send magic link notification
    """
    try:
        # Get user information before updating email for email notification
        user_info_query = """
            SELECT full_name, last_name, email as old_email
            FROM public.organization_members
            WHERE user_id = $1 AND organization_id = $2
        """
        user_info = await db_conn.fetchrow(user_info_query, user_id, organization_id)

        if user_info is None:
            raise HTTPException(status_code=404, detail="Member not found")

        # Update user email in Supabase Auth
        response = supabase_client.auth.admin.update_user_by_id(
            user_id, {"email": email}
        )

        # Check if the update was successful
        if response.user is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update user email",
            )

        # Update email in organization_members table
        email_update_query = """
            UPDATE public.organization_members
            SET    email      = $1,
                updated_at = NOW()
            WHERE  user_id          = $2
            AND  organization_id  = $3
            RETURNING id, email, updated_at;
        """

        result = await db_conn.fetchrow(
            email_update_query, email, user_id, organization_id
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Member not found")

        # Prepare user data for email notification
        user_data = {
            "id": user_id,
            "full_name": user_info.get("full_name", ""),
            "email": email,  # Use the new email
        }

        # Send magic link email notification
        logger.info("Sending magic link email notification to user %s", user_id)
        email_sent = send_admin_update_email(supabase_client, user_data)

        if email_sent:
            logger.info("Magic link email sent successfully to %s", email)
        else:
            logger.warning("Failed to send magic link email to %s", email)
            # Note: We don't fail the entire operation if email fails
            # The email update was successful, only the notification failed

    except HTTPException:  # ⬅️ re-raise FastAPI errors untouched
        raise
    except Exception as e:  # ⬅️ handle every other failure
        logger.error("Error updating Supabase user email: %s", str(e))
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error"
        ) from e


def format_permissions(permissions_data):
    """
    Convert raw permission rows to a list of PermissionInfo objects.
    """
    return [
        PermissionInfo(
            permission_id=str(p["permission_id"]),
            permission_name=p["permission_name"],
            permission_code=p["permission_code"],
            category=p["category"],
        )
        for p in permissions_data
    ]


def format_role_with_description(user_profile):
    """
    Extract role info from user profile and return as RoleInfo.
    """
    return RoleInfoWithDescription(
        role_id=str(user_profile["role_id"]),
        role_name=user_profile["role_name"],
        description=user_profile["role_description"],
    )


def format_role(user_profile):
    """
    Extract role info from user profile and return as RoleInfo.
    """
    return RoleInfo(
        role_id=str(user_profile["role_id"]),
        role_name=user_profile["role_name"],
    )


def format_timestamps(user_profile):
    """
    Format joined_at and last_active_at fields as ISO strings.
    """
    joined_at = (
        user_profile["joined_at"].isoformat()
        if user_profile["joined_at"]
        else datetime.utcnow().isoformat()
    )
    last_active_at = (
        user_profile["last_active_at"].isoformat()
        if user_profile["last_active_at"]
        else None
    )
    return joined_at, last_active_at


async def update_user_activity(db_conn, user_id: str, org_id: str) -> None:
    """
    Update user's last_active_at and updated_at timestamps.
    This is a non-critical background operation that logs errors but doesn't raise them.

    Args:
        db_conn: Database connection
        user_id: User ID to update
        org_id: Organization ID for the user

    Note:
        This function intentionally catches and logs all errors without raising them
        since it's used for background activity tracking that shouldn't interrupt the main flow.
    """
    if not isinstance(user_id, str) or not isinstance(org_id, str):
        logger.warning(
            "Invalid type for user_id or org_id. Expected str, got %s and %s",
            type(user_id),
            type(org_id)
        )
        return

    update_query = """
        UPDATE public.organization_members
        SET last_active_at = NOW(), updated_at = NOW()
        WHERE user_id = $1 AND organization_id = $2 AND status = 'active';
    """

    try:
        await db_conn.execute(update_query, user_id, org_id)
    except asyncpg.exceptions.DataError as err:
        # Handles issues with data format/content
        logger.warning("Data error updating user activity: %s", str(err))
    except asyncpg.exceptions.ForeignKeyViolationError as err:
        # Handles foreign key violations
        logger.warning("Foreign key violation updating user activity: %s", str(err))
    except asyncpg.exceptions.UniqueViolationError as err:
        # Handles unique constraint violations
        logger.warning("Unique constraint violation updating user activity: %s", str(err))
    except asyncpg.exceptions.ConnectionDoesNotExistError as err:
        # Handles connection issues
        logger.warning("Connection error updating user activity: %s", str(err))
    except asyncpg.PostgresError as err:
        # Catches any other Postgres-specific errors
        logger.warning("Database error updating user activity: %s", str(err))


def build_update_query(
    body: UpdateUserRequest, user_id: str, org_id: str
) -> tuple[str, list]:
    """Constructs a dynamic SQL update query for an organization member."""
    updates = [
        ("full_name", body.full_name),
        ("first_name", body.first_name),
        ("last_name", body.last_name),
        ("status", body.status),
        ("phone", body.phone),
        ("timezone", body.timezone),
        ("avatar_url", body.avatar_url),
        ("role_id", body.role_id),
    ]
    fields, values, idx = [], [], 1
    for field, val in updates:
        if val:
            fields.append(f"{field} = ${idx}")
            values.append(val)
            idx += 1

    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    fields.append("updated_at = NOW()")
    values += [user_id, org_id]

    query = f"""
        UPDATE public.organization_members
        SET {', '.join(fields)}
        WHERE user_id = ${idx} AND organization_id = ${idx + 1}
        RETURNING user_id, role_id;
    """
    return query, values


async def fetch_user_profile(db_conn, user_id: str, org_id: str) -> Optional[Record]:
    """Fetches a user's profile and role info from the database."""
    return await db_conn.fetchrow(
        """
        SELECT
            om.user_id, om.email, om.full_name, om.first_name, om.last_name,
            om.avatar_url, om.phone, om.timezone, om.status,
            om.joined_at, om.last_active_at,
            om.organization_id, r.id as role_id, r.name as role_name
        FROM public.organization_members om
        INNER JOIN public.roles r ON om.role_id = r.id
            AND om.organization_id = r.organization_id
        WHERE om.user_id = $1 AND om.organization_id = $2
        LIMIT 1;
        """,
        user_id,
        org_id,
    )


async def fetch_user_permissions(
    db_conn, role_id: str, org_id: str
) -> list[PermissionInfo]:
    """Retrieves permissions assigned to a role within an organization."""
    rows = await db_conn.fetch(
        """
        SELECT DISTINCT
            p.id as permission_id, p.code as permission_code,
            p.name as permission_name, p.category
        FROM public.role_permissions rp
        INNER JOIN public.permissions p ON rp.permission_id = p.id
        WHERE rp.role_id = $1 AND rp.organization_id = $2
        ORDER BY p.category NULLS LAST, p.name;
        """,
        role_id,
        org_id,
    )
    return [
        PermissionInfo(
            permission_id=str(row["permission_id"]),
            permission_name=row["permission_name"],
            permission_code=row["permission_code"],
            category=row["category"],
        )
        for row in rows
    ]


def create_user_profile_data(
    user_profile: Union[Record, Dict[str, Any]],
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
            if user_profile["joined_at"]
            else datetime.now().isoformat()
        ),
        last_active_at=(
            user_profile["last_active_at"].isoformat()
            if user_profile["last_active_at"]
            else None
        ),
        organization_id=str(user_profile["organization_id"]),
        user_type=user_type,
        role=role_info,
        permissions=permissions or [],
    )

async def phone_exists_for_other_user(
    db_conn,
    phone: str,
    org_id: str,
    user_id: Optional[str] = None
) -> bool:
    """
    Checks if user phone number exists in DB for a particular organization.
    
    Args:
        db_conn: Database connection
        phone: Phone number to check
        org_id: Organization ID
        user_id: Optional user ID to exclude from the check (for updates)
    
    Returns:
        bool: True if phone exists for another user, False otherwise
    """
    query = """
        SELECT 1
        FROM public.organization_members
        WHERE phone = $1 
        AND organization_id = $2
        AND ($3::uuid IS NULL OR user_id != $3)
        LIMIT 1;
    """
    row = await db_conn.fetchrow(query, phone, org_id, user_id)
    return row is not None
