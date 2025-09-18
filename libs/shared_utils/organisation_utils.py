"""
Organisation Utility Functions

This module provides utility functions for organisation management.
"""

# Standard library imports
from typing import Any, Dict

# Third-party imports
from fastapi import HTTPException, status

# Local imports

from apps.user_service.app.dependencies.logger import get_logger

from libs.shared_db.supabase_db.admin_operations.user import delete_auth_user
from libs.shared_db.postgres_db.user_service_operations.organisation_operations import (
    create_new_organisation,
    create_super_admin_role,
    create_default_permissions_for_organisation,
    assign_all_permissions_to_role,
    add_member_to_organisation,
)

logger = get_logger("organisation_utils")

async def create_organisation_with_super_admin(org_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new organisation."""
    try:
        org_result = await create_new_organisation(org_data)
        print(f"Created organization: {org_result['id']}")
        # logger.debug("Organization created - Request ID: %s, ",org_data["request_id"])
        # logger.debug("Organization ID: %s, ",org_result['id'])

        # Create Super Admin role
        super_admin_role_result = await create_super_admin_role(org_data["organization_id"])
        super_admin_role_id = super_admin_role_result['id']
        print(f"Created Super Admin role: {super_admin_role_id}")
        # logger.debug("Super Admin role created - Request ID: %s, ",org_data["request_id"])
        # logger.debug("Role ID: %s",super_admin_role_id)

        # Create default permissions
        permission_ids = await create_default_permissions_for_organisation(
            org_data["organization_id"]
        )
        print(f"Created {len(permission_ids)} default permissions")
        # logger.debug("Default permissions created - Request ID: %s, ",org_data["request_id"])
        # logger.debug("Permission count: %s",len(permission_ids))

        # Assign all permissions to Super Admin role
        await assign_all_permissions_to_role(
            super_admin_role_id, org_data["organization_id"]
        )
        print("Assigned permissions to Super Admin role")
        # logger.debug("Permissions assigned to role - Request ID: %s, ",org_data["request_id"])
        # logger.debug("Organization member created - Request ID: %s, ",org_data["request_id"])

        # Create organization member
        member_result = await add_member_to_organisation(org_data["organization_id"], {
            "user_id": org_data["user_id"],
            "email": org_data["email"],
            "first_name": org_data["first_name"],
            "last_name": org_data["last_name"],
            "phone": org_data["phone"],
            "timezone": org_data["timezone"],
            "role_id": super_admin_role_id,
            "status": "active",
        })
        logger.debug("Member ID: %s, User ID: %s",member_result['id'],org_data["user_id"])
        print(f"Created organization member: {member_result['id']}")
    except Exception as db_error:
        print(f"Database transaction failed: {db_error}")

        # # Try to delete the Supabase user if database transaction fails
        # try:
        #     await delete_auth_user(org_data["user_id"])
        #     # print(f"Cleaned up Supabase user: {org_data["user_id"]}")
        # except Exception as cleanup_error:  # noqa: W0718
        #     # print(f"Failed to cleanup Supabase user: {cleanup_error}")
        #     raise HTTPException(
        #         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        #         detail="Failed to create account. Please try again.",
        #     ) from cleanup_error

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create account. Please try again.",
        ) from db_error
