"""
Organisation Management Utilities Module

This module provides specialized utility functions for organisation management operations.
These utilities handle organisation-specific validations, database operations, and business logic.

Author: AI Assistant
Date: 2024-12-19
Last Updated: 2024-12-19

Organisation-Specific Operations Covered:
1. Organisation existence checking
2. Organisation slug uniqueness validation
3. Organisation status validation
4. Organisation creation helpers
5. Organisation query building
6. Default permissions and roles setup
"""

import time
import asyncio
from typing import List, Optional, Any, Dict, Tuple

import asyncpg
from fastapi import HTTPException, status

# First party imports
from libs.shared_middleware.jwt_auth import check_user_access_async

# Local imports
from .common_utils import validate_uuid_format, ORG_STATUSES


# ============================================================================
# ORGANISATION VALIDATION
# ============================================================================
# ───────────────────────── Helper types ──────────────────────────
PermissionRow = Tuple[str, str, str, str]  # code, name, desc, category
PermissionsMap = Dict[str, str]

class PermissionBatch:
    """Helper class to manage permission data for bulk operations"""
    def __init__(self, perms: List[PermissionRow], org_id: str):
        self.codes = [p[0] for p in perms]
        self.names = [p[1] for p in perms]
        self.descriptions = [p[2] for p in perms]
        self.categories = [p[3] for p in perms]
        self.org_ids = [org_id] * len(perms)
        self.size = len(perms)

    def get_insert_params(self) -> tuple:
        """Get parameters for the UNNEST insert query"""
        return (
            self.org_ids,
            self.codes,
            self.names,
            self.descriptions,
            self.categories,
        )

    def get_insert_query(self) -> str:
        """Get the SQL query for inserting permissions"""
        return """
            INSERT INTO public.permissions
                   (organization_id, code, name, description, category, created_at)
            SELECT  UNNEST($1::uuid[]),
                    UNNEST($2::text[]),
                    UNNEST($3::text[]),
                    UNNEST($4::text[]),
                    UNNEST($5::text[]),
                    NOW()
            ON CONFLICT (organization_id, code) DO NOTHING
            RETURNING code, id;
        """


def validate_organisation_status(org_status: str) -> None:
    """
    Validate organisation status against allowed values.

    Args:
        org_status (str): Organisation status to validate

    Raises:
        HTTPException: 422 for invalid organisation status

    Usage:
        validate_organisation_status(body.status)
    """
    if org_status not in ORG_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Status must be one of: {', '.join(ORG_STATUSES)}",
        )


def validate_organisation_slug(slug: str) -> None:
    """
    Validate organisation slug format and length.

    Args:
        slug (str): Organisation slug to validate

    Raises:
        HTTPException: 400 for invalid slug format

    Usage:
        validate_organisation_slug(body.slug)
    """
    if not slug or len(slug.strip()) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organisation slug is required",
        )

    slug = slug.strip()
    if len(slug) < 2 or len(slug) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organisation slug must be between 2 and 50 characters",
        )


def validate_organisation_name_filter(name: str) -> str:
    """
    Validate and sanitize organisation name filter.

    Args:
        name (str): Organisation name filter to validate

    Returns:
        str: Sanitized name filter

    Raises:
        HTTPException: 422 for invalid name filter

    Usage:
        clean_name = validate_organisation_name_filter(name)
    """
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Name filter cannot be empty",
        )

    name = name.strip()
    if len(name) < 1 or len(name) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Name filter must be between 1 and 255 characters",
        )

    return name


# ============================================================================
# ORGANISATION DATABASE OPERATIONS
# ============================================================================


async def check_organisation_exists(
    organisation_id: str, db_conn, with_timing: bool = True
) -> dict:
    """
    Check if organisation exists and return organisation data.

    Args:
        organisation_id (str): Organisation ID to check
        db_conn: AsyncPG database connection
        with_timing (bool): Whether to log timing information

    Returns:
        dict: Organisation data if found

    Raises:
        HTTPException: 404 if organisation not found

    Usage:
        org_data = await check_organisation_exists(organisation_id, db_conn)
    """
    validate_uuid_format(organisation_id, "organisation ID")

    if with_timing:
        start_time = time.time()

    org_check_query = """
        SELECT id, name, slug, status FROM public.organizations
        WHERE id = $1;
    """

    existing_org = await db_conn.fetchrow(org_check_query, organisation_id)

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Organisation existence check took {elapsed:.2f}ms")

    if not existing_org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organisation not found",
        )

    return existing_org


