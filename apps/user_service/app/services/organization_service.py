"""Service for organization business logic."""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import asyncpg
from pydantic import BaseModel

from apps.user_service.app.db.repositories import (
    OrganizationDeleteRequestRepository,
    OrganizationMemberRepository,
    OrganizationRepository,
    PermissionsRepository,
    RoleRepository,
    TeamRepository,
)
from apps.user_service.app.schemas.auth import AccountType, PlanType, Subscription
from apps.user_service.app.schemas.organizations import (
    DeleteRequestInfo,
    DeleteRequestStatus,
    NewOrganizationBody,
    OrganizationAdminUpdate,
    OrganizationInfo,
    OrganizationListResponse,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    validate_uuid_format,
)
from apps.user_service.app.utils.email_utils import (
    send_organization_delete_request_email,
    send_organization_deletion_approved_email,
    send_organization_deletion_rejected_email,
)
from libs.shared_utils.http_exceptions import (
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
)
from libs.shared_utils.isometrik_service import (
    create_isometrik_application,
    create_isometrik_user,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.super_admin_utils import get_system_super_admin_emails

logger = get_logger("organization_service")


def _serialize_pydantic_models(value: Any) -> Any:
    """Recursively convert Pydantic models and other
    non-serializable objects to JSON-serializable primitives.

    Args:
        value: The value to serialize (can be Pydantic model, dict, list, enum, or primitive)

    Returns:
        JSON-serializable value
    """
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _serialize_pydantic_models(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_pydantic_models(item) for item in value]
    return value


class OrganizationService:
    """Service for organization business logic.

    User context is provided during initialization.
    """

    def __init__(
        self,
        user_context: UserContext,
        db_connection: asyncpg.Connection,
    ) -> None:
        self.user_context = user_context
        self.db_connection = db_connection
        self.organization_repository = OrganizationRepository(db_connection=db_connection)
        self.permissions_repository = PermissionsRepository(db_connection=db_connection)
        self.role_repository = RoleRepository(db_connection=db_connection)
        self.organization_member_repository = OrganizationMemberRepository(
            db_connection=db_connection
        )
        self.delete_request_repository = OrganizationDeleteRequestRepository(
            db_connection=db_connection
        )
        self.team_repository = TeamRepository(db_connection=db_connection)

    async def list_organizations(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        status: str | None = None,
    ) -> OrganizationListResponse:
        """Retrieve paginated list of organizations."""
        organizations_data = await self.organization_repository.get_organizations_list(
            search=search,
            status=status,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total_count = await self.organization_repository.get_organizations_count(
            search=search, status=status
        )

        items = [self._map_to_organization_info(org) for org in organizations_data]
        total_pages = math.ceil(total_count / page_size) if page_size else 0
        message = "success.no_data" if total_count == 0 else "success.retrieved"

        return OrganizationListResponse(
            data=items,
            total_count=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            message=message,
        )

    async def get_organization_detail(self, organization_id: str) -> OrganizationInfo:
        """Get organization by ID."""
        validate_uuid_format(organization_id, "organization_id")
        org = await self.organization_repository.get_organization_by_id(organization_id)
        if not org:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._map_to_organization_info(org)

    async def create_organization(
        self,
        body: NewOrganizationBody,
        slug: str | None,
    ) -> dict:
        """Create a new organization after slug uniqueness check."""
        # Validate user context
        if self.user_context.user_id is None:
            raise ForbiddenException(
                message_key="organizations.errors.forbidden",
                custom_code=CustomStatusCode.FORBIDDEN,
            )
        if self.user_context.organization_id is not None:
            raise ConflictException(
                message_key="organizations.errors.conflict",
                custom_code=CustomStatusCode.CONFLICT,
            )

        organization_id = str(uuid.uuid4())
        validate_uuid_format(organization_id, "organization_id")

        resolved_slug = slug or self._generate_slug(
            body.company_data.company_name, AccountType.BUSINESS.value
        )
        await self._validate_slug_unique(resolved_slug)

        subscription = self._build_subscription(body)
        settings = self._build_settings(body)
        isometrik_details = await self._create_isometrik_application_if_enabled(body)

        org_payload = self._build_organization_payload(
            organization_id=organization_id,
            resolved_slug=resolved_slug,
            body=body,
            subscription=subscription,
            settings=settings,
            isometrik_details=isometrik_details,
        )

        created = await self.organization_repository.create_organization(org_payload)

        permission_ids = await self.permissions_repository.create_default_permissions(
            organization_id=organization_id
        )
        super_admin_role_id = await self._create_super_admin_role(organization_id, permission_ids)
        # Pass isometrik_details to _add_requesting_user_as_member
        await self._add_requesting_user_as_member(
            organization_id=organization_id,
            role_id=super_admin_role_id,
            body=body,
            isometrik_creds=isometrik_details,  # Pass the isometrik details here
        )
        # Match API response shape
        return {
            "organization_id": organization_id,
            "organization_name": created["name"],
            "slug": created["slug"],
            "user_id": self.user_context.user_id,
            "user_email": self.user_context.email,
            "role_name": "admin",
        }

    async def update_organization(
        self, organization_id: str, update_data: OrganizationAdminUpdate
    ) -> dict:
        """Update organization fields with slug validation when provided."""
        validate_uuid_format(organization_id, "organization_id")

        # Get only the minimal fields needed for update (id, name, slug, settings)
        existing = await self.organization_repository.get_organization_for_update(organization_id)
        if not existing:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Extract only fields that were actually set
        update_payload = update_data.model_dump(exclude_none=True, exclude_unset=True)

        if not update_payload:
            return {
                "organization_id": organization_id,
                "organization_name": existing.get("name"),
                "slug": existing.get("slug"),
            }

        # Validate slug uniqueness if slug is being updated
        if "slug" in update_payload:
            await self._validate_slug_unique(update_payload["slug"], exclude_id=organization_id)

        # Transform update payload to database structure
        # Only pass existing settings for comparison, not the entire organization object
        existing_settings = existing.get("settings") or {}
        db_payload = self._transform_update_to_db_format(existing_settings, update_payload)

        # Convert any Pydantic models to dicts and serialize JSON fields for asyncpg
        if "settings" in db_payload and isinstance(db_payload["settings"], dict):
            serialized_settings = _serialize_pydantic_models(db_payload["settings"])
            db_payload["settings"] = json.dumps(serialized_settings)
        if "subscription" in db_payload and isinstance(db_payload["subscription"], dict):
            serialized_subscription = _serialize_pydantic_models(db_payload["subscription"])
            db_payload["subscription"] = json.dumps(serialized_subscription)

        # Perform the update
        updated = await self.organization_repository.update_organization(
            organization_id=organization_id, update_data=db_payload
        )
        return {
            "organization_id": organization_id,
            "organization_name": updated.get("name", existing.get("name")),
            "slug": updated.get("slug", existing.get("slug")),
        }

    async def delete_organization(self, organization_id: str) -> None:
        """Soft delete organization."""
        validate_uuid_format(organization_id, "organization_id")
        await self.organization_repository.delete_organization(organization_id)

    async def _validate_slug_unique(self, slug: str, exclude_id: str | None = None) -> None:
        """Ensure organization slug is unique."""
        is_unique = await self.organization_repository.check_slug_unique(slug, exclude_id)
        if not is_unique:
            raise ConflictException(
                message_key="organizations.errors.slug_conflict",
                custom_code=CustomStatusCode.CONFLICT,
            )

    @staticmethod
    def _transform_update_to_db_format(
        existing_settings: dict[str, Any], update_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Transform update payload to database structure.

        Update Rules:
        1. Settings fields (address, practice_areas, etc.): UI sends entire objects,
           so we replace them entirely in the database.
        2. Direct fields (name, slug, etc.): Only changed fields are sent,
           so we update only what's provided.

        Args:
            existing_settings: Existing settings from organization (only settings field)
            update_data: Update payload with only fields being updated

        Returns:
            Transformed payload ready for database update
        """
        # Top-level database columns (not in settings)
        # These are only updated if present in payload
        top_level_fields = {
            "name",
            "slug",
            "domain",
            "status",
            "timezone",
            "logo_url",
            "industry",
            "company_size",
            "description",
            "referral_source",
        }

        # Settings fields that are replaced entirely when provided
        # UI always sends complete objects for these fields
        settings_object_fields = {
            "address",
            "preferred_integration",
            "compliance_security",
            "enterprise_features",
            "team_setup",
            "need_help_importing_data",
            "need_migration_assistance",
        }

        # Practice area fields map to nested settings.practice_areas structure
        practice_area_field_map = {
            "primary_practice_areas": "primary",
            "secondary_practice_areas": "secondary",
            "specializations": "specializations",
        }

        # Separate fields by type
        db_payload = {}
        settings_updates = {}
        practice_areas_data = {}

        for field, value in update_data.items():
            if field in top_level_fields:
                # Direct fields: only update if present in payload
                db_payload[field] = value
            elif field in settings_object_fields:
                # Settings objects: replace entirely (UI sends complete objects)
                settings_updates[field] = value
            elif field in practice_area_field_map:
                # Practice areas: map to nested structure (treated same as other settings)
                practice_areas_data[practice_area_field_map[field]] = value

        # Build settings object if any settings fields are being updated
        if settings_updates or practice_areas_data:
            # Start with existing settings to preserve fields not being updated
            merged_settings = (
                existing_settings.copy() if isinstance(existing_settings, dict) else {}
            )

            # Replace entire settings object fields (no sub-field merging)
            # UI sends complete objects, so we replace them entirely
            for field, value in settings_updates.items():
                merged_settings[field] = value

            # Replace entire practice_areas object (same as other settings fields)
            if practice_areas_data:
                merged_settings["practice_areas"] = {
                    "primary": practice_areas_data.get("primary"),
                    "secondary": practice_areas_data.get("secondary"),
                    "specializations": practice_areas_data.get("specializations"),
                }

            db_payload["settings"] = merged_settings

        return db_payload

    @staticmethod
    def _parse_settings(settings_raw: Any) -> dict[str, Any]:
        """Parse settings from raw database value.

        Args:
            settings_raw: Settings value from database (can be str, dict, or None)

        Returns:
            Parsed settings dictionary, empty dict if invalid
        """
        if isinstance(settings_raw, str):
            try:
                return json.loads(settings_raw)
            except (json.JSONDecodeError, TypeError):
                return {}
        return settings_raw or {}

    @staticmethod
    def _parse_subscription(subscription_raw: Any) -> Subscription | None:
        """Parse subscription from raw database value.

        Args:
            subscription_raw: Subscription value from database (can be str, dict, or None)

        Returns:
            Subscription object if valid, None otherwise
        """
        if not subscription_raw:
            return None

        if isinstance(subscription_raw, str):
            try:
                subscription_dict = json.loads(subscription_raw)
            except (json.JSONDecodeError, TypeError):
                return None
        else:
            subscription_dict = subscription_raw

        if subscription_dict and isinstance(subscription_dict, dict):
            try:
                return Subscription(**subscription_dict)
            except Exception:
                return None

        return None

    @staticmethod
    def _extract_settings_fields(settings: dict[str, Any]) -> dict[str, Any]:
        """Extract fields from settings dictionary.

        Args:
            settings: Settings dictionary

        Returns:
            Dictionary with extracted fields
        """
        practice_areas = settings.get("practice_areas", {}) if isinstance(settings, dict) else {}

        return {
            "address": settings.get("address"),
            "preferred_integration": settings.get("preferred_integration"),
            "need_help_importing_data": settings.get("need_help_importing_data"),
            "need_migration_assistance": settings.get("need_migration_assistance"),
            "compliance_security": settings.get("compliance_security"),
            "enterprise_features": settings.get("enterprise_features"),
            "team_setup": settings.get("team_setup"),
            "primary_practice_areas": practice_areas.get("primary"),
            "secondary_practice_areas": practice_areas.get("secondary"),
            "specializations": practice_areas.get("specializations"),
        }

    @staticmethod
    def _map_to_organization_info(org_data: dict[str, Any]) -> OrganizationInfo:
        """Map raw DB row to OrganizationInfo schema."""
        settings = OrganizationService._parse_settings(org_data.get("settings"))
        subscription_obj = OrganizationService._parse_subscription(org_data.get("subscription"))
        settings_fields = OrganizationService._extract_settings_fields(settings)

        return OrganizationInfo(
            organization_id=str(org_data["id"]),
            name=org_data.get("name"),
            slug=org_data.get("slug"),
            domain=org_data.get("domain"),
            logo_url=org_data.get("logo_url"),
            subscription=subscription_obj,
            status=org_data.get("status"),
            timezone=org_data.get("timezone") or "UTC",
            created_at=format_iso_datetime(org_data.get("created_at")) or "",
            updated_at=format_iso_datetime(org_data.get("updated_at")) or "",
            member_count=org_data.get("member_count", 0),
            description=org_data.get("description"),
            company_size=org_data.get("company_size"),
            address=settings_fields["address"],
            preferred_integration=settings_fields["preferred_integration"],
            need_help_importing_data=settings_fields["need_help_importing_data"],
            need_migration_assistance=settings_fields["need_migration_assistance"],
            compliance_security=settings_fields["compliance_security"],
            enterprise_features=settings_fields["enterprise_features"],
            team_setup=settings_fields["team_setup"],
            primary_practice_areas=settings_fields["primary_practice_areas"],
            secondary_practice_areas=settings_fields["secondary_practice_areas"],
            specializations=settings_fields["specializations"],
        )

    @staticmethod
    def _generate_slug(name: str, account_type: str = AccountType.BUSINESS.value) -> str:
        """Generate a URL-friendly slug from organization name with account type prefix."""
        clean_name = name.lower().strip()
        normalized = "".join(c if c.isalnum() else "-" for c in clean_name)
        compact = "-".join(filter(None, normalized.split("-")))
        prefix = "personal" if account_type == AccountType.PERSONAL.value else "business"
        return f"{prefix}-{compact}"

    def _build_subscription(self, body: NewOrganizationBody) -> dict:
        """Create subscription payload with trial defaults when missing."""
        subscription = getattr(body.company_data, "subscription", None)
        if subscription:
            if hasattr(subscription, "model_dump"):
                return subscription.model_dump(exclude_none=True)
            return subscription

        now = datetime.now(timezone.utc)
        return {
            "plan_type": PlanType.TRIAL,
            "max_users": 5,
            "start_date": now.isoformat(),
            "end_date": (now + timedelta(days=7)).isoformat(),
        }

    def _build_settings(self, body: NewOrganizationBody) -> dict:
        """Build settings payload; fall back to derived defaults when not provided."""
        provided_settings = getattr(body.company_data, "settings", None)
        if provided_settings:
            # Convert Pydantic models to dicts if settings are provided
            return _serialize_pydantic_models(provided_settings)

        settings = {
            "practice_areas": {
                "primary": body.company_data.primary_practice_areas,
                "secondary": body.company_data.secondary_practice_areas,
                "specializations": body.company_data.specializations,
            },
            "preferred_integration": body.company_data.preferred_integration,
            "need_help_importing_data": body.company_data.need_help_importing_data,
            "need_migration_assistance": body.company_data.need_migration_assistance,
            "compliance_security": body.company_data.compliance_security,
            "enterprise_features": body.company_data.enterprise_features,
            "team_setup": body.company_data.team_setup,
            "address": body.company_data.address,
        }
        # Convert any Pydantic models to dicts for JSON serialization
        return _serialize_pydantic_models(settings)

    async def _create_isometrik_application_if_enabled(
        self, body: NewOrganizationBody
    ) -> dict | None:
        """Create isometrik application."""
        try:
            resp = await create_isometrik_application(
                organization_name=body.company_data.company_name,
                product_types=["chat", "video"],
                plan="basic",
            )
            if resp and resp.get("data"):
                return resp.get("data")
        except Exception as error:
            raise error

    def _build_organization_payload(
        self,
        organization_id: str,
        resolved_slug: str,
        body: NewOrganizationBody,
        subscription: dict,
        settings: dict,
        isometrik_details: dict | None,
    ) -> dict:
        """Assemble organization payload for repository."""
        # Add isometrik_application_details to settings if provided
        if isometrik_details is not None:
            settings = settings.copy() if settings else {}
            settings["isometrik_application_details"] = isometrik_details

        # Convert any remaining Pydantic models to dicts before JSON serialization
        serialized_settings_dict = _serialize_pydantic_models(settings) if settings else None
        serialized_subscription_dict = (
            _serialize_pydantic_models(subscription) if subscription else None
        )

        # Serialize JSON fields for asyncpg (business logic in service layer)
        serialized_settings = (
            json.dumps(serialized_settings_dict) if serialized_settings_dict else None
        )
        serialized_subscription = (
            json.dumps(serialized_subscription_dict) if serialized_subscription_dict else None
        )

        return {
            "id": organization_id,
            "name": body.company_data.company_name,
            "slug": resolved_slug,
            "domain": body.company_data.company_website,
            "logo_url": body.company_data.logo_url,
            "status": "active",
            "description": body.company_data.description,
            "company_size": body.company_data.company_size,
            "settings": serialized_settings,
            "subscription": serialized_subscription,
            "created_by_id": self.user_context.user_id,
        }

    async def _create_super_admin_role(
        self, organization_id: str, permission_ids: list[str]
    ) -> str:
        """Create super admin role and attach permissions."""
        super_admin_role = await self.role_repository.create_role(
            name="admin",
            description="Full administrative access to all system features",
            organization_id=organization_id,
            is_default=True,
        )
        super_admin_role_id = str(super_admin_role["id"])
        if permission_ids:
            await self.role_repository.assign_permissions_to_role(
                role_id=super_admin_role_id,
                organization_id=organization_id,
                permission_ids=permission_ids,
            )
        return super_admin_role_id

    async def _add_requesting_user_as_member(
        self,
        organization_id: str,
        role_id: str,
        body: NewOrganizationBody,
        isometrik_creds: dict | None = None,
    ) -> None:
        """Add the requesting user as an organization member with the provided role.

        Args:
            organization_id: The ID of the organization
            role_id: The ID of the role to assign to the member
            body: The request body containing member data
            isometrik_creds: Optional Isometrik credentials if already available
        """
        member_data = {
            "user_id": self.user_context.user_id,
            "email": self.user_context.email,
            "first_name": getattr(body.user_data, "first_name", None) if body.user_data else None,
            "last_name": getattr(body.user_data, "last_name", None) if body.user_data else None,
            "phone": getattr(body.user_data, "phone", None) if body.user_data else None,
            "timezone": getattr(body.user_data, "timezone", None) or "UTC",
            "role_id": role_id,
            "status": "active",
        }

        # Create Isometrik user if enabled and credentials are provided
        if isometrik_creds:
            isometrik_user = await create_isometrik_user(
                user_id=member_data["user_id"],
                first_name=member_data["first_name"],
                last_name=member_data["last_name"],
                email=member_data["email"],
                isometrik_credentials=isometrik_creds,
                organization_id=organization_id,
                role="admin",  # Default role for organization creators
            )
            if isometrik_user and (user_id := isometrik_user.get("userId")):
                member_data["isometrik_user_id"] = user_id

        await self.organization_member_repository.add_member(
            organization_id=organization_id, member_data=member_data
        )

    async def _notify_super_admins(
        self,
        organization_name: str,
        requester_email: str,
    ) -> None:
        """Notify all system super admin users about an organization delete request.

        Args:
            organization_name (str): Name of the organization requested for deletion
            requester_email (str): Email of the user who requested the deletion

        Raises:
            InternalServerErrorException: If email notification fails
        """
        # Get all super admin emails using utility function
        super_admin_emails = await get_system_super_admin_emails(self.db_connection)

        if not super_admin_emails:
            logger.warning("No system super admin users found to notify")
            return

        # Send email notifications to all super admins
        email_failures = []
        for super_admin_email in super_admin_emails:
            email_sent = send_organization_delete_request_email(
                email=super_admin_email,
                organization_name=organization_name,
                requester_email=requester_email,
            )
            if not email_sent:
                email_failures.append(super_admin_email)

        if email_failures:
            logger.warning(
                "Failed to send delete request emails to some super admins: %s",
                email_failures,
            )
            # Only raise exception if all emails failed
            if len(email_failures) == len(super_admin_emails):
                raise InternalServerErrorException(
                    message_key="organizations.errors.email_notification_failed",
                    custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
                )

        logger.info(
            "Sent organization delete request notifications to %d super admin(s)",
            len(super_admin_emails) - len(email_failures),
        )

    async def create_delete_request(
        self,
        organization_id: str,
    ) -> dict[str, Any]:
        """Create a delete request for an organization.

        Business Rules:
        - Only the organization creator can create a delete request
        - User must be a member of the organization (validated in endpoint)
        - Cannot create a duplicate pending request for the same organization
        - Sends email notifications to all system super admin users

        Args:
            organization_id (str): Organization ID

        Returns:
            dict[str, Any]: Created delete request record

        Raises:
            NotFoundException: If organization is not found
            ConflictException: If duplicate pending request exists
            InternalServerErrorException: If email notification fails
        """
        validate_uuid_format(organization_id, "organization_id")

        # Verify organization exists
        # Fetch organization details to get name
        organization = await self.organization_repository.get_organization_by_id(organization_id)
        if not organization:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Check for duplicate pending request
        existing_request = (
            await self.delete_request_repository.get_pending_request_by_organization_and_requester(
                organization_id=organization_id,
                requester_id=self.user_context.user_id,
            )
        )
        if existing_request:
            raise ConflictException(
                message_key="organizations.errors.duplicate_delete_request",
                custom_code=CustomStatusCode.CONFLICT,
            )

        organization_name = organization.get("name")

        # Create delete request
        delete_request = await self.delete_request_repository.create_delete_request(
            organization_id=organization_id,
            requester_id=self.user_context.user_id,
        )

        # Get all super admin users and send email notifications
        try:
            await self._notify_super_admins(
                organization_name=organization_name,
                requester_email=self.user_context.email,
            )
        except Exception as error:
            logger.error(
                "Failed to send email notifications to super admins for delete request: %s",
                str(error),
            )

        return delete_request

    async def list_delete_requests(
        self,
        page: int = 1,
        page_size: int = 20,
        organization_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve paginated list of delete requests.

        Args:
            page (int): Page number (1-indexed)
            page_size (int): Number of items per page
            organization_id (str | None): Optional organization ID to filter by
            status (str | None): Optional status to filter by

        Returns:
            dict[str, Any]: Dictionary containing:
                - data: List of delete request records
                - total_count: Total number of delete requests
                - page: Current page number
                - page_size: Items per page
                - total_pages: Total number of pages
        """
        # Validate organization_id format if provided
        if organization_id:
            validate_uuid_format(organization_id, "organization_id")

        # Calculate offset
        offset = (page - 1) * page_size

        # Get paginated delete requests
        delete_requests = await self.delete_request_repository.get_delete_requests_list(
            organization_id=organization_id,
            status=status,
            limit=page_size,
            offset=offset,
        )

        # Get total count
        total_count = await self.delete_request_repository.get_delete_requests_count(
            organization_id=organization_id,
            status=status,
        )

        # Convert delete requests to models
        formatted_requests = []
        for request in delete_requests:
            delete_request_info = DeleteRequestInfo(
                request_id=str(request["id"]),
                organization_id=str(request["organization_id"]),
                requester_id=str(request["requester_id"]),
                status=request["status"],
                requested_at=format_iso_datetime(request["requested_at"]) or "",
                decision_at=format_iso_datetime(request.get("decision_at")) or None,
                processed_at=format_iso_datetime(request.get("processed_at")) or None,
                approver_id=str(request["approver_id"]) if request.get("approver_id") else None,
                decision_reason=request.get("decision_reason"),
                created_at=format_iso_datetime(request.get("created_at")) or "",
                updated_at=format_iso_datetime(request.get("updated_at")) or "",
            )
            formatted_requests.append(delete_request_info)

        total_pages = math.ceil(total_count / page_size) if page_size > 0 else 0

        return {
            "data": formatted_requests,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    async def process_delete_request(
        self,
        request_id: str,
        is_accepted: bool,
        reason: str,
    ) -> dict[str, Any]:
        """Process (approve/reject) a delete request for an organization.

        Business Rules:
        - Only super admins can process delete requests (validated in endpoint)
        - Request must be in pending status (DeleteRequestStatus.PENDING)
        - If approved: Delete organization and all related data, send notifications
        - If rejected: Update request status, send rejection notification

        Args:
            request_id (str): Delete request ID
            is_accepted (bool): True to approve, False to reject
            reason (str): Reason for the decision

        Returns:
            dict[str, Any]: Result containing request status and details

        Raises:
            NotFoundException: If organization or request not found
            ForbiddenException: If request is not in pending status
            InternalServerErrorException: If deletion or email notification fails
        """
        # Validate UUID format
        validate_uuid_format(request_id, "request_id")

        # Verify delete request exists and get organization_id from it
        delete_request = await self.delete_request_repository.get_delete_request_by_id(request_id)
        if not delete_request:
            raise NotFoundException(
                message_key="organizations.errors.delete_request_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Extract organization_id from the delete request
        organization_id = str(delete_request["organization_id"])

        # Verify organization exists
        organization = await self.organization_repository.get_organization_by_id(organization_id)
        if not organization:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        organization_name = organization.get("name")

        # Verify request is in pending status
        if delete_request["status"] != DeleteRequestStatus.PENDING.value:
            raise ForbiddenException(
                message_key="organizations.errors.delete_request_already_processed",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        if is_accepted:
            return await self._approve_delete_request(
                request_id=request_id,
                organization_id=organization_id,
                organization_name=organization_name,
                reason=reason,
            )

        return await self._reject_delete_request(
            request_id=request_id,
            organization_id=organization_id,
            organization_name=organization_name,
            delete_request=delete_request,
            reason=reason,
        )

    async def _approve_delete_request(
        self,
        request_id: str,
        organization_id: str,
        organization_name: str,
        reason: str,
    ) -> dict[str, Any]:
        """Handle the approval flow for a delete request.

        This method:
        1. Collects member emails before deletion
        2. Deletes all related data in correct order
        3. Updates delete request status to approved
        4. Sends notification emails to all members

        Args:
            request_id (str): Delete request ID
            organization_id (str): Organization ID
            organization_name (str): Organization name
            reason (str): Reason for approval

        Returns:
            dict[str, Any]: Result containing request status and details
        """
        # Get all organization members before deletion (for email notifications)
        members = await self.organization_member_repository.get_all_members_by_organization_id(
            organization_id
        )
        member_emails = [member.get("email") for member in members if member.get("email")]

        # Delete all related data first (in correct order to respect foreign keys)
        # 1. Delete team members and teams
        await self.team_repository.delete_all_teams_by_organization_id(organization_id)

        # 2. Delete roles
        await self.role_repository.delete_all_roles_by_organization_id(organization_id)

        # 3. Delete permissions
        await self.permissions_repository.delete_all_permissions_by_organization_id(organization_id)

        # 4. Delete organization members
        await self.organization_member_repository.delete_all_members_by_organization_id(
            organization_id
        )

        # 5. Delete organization
        await self.organization_repository.delete_organization(organization_id)

        # Update delete request status to approved
        updated_request = await self.delete_request_repository.approve_delete_request(
            request_id=request_id,
            approver_id=self.user_context.user_id,
            decision_reason=reason,
        )

        # Send deletion notification emails to all organization members
        email_failures = []
        for email in member_emails:
            email_sent = send_organization_deletion_approved_email(
                email=email,
                organization_name=organization_name,
            )
            if not email_sent:
                email_failures.append(email)

        if email_failures:
            logger.warning(
                "Failed to send deletion notification emails to some members: %s",
                email_failures,
            )
            # Don't fail the operation if some emails fail, but log it

        logger.info(
            "Approved and processed delete request %s for organization %s",
            request_id,
            organization_id,
        )

        return {
            "request_id": str(updated_request["id"]),
            "organization_id": str(updated_request["organization_id"]),
            "status": updated_request["status"],
            "decision_reason": updated_request.get("decision_reason"),
            "decision_at": format_iso_datetime(updated_request.get("decision_at")) or "",
        }

    async def _reject_delete_request(
        self,
        request_id: str,
        organization_id: str,
        organization_name: str,
        delete_request: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """Handle the rejection flow for a delete request.

        This method:
        1. Gets requester details
        2. Updates delete request status to rejected
        3. Sends rejection notification email to requester

        Args:
            request_id (str): Delete request ID
            organization_id (str): Organization ID
            organization_name (str): Organization name
            delete_request (dict[str, Any]): Delete request record
            reason (str): Reason for rejection

        Returns:
            dict[str, Any]: Result containing request status and details
        """
        # Get requester details from organization members
        requester_id = delete_request["requester_id"]
        requester = await self.organization_member_repository.get_user_profile_by_id(
            requester_id, organization_id
        )

        # Update delete request status to rejected
        updated_request = await self.delete_request_repository.reject_delete_request(
            request_id=request_id,
            approver_id=self.user_context.user_id,
            decision_reason=reason,
        )

        # Send rejection notification email to requester
        if requester and requester.get("email"):
            email_sent = send_organization_deletion_rejected_email(
                email=requester["email"],
                organization_name=organization_name,
                rejection_reason=reason,
            )
            if not email_sent:
                logger.warning(
                    "Failed to send rejection notification email to requester: %s",
                    requester["email"],
                )
        else:
            logger.warning(
                "Requester not found or has no email for delete request %s",
                request_id,
            )

        logger.info(
            "Rejected delete request %s for organization %s",
            request_id,
            organization_id,
        )

        return {
            "request_id": str(updated_request["id"]),
            "organization_id": str(updated_request["organization_id"]),
            "status": updated_request["status"],
            "decision_reason": updated_request.get("decision_reason"),
            "decision_at": format_iso_datetime(updated_request.get("decision_at")) or "",
        }
