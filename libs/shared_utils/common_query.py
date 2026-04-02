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

CLIENTS_MANAGEMENT_CREATE = "clients_management.create"
CLIENTS_MANAGEMENT_VIEW = "clients_management.view"
CLIENTS_MANAGEMENT_EDIT = "clients_management.edit"
CLIENTS_MANAGEMENT_DELETE = "clients_management.delete"

PROJECTS_MANAGEMENT_CREATE = "projects_management.create"
PROJECTS_MANAGEMENT_VIEW = "projects_management.view"
PROJECTS_MANAGEMENT_EDIT = "projects_management.edit"
PROJECTS_MANAGEMENT_DELETE = "projects_management.delete"

CUSTOM_FIELDS_MANAGEMENT_CREATE = "custom_fields_management.create"
CUSTOM_FIELDS_MANAGEMENT_VIEW = "custom_fields_management.view"
CUSTOM_FIELDS_MANAGEMENT_EDIT = "custom_fields_management.edit"
CUSTOM_FIELDS_MANAGEMENT_DELETE = "custom_fields_management.delete"

LEADS_MANAGEMENT_CREATE = "leads_management.create"
LEADS_MANAGEMENT_VIEW = "leads_management.view"
LEADS_MANAGEMENT_EDIT = "leads_management.edit"
LEADS_MANAGEMENT_DELETE = "leads_management.delete"

USERS_MANAGEMENT_DELETE = "users_management.delete"

# Audit Logs
# "view_system" is intended to mean org-wide (system-level) audit logs visibility.
AUDIT_LOGS_MANAGEMENT_VIEW_SYSTEM = "audit_logs_management.view_system"

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
    # Audit Logs
    (
        AUDIT_LOGS_MANAGEMENT_VIEW_SYSTEM,
        "View System Audit Logs",
        "View organization-wide audit logs",
        "audit_logs",
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
    # clients management
    (
        "clients_management.view",
        "View Clients",
        "View client list and details",
        "clients",
    ),
    (
        "clients_management.create",
        "Create Clients",
        "Create new clients",
        "clients",
    ),
    (
        "clients_management.edit",
        "Edit Clients",
        "Modify client information",
        "clients",
    ),
    (
        "clients_management.delete",
        "Delete Clients",
        "Remove clients from the system",
        "clients",
    ),
    # projects management
    (
        "projects_management.view",
        "View Projects",
        "View project list and details",
        "projects",
    ),
    (
        "projects_management.create",
        "Create Projects",
        "Create new projects",
        "projects",
    ),
    (
        "projects_management.edit",
        "Edit Projects",
        "Modify project information",
        "projects",
    ),
    (
        "projects_management.delete",
        "Delete Projects",
        "Remove projects from the system",
        "projects",
    ),
    # custom fields management
    (
        "custom_fields_management.view",
        "View Custom Fields",
        "View custom field list and details",
        "custom_fields",
    ),
    (
        "custom_fields_management.create",
        "Create Custom Fields",
        "Create new custom fields",
        "custom_fields",
    ),
    (
        "custom_fields_management.edit",
        "Edit Custom Fields",
        "Modify custom field information",
        "custom_fields",
    ),
    (
        "custom_fields_management.delete",
        "Delete Custom Fields",
        "Remove custom fields from the system",
        "custom_fields",
    ),
    # lead stages management
    (
        "leads_management.view",
        "View Leads",
        "View leads list and details",
        "leads",
    ),
    (
        "leads_management.create",
        "Create Leads",
        "Create new leads",
        "leads",
    ),
    (
        "leads_management.edit",
        "Edit Leads",
        "Modify lead information",
        "leads",
    ),
    (
        "leads_management.delete",
        "Delete Leads",
        "Remove leads from the system",
        "leads",
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