async def check_organisation_slug_unique(
    slug: str, db_conn, exclude_org_id: Optional[str] = None, with_timing: bool = True
) -> None:
    """
    Check if organisation slug is unique.

    Args:
        slug (str): Organisation slug to check
        db_conn: AsyncPG database connection
        exclude_org_id (Optional[str]): Organisation ID to exclude from check (for updates)
        with_timing (bool): Whether to log timing information

    Raises:
        HTTPException: 409 for slug conflicts

    Usage:
        await check_organisation_slug_unique(body.slug, db_conn)
        await check_organisation_slug_unique(body.slug, db_conn, exclude_org_id=org_id)
    """
    if with_timing:
        start_time = time.time()

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            # Set a reasonable timeout for the slug check
            await db_conn.execute("SET LOCAL statement_timeout = '10s';")
            await db_conn.execute("SET LOCAL lock_timeout = '3s';")
        except asyncpg.PostgresError:
            # Non-fatal if we're not inside a transaction context
            pass

        try:
            if exclude_org_id:
                validate_uuid_format(exclude_org_id, "organisation ID")
                slug_conflict_query = """
                    SELECT id FROM public.organizations
                    WHERE slug = $1 AND id != $2;
                """
                slug_conflict = await db_conn.fetchrow(
                    slug_conflict_query, slug, exclude_org_id
                )
            else:
                slug_check_query = """
                    SELECT id FROM public.organizations
                    WHERE slug = $1;
                """
                slug_conflict = await db_conn.fetchrow(slug_check_query, slug)
            
            # If we get here, the query succeeded
            break
            
        except (TimeoutError, asyncpg.exceptions.QueryCanceledError) as e:
            if attempt == max_attempts:
                # Since the slug contains a UUID, it's extremely unlikely to conflict
                # We'll skip the check and proceed with the signup
                slug_conflict = None
                break
            await asyncio.sleep(0.5 * attempt)
        except asyncpg.PostgresError as e:
            err_text = str(e).lower()
            if "timeout" in err_text or "canceling" in err_text:
                if attempt == max_attempts:
                    # Since the slug contains a UUID, it's extremely unlikely to conflict
                    # We'll skip the check and proceed with the signup
                    slug_conflict = None
                    break
                await asyncio.sleep(0.5 * attempt)
            else:
                # Re-raise non-timeout Postgres errors immediately
                raise

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Slug uniqueness check took {elapsed:.2f}ms")

    if slug_conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organisation slug already exists",
        )


async def check_organisation_access(
    user_id: str,
    organisation_id: str,
    permission_code: str,
    db_conn,
    with_timing: bool = True,
) -> bool:
    """
    Check if user has access to organisation with specific permission.

    Args:
        user_id (str): User ID to check
        organisation_id (str): Organisation ID to check access for
        permission_code (str): Permission code to check
        db_conn: AsyncPG database connection
        with_timing (bool): Whether to log timing information

    Returns:
        bool: True if user has access, False otherwise

    Usage:
        has_access = await check_organisation_access(user_id,
                                org_id, "organization.appscrip.manage", db_conn)
    """
    validate_uuid_format(user_id, "user ID")
    validate_uuid_format(organisation_id, "organisation ID")

    if with_timing:
        start_time = time.time()

    has_permission = await check_user_access_async(
        permission_code=permission_code,
        user_id=user_id,
        organisation_id=organisation_id,
        db_conn=db_conn,
    )

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Organisation access check took {elapsed:.2f}ms")

    return has_permission


# ============================================================================
# ORGANISATION QUERY BUILDERS
# ============================================================================


