"""Constants for query used across the application."""

# libs/shared_utils/common_query.py


# Common SELECT fields for roles
ROLE_SELECT_FIELDS = """
    r.id,
    r.name,
    r.description,
    r.is_default,
    r.updated_at,
    r.created_at
"""

# Common SELECT fields for permissions
PERMISSION_SELECT_FIELDS = """
    p.id,
    p.name,
    p.code,
    p.category,
    p.description,
    p.created_at
"""

# Example: Full query for fetching a role by ID
GET_ROLE_BY_ID_QUERY = f"""
SELECT
    {ROLE_SELECT_FIELDS}
FROM public.roles r
WHERE r.id = $1
"""

# Example: Full query for fetching permissions for a role
GET_PERMISSIONS_FOR_ROLE_QUERY = f"""
SELECT
    {PERMISSION_SELECT_FIELDS}
FROM public.role_permissions rp
JOIN public.permissions p ON rp.permission_id = p.id
WHERE rp.role_id = $1
"""


MEMBER_INSERT_QUERY = """
        INSERT INTO public.organization_members (
            user_id, organization_id, role_id, email, full_name, phone, timezone,
            status, joined_at, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, 'active', NOW(), NOW(), NOW()
        ) RETURNING id;
    """
