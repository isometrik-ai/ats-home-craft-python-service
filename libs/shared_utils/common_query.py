"""Constants for query used across the application."""

ROLE_TYPES = ["system", "custom"]

# Permission code constants (to avoid duplication)

TEAMS_MANAGEMENT_CREATE = "teams_management.create"
TEAMS_MANAGEMENT_VIEW = "teams_management.view"
TEAMS_MANAGEMENT_DELETE = "teams_management.delete"
TEAMS_MANAGEMENT_EDIT = "teams_management.edit"

ROLES_MANAGEMENT_CREATE = "roles_management.create"
ROLES_MANAGEMENT_VIEW = "roles_management.view"
ROLES_MANAGEMENT_EDIT = "roles_management.edit"
ROLES_MANAGEMENT_DELETE = "roles_management.delete"

PERMISSIONS_MANAGEMENT_CREATE = "permissions_management.create"
PERMISSIONS_MANAGEMENT_VIEW = "permissions_management.view"
PERMISSIONS_MANAGEMENT_EDIT = "permissions_management.edit"
PERMISSIONS_MANAGEMENT_DELETE = "permissions_management.delete"


DEFAULT_PERMISSIONS = [
    # User Management
    (
        "users_management.view",
        "View Users",
        "View user list and details",
        "users",
    ),
    (
        "users_management.create",
        "Create Users",
        "Invite new users to the system",
        "users",
    ),
    (
        "users_management.edit",
        "Edit Users",
        "Modify user information",
        "users",
    ),
    (
        "users_management.delete",
        "Delete Users",
        "Remove users from the system",
        "users",
    ),
    # Role Management
    (
        "roles_management.view",
        "View Roles",
        "View role list and details",
        "roles",
    ),
    (
        "roles_management.create",
        "Create Roles",
        "Create new roles",
        "roles",
    ),
    (
        ROLES_MANAGEMENT_EDIT,
        "Edit Roles",
        "Modify role information and permissions",
        "roles",
    ),
    (
        "roles_management.delete",
        "Delete Roles",
        "Remove roles from the system",
        "roles",
    ),
    # Team Management
    (
        "teams_management.view",
        "View Teams",
        "View team list and details",
        "teams",
    ),
    (
        "teams_management.create",
        "Create Teams",
        "Create new teams",
        "teams",
    ),
    (
        "teams_management.edit",
        "Edit Teams",
        "Modify team information and members",
        "teams",
    ),
    (
        "teams_management.delete",
        "Delete Teams",
        "Remove teams from the system",
        "teams",
    ),
    # Case Management
    (
        "cases_management.view",
        "View Cases",
        "View case list and details",
        "cases",
    ),
    (
        "cases_management.create",
        "Create Cases",
        "Create new legal cases",
        "cases",
    ),
    (
        "cases_management.edit",
        "Edit Cases",
        "Modify case information",
        "cases",
    ),
    (
        "cases_management.delete",
        "Delete Cases",
        "Remove cases from the system",
        "cases",
    ),
    (
        "cases_management.assign",
        "Assign Cases",
        "Assign cases to team members",
        "cases",
    ),
    # Document Management
    (
        "documents_management.view",
        "View Documents",
        "View documents",
        "documents",
    ),
    (
        "documents_management.upload",
        "Upload Documents",
        "Upload new documents",
        "documents",
    ),
    (
        "documents_management.edit",
        "Edit Documents",
        "Modify document information",
        "documents",
    ),
    (
        "documents_management.delete",
        "Delete Documents",
        "Remove documents",
        "documents",
    ),
    (
        "documents_management.download",
        "Download Documents",
        "Download documents",
        "documents",
    ),
    # Reports & Analytics
    (
        "reports_management.view",
        "View Reports",
        "Access reports and analytics",
        "reports",
    ),
    (
        "reports_management.export",
        "Export Reports",
        "Export reports to external formats",
        "reports",
    ),
    # System Settings
    (
        "settings_management.view",
        "View Settings",
        "View system settings",
        "settings",
    ),
    (
        "settings_management.edit",
        "Edit Settings",
        "Modify system settings",
        "settings",
    ),
    (
        "settings_management.billing",
        "Manage Billing",
        "Access billing and subscription",
        "settings",
    ),
    # Permissions Management
    (
        "permissions_management.view",
        "View Permissions",
        "View permissions list and details",
        "permissions",
    ),
    (
        "permissions_management.create",
        "Create Permissions",
        "create new permissions",
        "permissions",
    ),
    (
        "permissions_management.edit",
        "Edit Permissions",
        "Modify permission information",
        "permissions",
    ),
    (
        "permissions_management.delete",
        "Delete Permissions",
        "Remove permissions",
        "permissions",
    ),
]


# Common SELECT fields for roles
ROLE_SELECT_FIELDS = """
    id,
    name,
    description,
    is_default,
    updated_at,
    created_at
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

# Permission constants (new permission codes with _management suffix)
SETTINGS_SYSTEM_MANAGE = "settings_management.edit"
SETTINGS_ROLES_MANAGE = ROLES_MANAGEMENT_EDIT
SETTINGS_USERS_MANAGE = "users_management.edit"
SETTINGS_USERS_VIEW = "users_management.view"
SETTINGS_PERMISSIONS_MANAGE = ROLES_MANAGEMENT_EDIT