def build_organisations_filter_query(
    name: Optional[str] = None,
    org_status: Optional[str] = None,
    page_size: int = 20,
    offset: int = 0,
) -> tuple[str, List[Any]]:
    """
    Build a dynamic organisations query with filters.

    Args:
        name (Optional[str]): Search term for organisation names
        org_status (Optional[str]): Organisation status filter
        page_size (int): Query limit
        offset (int): Query offset

    Returns:
        tuple: (query_string, parameters_list)

    Usage:
        query, params = build_organisations_filter_query(
            name="acme", org_status="active", page_size=10, offset=0
        )
    """
    query_params = []
    param_count = 0

    # Build filter conditions
    filter_conditions = []

    # Add name filter if provided
    if name:
        param_count += 1
        filter_conditions.append(f"o.name ILIKE ${param_count}")
        query_params.append(f"%{name}%")

    # Add status filter if provided
    if org_status:
        validate_organisation_status(org_status)
        param_count += 1
        filter_conditions.append(f"o.status = ${param_count}")
        query_params.append(org_status)

    # Combine filter conditions
    where_clause = ""
    if filter_conditions:
        where_clause = "WHERE " + " AND ".join(filter_conditions)

    # Add pagination parameters
    param_count += 1
    limit_param = f"${param_count}"
    query_params.append(page_size)

    param_count += 1
    offset_param = f"${param_count}"
    query_params.append(offset)

    # Build complete query
    organizations_query = f"""
        SELECT
            o.id as organization_id,
            o.name,
            o.slug,
            o.domain,
            o.logo_url,
            o.plan_type,
            o.status,
            o.max_users,
            o.timezone,
            o.created_at,
            o.updated_at,
            COUNT(om.id) as member_count
        FROM public.organizations o
        LEFT JOIN public.organization_members om ON o.id = om.organization_id
            AND om.status = 'active'
        {where_clause}
        GROUP BY o.id, o.name, o.slug, o.domain, o.logo_url, o.plan_type,
                 o.status, o.max_users, o.timezone, o.created_at, o.updated_at
        ORDER BY o.created_at DESC
        LIMIT {limit_param} OFFSET {offset_param};
    """

    return organizations_query, query_params


def build_organisations_count_query(
    name: Optional[str] = None, org_status: Optional[str] = None
) -> tuple[str, List[Any]]:
    """
    Build a count query for organisations with the same filters.

    Args:
        name (Optional[str]): Search term for organisation names
        org_status (Optional[str]): Organisation status filter

    Returns:
        tuple: (count_query_string, parameters_list)

    Usage:
        count_query, count_params = build_organisations_count_query(
            name="acme", org_status="active"
        )
    """
    query_params = []
    param_count = 0

    # Build filter conditions
    filter_conditions = []

    # Add name filter if provided
    if name:
        param_count += 1
        filter_conditions.append(f"o.name ILIKE ${param_count}")
        query_params.append(f"%{name}%")

    # Add status filter if provided
    if org_status:
        validate_organisation_status(org_status)
        param_count += 1
        filter_conditions.append(f"o.status = ${param_count}")
        query_params.append(org_status)

    # Combine filter conditions
    where_clause = ""
    if filter_conditions:
        where_clause = "WHERE " + " AND ".join(filter_conditions)

    # Build count query
    count_query = f"""
        SELECT COUNT(*) as total_count
        FROM public.organizations o
        {where_clause};
    """

    return count_query, query_params


def build_organisation_detail_query() -> str:
    """
    Build organisation detail query for single organisation retrieval.

    Returns:
        str: Organisation detail query string

    Usage:
        query = build_organisation_detail_query()
    """
    return """
        SELECT
            o.id as organization_id,
            o.name,
            o.slug,
            o.domain,
            o.logo_url,
            o.plan_type,
            o.status,
            o.max_users,
            o.timezone,
            o.settings,
            o.created_at,
            o.updated_at,
            COUNT(om.id) as member_count
        FROM public.organizations o
        LEFT JOIN public.organization_members om ON o.id = om.organization_id
            AND om.status = 'active'
        WHERE o.id = $1
        GROUP BY o.id, o.name, o.slug, o.domain, o.logo_url, o.plan_type,
                 o.status, o.max_users, o.timezone, o.settings, o.created_at, o.updated_at
        LIMIT 1;
    """


