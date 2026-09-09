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

CONTACTS_MANAGEMENT_CREATE = "contacts_management.create"
CONTACTS_MANAGEMENT_VIEW = "contacts_management.view"
CONTACTS_MANAGEMENT_EDIT = "contacts_management.edit"
CONTACTS_MANAGEMENT_DELETE = "contacts_management.delete"

COMPANIES_MANAGEMENT_CREATE = "companies_management.create"
COMPANIES_MANAGEMENT_VIEW = "companies_management.view"
COMPANIES_MANAGEMENT_EDIT = "companies_management.edit"
COMPANIES_MANAGEMENT_DELETE = "companies_management.delete"

PROJECTS_MANAGEMENT_CREATE = "projects_management.create"
PROJECTS_MANAGEMENT_VIEW = "projects_management.view"
PROJECTS_MANAGEMENT_VIEW_ASSIGNED = "projects_management.view_assigned"
PROJECTS_MANAGEMENT_EDIT = "projects_management.edit"
PROJECTS_MANAGEMENT_DELETE = "projects_management.delete"
PROJECT_MEMBERS_MANAGE = "project_members.manage"
PROJECT_MEMBERS_MANAGE_ASSIGNED = "project_members.manage_assigned"

PROJECT_SETUP_EDIT = "project_setup.edit"
PROJECT_SETUP_DELETE = "project_setup.delete"

NOTICES_MANAGEMENT_VIEW = "notices_management.view"
NOTICES_MANAGEMENT_EDIT = "notices_management.edit"

COMMUNITY_EVENTS_MANAGEMENT_VIEW = "community_events_management.view"
COMMUNITY_EVENTS_MANAGEMENT_EDIT = "community_events_management.edit"

DAILY_HELP_MANAGEMENT_VIEW = "daily_help_management.view"
DAILY_HELP_MANAGEMENT_CREATE = "daily_help_management.create"
DAILY_HELP_MANAGEMENT_UPDATE = "daily_help_management.update"
DAILY_HELP_MANAGEMENT_REVIEW = "daily_help_management.review"

TENANT_REQUESTS_MANAGEMENT_VIEW = "tenant_requests_management.view"
TENANT_REQUESTS_MANAGEMENT_EDIT = "tenant_requests_management.edit"

MOVE_EVENTS_MANAGEMENT_VIEW = "move_events_management.view"
MOVE_EVENTS_MANAGEMENT_EDIT = "move_events_management.edit"

PARKING_MANAGEMENT_VIEW = "parking_management.view"
PARKING_MANAGEMENT_EDIT = "parking_management.edit"

RESIDENT_MANAGEMENT_VIEW = "resident_management.view"
RESIDENT_MANAGEMENT_EDIT = "resident_management.edit"

WORK_ORDER_MANAGEMENT_VIEW = "work_order_management.view"
WORK_ORDER_MANAGEMENT_EDIT = "work_order_management.edit"
WORK_ORDER_MANAGEMENT_APPROVE = "work_order_management.approve"
WORK_ORDER_MANAGEMENT_PAY = "work_order_management.pay"

CUSTOM_FIELDS_MANAGEMENT_CREATE = "custom_fields_management.create"
CUSTOM_FIELDS_MANAGEMENT_VIEW = "custom_fields_management.view"
CUSTOM_FIELDS_MANAGEMENT_EDIT = "custom_fields_management.edit"
CUSTOM_FIELDS_MANAGEMENT_DELETE = "custom_fields_management.delete"

EMAIL_TEMPLATES_MANAGEMENT_CREATE = "email_templates_management.create"
EMAIL_TEMPLATES_MANAGEMENT_VIEW = "email_templates_management.view"
EMAIL_TEMPLATES_MANAGEMENT_EDIT = "email_templates_management.edit"
EMAIL_TEMPLATES_MANAGEMENT_DELETE = "email_templates_management.delete"

LEADS_MANAGEMENT_CREATE = "leads_management.create"
LEADS_MANAGEMENT_VIEW = "leads_management.view"
LEADS_MANAGEMENT_EDIT = "leads_management.edit"
LEADS_MANAGEMENT_DELETE = "leads_management.delete"
LEADS_MANAGEMENT_VIEW_SYSTEM = "leads_management.view_system"

BUSINESS_DASHBOARD_VIEW = "business.dashboard.view"

USERS_MANAGEMENT_DELETE = "users_management.delete"

# Audit Logs
# "view_system" is intended to mean org-wide (system-level) audit logs visibility.
AUDIT_LOGS_MANAGEMENT_VIEW_SYSTEM = "audit_logs_management.view_system"

VISITOR_MANAGEMENT_VIEW = "visitor_management.view"
VISITOR_MANAGEMENT_VERIFY = "visitor_management.verify"