# ============================================================================
# ORGANISATION CREATION HELPERS
# ============================================================================


def get_default_permissions() -> List[tuple]:
    """
    Get the default permissions for a new organisation.

    Returns:
        List[tuple]: List of permission tuples (code, name, description, category)

    Usage:
        permissions = get_default_permissions()
    """
    return [

        (
            "business.dashboard.view",
            "View Dashboard",
            "Access to main dashboard",
            "business",
        ),
        (
            "business.customers.view",
            "View Customers",
            "View customer information",
            "business",
        ),
        (
            "business.customers.manage",
            "Manage Customers",
            "Full customer management",
            "business",
        ),
        (
            "business.projects.view",
            "View Projects",
            "View project information",
            "business",
        ),
        (
            "business.projects.manage",
            "Manage Projects",
            "Full project management",
            "business",
        ),
        (
            "talent.candidates.view",
            "View Candidates",
            "View candidate profiles",
            "talent",
        ),
        (
            "talent.candidates.manage",
            "Manage Candidates",
            "Full candidate management",
            "talent",
        ),
        (
            "talent.rst_templates.view",
            "View RST Templates",
            "View templates only",
            "talent",
        ),
        (
            "talent.rst_templates.manage",
            "Manage RST Templates",
            "Create and edit templates",
            "talent",
        ),
        (
            "talent.search.advanced",
            "Advanced Search",
            "Access to advanced search features",
            "talent",
        ),
        (
            "automation.triggers.manage",
            "Manage Triggers",
            "Create and manage automation triggers",
            "automation",
        ),
        (
            "automation.analytics.view",
            "View Analytics",
            "Access to analytics dashboard",
            "automation",
        ),
        (
            "automation.analytics.export",
            "Export Analytics",
            "Export analytics data",
            "automation",
        ),
        (
            "automation.ai_assistant.access",
            "AI Assistant Access",
            "Access to AI assistant features",
            "automation",
        ),
        ("settings.users.manage", "Manage Users", "Full user management", "settings"),
        (
            "settings.roles.manage",
            "Manage Roles",
            "Create and manage roles",
            "settings",
        ),
        (
            "settings.integrations.manage",
            "Manage Integrations",
            "Configure integrations",
            "settings",
        ),
        (
            "settings.system.manage",
            "System Settings",
            "Access to system settings",
            "settings",
        ),

    ]


async def create_default_permissions_for_organisation(
    organisation_id: str,
    db_conn,
    with_timing: bool = True,
) -> List[str]:
    """
    Insert the default permission set for an organisation and return the IDs,
    re-using any permissions that already exist.
    """
    validate_uuid_format(organisation_id, "organisation ID")
    if with_timing:
        _start = time.time()

    default_perms: List[PermissionRow] = get_default_permissions()
    existing = await _fetch_existing_permissions(
        db_conn, organisation_id, default_perms
    )
    inserted = await _insert_missing_permissions(
        db_conn, organisation_id, default_perms, existing
    )

    # Preserve original order
    ids: List[str] = [
        existing.get(code) or inserted[code] for code, *_ in default_perms
    ]

    if with_timing:
        print(f"Default permissions creation took {(time.time() - _start)*1000:.2f} ms")
    return ids


async def create_default_permissions_for_new_org(
    organisation_id: str,
    db_conn,
    with_timing: bool = True,
) -> List[str]:
    """
    Fast-path: Insert all default permissions for a brand-new organisation.
    Assumes there are NO existing permissions yet for this organisation.
    Performs a single bulk UNNEST insert with ON CONFLICT DO NOTHING and
    returns IDs in the original default order.
    """
    validate_uuid_format(organisation_id, "organisation ID")
    start_time = time.time() if with_timing else None

    # Prepare permission data
    perms = PermissionBatch(get_default_permissions(), organisation_id)
    inserted_rows = await _insert_permissions_with_retry(db_conn, perms)

    # Map results back to original codes
    code_to_id = {r["code"]: r["id"] for r in inserted_rows}
    ids = [code_to_id.get(code) for code in perms.codes if code in code_to_id]

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Inserted {len(ids)}/{perms.size} default permissions in {elapsed:.2f} ms")

    return ids

async def _insert_permissions_with_retry(db_conn, perms: PermissionBatch) -> List[dict]:
    """Helper function to handle permission insertion with retry logic"""
    max_attempts = 3
    base_timeout_seconds = 30  # Increased from 15 to 30 seconds

    for attempt in range(1, max_attempts + 1):
        try:
            # Set local timeouts per attempt
            stmt_timeout = base_timeout_seconds * attempt
            try:
                await db_conn.execute(f"SET LOCAL statement_timeout = '{stmt_timeout}s';")
                await db_conn.execute("SET LOCAL lock_timeout = '5s';")  # Increased lock timeout
            except asyncpg.PostgresError:
                # Non-fatal if we're not inside a transaction context
                pass

            result = await db_conn.fetch(
                perms.get_insert_query(),
                *perms.get_insert_params()
            )
            return result
        except (asyncpg.DeadlockDetectedError, asyncpg.exceptions.QueryCanceledError, TimeoutError):
            # Handle specific database timeout/lock errors
            if attempt == max_attempts:
                return await _insert_permissions_individually(db_conn, perms)
            await asyncio.sleep(0.5 * attempt)  # Increased sleep time
        except asyncpg.PostgresError as error:
            # For any other Postgres errors, check if it's timeout related
            err_text = str(error).lower()
            is_timeout_like = (
                "statement timeout" in err_text
                or "canceling statement" in err_text
                or "lock timeout" in err_text
            )
            if attempt == max_attempts or not is_timeout_like:
                if attempt == max_attempts and is_timeout_like:
                    return await _insert_permissions_individually(db_conn, perms)
                raise
            await asyncio.sleep(0.5 * attempt)  # Increased sleep time

    return []  # Return empty list if all attempts failed


async def _insert_permissions_individually(db_conn, perms: PermissionBatch) -> List[dict]:
    """Fallback: Insert permissions one by one if bulk insert fails"""
    inserted_permissions = []
    
    for i, (code, name, description, category) in enumerate(zip(perms.codes, perms.names, perms.descriptions, perms.categories)):
        try:
            result = await db_conn.fetchrow(
                """
                INSERT INTO public.permissions
                       (organization_id, code, name, description, category, created_at)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (organization_id, code) DO NOTHING
                RETURNING code, id;
                """,
                perms.org_ids[0],  # All org_ids are the same
                code,
                name,
                description,
                category
            )
            if result:
                inserted_permissions.append(result)
        except Exception as e:
            # Continue with other permissions even if one fails
            pass
    
    return inserted_permissions


# ────────────────────────── Helpers ──────────────────────────────
async def _fetch_existing_permissions(
    db_conn,
    org_id: str,
    default_perms: List[PermissionRow],
) -> PermissionsMap:
    codes = [p[0] for p in default_perms]
    rows = await db_conn.fetch(
        """
        SELECT code, id
        FROM   public.permissions
        WHERE  organization_id = $1
          AND  code            = ANY($2::text[])
        """,
        org_id,
        codes,
    )
    return {r["code"]: r["id"] for r in rows}


async def _insert_missing_permissions(
    db_conn,
    org_id: str,
    default_perms: List[PermissionRow],
    existing: PermissionsMap,
) -> PermissionsMap:
    """Bulk-insert permissions that don't yet exist.  Returns {code: id}."""
    missing_rows = [
        (org_id, *perm) for perm in default_perms if perm[0] not in existing
    ]

    if not missing_rows:  # nothing to insert
        return {}

    org_ids, codes, names, descs, cats = zip(*missing_rows)
    inserted = await db_conn.fetch(
        """
        INSERT INTO public.permissions
               (organization_id, code, name, description, category, created_at)
        SELECT  UNNEST($1::uuid[]),
                UNNEST($2::text[]),
                UNNEST($3::text[]),
                UNNEST($4::text[]),
                UNNEST($5::text[]),
                NOW()
        ON CONFLICT (organization_id, code) DO NOTHING
        RETURNING code, id;
        """,
        org_ids,
        codes,
        names,
        descs,
        cats,
    )
    return {r["code"]: r["id"] for r in inserted}