FINANCE_MANAGEMENT_VIEW = "finance_management.view"
FINANCE_MANAGEMENT_EDIT = "finance_management.edit"
FINANCE_MANAGEMENT_ADMIN = "finance_management.admin"

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
    # contacts management
    (
        "contacts_management.view",
        "View Contacts",
        "View contact list and details",
        "contacts",
    ),
    (
        "contacts_management.create",
        "Create Contacts",
        "Create new contacts",
        "contacts",
    ),
    (
        "contacts_management.edit",
        "Edit Contacts",
        "Modify contact information",
        "contacts",
    ),
    (
        "contacts_management.delete",
        "Delete Contacts",
        "Remove contacts from the system",
        "contacts",
    ),
    # companies management
    (
        "companies_management.view",
        "View Companies",
        "View company list and details",
        "companies",
    ),
    (
        "companies_management.create",
        "Create Companies",
        "Create new companies",
        "companies",
    ),
    (
        "companies_management.edit",
        "Edit Companies",
        "Modify company information",
        "companies",
    ),
    (
        "companies_management.delete",
        "Delete Companies",
        "Remove companies from the system",
        "companies",
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
    (
        PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
        "View Assigned Projects",
        "View and access only projects assigned via project_members",
        "projects",
    ),
    (
        PROJECT_MEMBERS_MANAGE,
        "Manage Project Members",
        "Assign and remove staff on projects",
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
        LEADS_MANAGEMENT_VIEW_SYSTEM,
        "View System Leads",
        "View organization-wide leads",
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
    (
        BUSINESS_DASHBOARD_VIEW,
        "View Dashboard",
        "View organization CRM dashboard metrics",
        "dashboard",
    ),
    # Email Templates
    (
        EMAIL_TEMPLATES_MANAGEMENT_VIEW,
        "View Email Templates",
        "View email template list and details",
        "email_templates",
    ),
    (
        EMAIL_TEMPLATES_MANAGEMENT_CREATE,
        "Create Email Templates",
        "Create email templates",
        "email_templates",
    ),
    (
        EMAIL_TEMPLATES_MANAGEMENT_EDIT,
        "Edit Email Templates",
        "Modify email templates",
        "email_templates",
    ),
    (
        EMAIL_TEMPLATES_MANAGEMENT_DELETE,
        "Delete Email Templates",
        "Remove email templates",
        "email_templates",
    ),
]

# Project-role catalog (stored in project_permissions, not organization permissions).
DEFAULT_PROJECT_PERMISSIONS = [
    (
        PROJECTS_MANAGEMENT_VIEW_ASSIGNED,
        "View Assigned Projects",
        "View and access only projects assigned via project_members",
        "projects",
    ),
    (
        PROJECT_MEMBERS_MANAGE_ASSIGNED,
        "Manage Assigned Project Members",
        "Assign and remove staff on projects where caller is community admin",
        "projects",
    ),
    (
        PROJECT_SETUP_EDIT,
        "Edit Project Setup",
        "Modify project setup, towers, units, and configuration within assigned projects",
        "projects",
    ),
    (
        PROJECT_SETUP_DELETE,
        "Delete Project",
        "Delete projects the user is assigned to with sufficient project role",
        "projects",
    ),
    (
        NOTICES_MANAGEMENT_VIEW,
        "View Notices",
        "View notice board content within assigned projects",
        "notices",
    ),
    (
        NOTICES_MANAGEMENT_EDIT,
        "Edit Notices",
        "Create and publish notices within assigned projects",
        "notices",
    ),
    (
        COMMUNITY_EVENTS_MANAGEMENT_VIEW,
        "View Community Events",
        "View community events within assigned projects",
        "community_events",
    ),
    (
        COMMUNITY_EVENTS_MANAGEMENT_EDIT,
        "Edit Community Events",
        "Create and manage community events within assigned projects",
        "community_events",
    ),
    (
        DAILY_HELP_MANAGEMENT_VIEW,
        "View Daily Help",
        "View daily help registry within assigned projects",
        "daily_help",
    ),
    (
        DAILY_HELP_MANAGEMENT_CREATE,
        "Create Daily Help",
        "Create daily help profiles within assigned projects",
        "daily_help",
    ),
    (
        DAILY_HELP_MANAGEMENT_UPDATE,
        "Update Daily Help",
        "Edit daily help profiles and categories within assigned projects",
        "daily_help",
    ),
    (
        DAILY_HELP_MANAGEMENT_REVIEW,
        "Review Daily Help",
        "Approve or reject daily help submissions within assigned projects",
        "daily_help",
    ),
    (
        TENANT_REQUESTS_MANAGEMENT_VIEW,
        "View Tenant Requests",
        "View tenant requests within assigned projects",
        "tenant_requests",
    ),
    (
        TENANT_REQUESTS_MANAGEMENT_EDIT,
        "Edit Tenant Requests",
        "Approve or reject tenant requests within assigned projects",
        "tenant_requests",
    ),
    (
        MOVE_EVENTS_MANAGEMENT_VIEW,
        "View Move Events",
        "View move-in and move-out events within assigned projects",
        "move_events",
    ),
    (
        MOVE_EVENTS_MANAGEMENT_EDIT,
        "Edit Move Events",
        "Create and update move events within assigned projects",
        "move_events",
    ),
    (
        PARKING_MANAGEMENT_VIEW,
        "View Parking Allotments",
        "View parking slots and allotments within assigned projects",
        "parking",
    ),
    (
        PARKING_MANAGEMENT_EDIT,
        "Edit Parking Allotments",
        "Assign and release parking slots within assigned projects",
        "parking",
    ),
    (
        RESIDENT_MANAGEMENT_VIEW,
        "View Project Residents",
        "View unit occupants, household links, and resident profiles within assigned projects",
        "residents",
    ),
    (
        RESIDENT_MANAGEMENT_EDIT,
        "Edit Project Residents",
        (
            "Assign units, manage household members, and"
            "update resident occupancy within assigned projects"
        ),
        "residents",
    ),
    (
        BUSINESS_DASHBOARD_VIEW,
        "View Project Dashboard",
        "View project business dashboard metrics",
        "dashboard",
    ),
    (
        CONTACTS_MANAGEMENT_VIEW,
        "View Project Contacts",
        "View contacts within assigned projects",
        "contacts",
    ),
    (
        CONTACTS_MANAGEMENT_CREATE,
        "Create Project Contacts",
        "Create contacts within assigned projects",
        "contacts",
    ),
    (
        CONTACTS_MANAGEMENT_DELETE,
        "Delete Project Contacts",
        "Remove contacts within assigned projects",
        "contacts",
    ),
    (
        VISITOR_MANAGEMENT_VIEW,
        "View Visitor Logs",
        "View visitor logs, overview, and pass details",
        "visitor_logs",
    ),
    (
        VISITOR_MANAGEMENT_VERIFY,
        "Verify Visitor Passes",
        "Verify passes and record check-in/check-out at the gate",
        "visitor_logs",
    ),
    (
        FINANCE_MANAGEMENT_VIEW,
        "View Finance",
        "View fee configuration and maintenance fee invoices",
        "finance",
    ),
    (
        FINANCE_MANAGEMENT_EDIT,
        "Edit Finance Settings",
        "Create and update project fee configuration",
        "finance",
    ),
    (
        FINANCE_MANAGEMENT_ADMIN,
        "Administer Finance",
        "Generate invoices, run billing scheduler, and manage escalations",
        "finance",
    ),
    (
        WORK_ORDER_MANAGEMENT_VIEW,
        "View Work Order Management",
        "View assets, contracts, work orders, and invoices",
        "work_order_management",
    ),
    (
        WORK_ORDER_MANAGEMENT_EDIT,
        "Edit Work Order Management",
        "Create and update assets, contracts, and work orders",
        "work_order_management",
    ),
    (
        WORK_ORDER_MANAGEMENT_APPROVE,
        "Approve Vendor Invoices",
        "Approve or reject vendor invoices",
        "work_order_management",
    ),
    (
        WORK_ORDER_MANAGEMENT_PAY,
        "Record Vendor Payments",
        "Record payments against approved invoices",
        "work_order_management",
    ),
]

PROJECT_PERMISSION_CODES = frozenset(code for code, _, _, _ in DEFAULT_PROJECT_PERMISSIONS)


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

# CRM entity permissions that require custom-fields management permissions on a role.
ENTITY_PERMISSION_CODE_PREFIXES = (
    "leads_management.",
    "companies_management.",
    "contacts_management.",
    "projects_management.",
    "work_order_management.",
)

ALL_CUSTOM_FIELDS_MANAGEMENT_PERMISSION_CODES = frozenset(
    {
        CUSTOM_FIELDS_MANAGEMENT_CREATE,
        CUSTOM_FIELDS_MANAGEMENT_VIEW,
        CUSTOM_FIELDS_MANAGEMENT_EDIT,
        CUSTOM_FIELDS_MANAGEMENT_DELETE,
    }
)

ALL_EMAIL_TEMPLATES_MANAGEMENT_PERMISSION_CODES = frozenset(
    {
        EMAIL_TEMPLATES_MANAGEMENT_CREATE,
        EMAIL_TEMPLATES_MANAGEMENT_VIEW,
        EMAIL_TEMPLATES_MANAGEMENT_EDIT,
        EMAIL_TEMPLATES_MANAGEMENT_DELETE,
    }
)


def custom_fields_permission_codes_to_add(selected_codes: set[str]) -> set[str]:
    """Return custom-fields permission codes implied by the selected permission codes."""
    requires_custom_fields = any(
        code.startswith(prefix)
        for code in selected_codes
        for prefix in ENTITY_PERMISSION_CODE_PREFIXES
    )
    if not requires_custom_fields:
        return set()
    return ALL_CUSTOM_FIELDS_MANAGEMENT_PERMISSION_CODES - selected_codes