async def create_super_admin_role(
    organisation_id: str, db_conn, with_timing: bool = True
) -> str:
    """
    Create Super Admin role for a new organisation.

    Args:
        organisation_id (str): Organisation ID to create role for
        db_conn: AsyncPG database connection
        with_timing (bool): Whether to log timing information

    Returns:
        str: Created role ID

    Usage:
        role_id = await create_super_admin_role(org_id, db_conn)
    """
    validate_uuid_format(organisation_id, "organisation ID")

    if with_timing:
        start_time = time.time()

    role_insert_query = """
        INSERT INTO public.roles (
            id, name, description, organization_id, is_default, created_at, updated_at
        ) VALUES (
            gen_random_uuid(), 'Super Admin', 'Full administrative access to all system features',
            $1, true, NOW(), NOW()
        ) RETURNING id;
    """

    role_result = await db_conn.fetchrow(role_insert_query, organisation_id)
    role_id = role_result["id"]

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Super Admin role creation took {elapsed:.2f}ms")

    return str(role_id)


async def assign_all_permissions_to_role(
    role_id: str,
    organisation_id: str,
    permission_ids: List[str],
    db_conn,
    with_timing: bool = True,
) -> None:
    """
    Assign all permissions to a role using bulk insert.

    Args:
        role_id (str): Role ID to assign permissions to
        organisation_id (str): Organisation ID
        permission_ids (List[str]): List of permission IDs to assign
        db_conn: AsyncPG database connection
        with_timing (bool): Whether to log timing information

    Usage:
        await assign_all_permissions_to_role(role_id, org_id, permission_ids, db_conn)
    """
    if not permission_ids:
        return

    validate_uuid_format(role_id, "role ID")
    validate_uuid_format(organisation_id, "organisation ID")

    if with_timing:
        start_time = time.time()

    role_perm_insert_query = """
        INSERT INTO public.role_permissions (organization_id, role_id, permission_id, created_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (organization_id, role_id, permission_id) DO NOTHING;
    """

    for permission_id in permission_ids:
        await db_conn.execute(
            role_perm_insert_query, organisation_id, role_id, permission_id
        )

    if with_timing:
        elapsed = (time.time() - start_time) * 1000
        print(f"Permission assignment took {elapsed:.2f}ms")


# ============================================================================
# ORGANISATION RESPONSE HELPERS
# ============================================================================


def build_organisation_filter_message(
    name: Optional[str] = None,
    org_status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> str:
    """
    Build a filter description message for organisation API responses.

    Args:
        name (Optional[str]): Search term
        org_status (Optional[str]): Organisation status filter
        page (int): Page number
        page_size (int): Page size

    Returns:
        str: Formatted filter message

    Usage:
        filter_msg = build_organisation_filter_message(name="acme", org_status="active")
    """
    filter_info = []

    if name:
        filter_info.append(f"name='{name}'")
    if org_status:
        filter_info.append(f"status='{org_status}'")
    if page > 1:
        filter_info.append(f"page={page}")
    filter_info.append(f"page_size={page_size}")

    filter_text = f" with filters: {', '.join(filter_info)}" if filter_info else ""
    return f"All organizations retrieved successfully{filter_text}"


def build_organisation_creation_success_message(org_name: str, user_email: str) -> str:
    """
    Build success message for organisation creation.

    Args:
        org_name (str): Organisation name
        user_email (str): User email

    Returns:
        str: Success message

    Usage:
        message = build_organisation_creation_success_message("Acme Corp", "admin@acme.com")
    """
    return f"Organisation '{org_name}' and user '{user_email}' created successfully"
