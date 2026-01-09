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
from apps.user_service.app.schemas.auth import Subscription
from apps.user_service.app.schemas.enums import (
    AccountType,
    DeleteRequestStatus,
    OrganizationMemberStatus,
    OrganizationStatus,
    PlanType,
)
from apps.user_service.app.schemas.organizations import (
    DeleteRequestInfo,
    NewOrganizationBody,
    OrganizationAdminUpdate,
    OrganizationInfo,
    OrganizationListResponse,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_field,
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
        """Update organization fields with slug validation when provided.

        Supports partial updates for all fields.

        Returns:
            dict: Update result containing organization_id, organization_name, and slug
        """
        validate_uuid_format(organization_id, "organization_id")

        # Get existing organization data (full data for audit logging)
        existing = await self.organization_repository.get_organization_by_id(organization_id)
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
                "old_data": self._format_organization_for_audit(existing),
            }

        # Validate slug uniqueness if slug is being updated
        if "slug" in update_payload:
            await self._validate_slug_unique(update_payload["slug"], exclude_id=organization_id)

        # Parse existing settings from JSON string to dict
        existing_settings = parse_json_field(existing.get("settings"))

        # Build database payload with simplified logic
        db_payload = self._build_update_payload(existing_settings, update_payload)

        # Serialize JSON fields for asyncpg
        # _serialize_pydantic_models handles all types (dict, BaseModel, list, None, etc.)
        if "settings" in db_payload:
            serialized_settings = _serialize_pydantic_models(db_payload["settings"])
            db_payload["settings"] = json.dumps(serialized_settings)

        # Perform the update
        updated = await self.organization_repository.update_organization(
            organization_id=organization_id, update_data=db_payload
        )

        # Format old data for audit logging before returning
        old_data = self._format_organization_for_audit(existing)

        return {
            "organization_id": organization_id,
            "organization_name": updated.get("name", existing.get("name")),
            "slug": updated.get("slug", existing.get("slug")),
            "old_data": old_data,
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

    def _deep_merge_dict(self, base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        """Deep merge two dictionaries, preserving existing values when update has None.

        Args:
            base: Base dictionary with existing values
            update: Dictionary with updates (None values are skipped)

        Returns:
            Merged dictionary
        """
        result = base.copy()
        for key, value in update.items():
            if value is None:
                # Skip None values - don't overwrite existing data
                continue

            # Note: value is already a dict from model_dump() which recursively converts BaseModels
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                # Recursively merge nested dictionaries
                result[key] = self._deep_merge_dict(result[key], value)
            else:
                # Replace or add the value
                result[key] = value
        return result

    def _categorize_update_fields(
        self, update_data: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Categorize update fields into different types.

        Args:
            update_data: Update payload with only fields being updated

        Returns:
            Tuple of (direct_columns, nested_settings, simple_settings, practice_areas)
        """
        # Direct database columns (not stored in settings JSON)
        direct_columns = {
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

        # Nested JSON objects in settings that support partial updates (deep merge)
        nested_settings_fields = {
            "address",
            "compliance_security",
            "enterprise_features",
            "team_setup",
        }

        # Simple settings fields (lists, booleans) that are replaced entirely
        simple_settings_fields = {
            "preferred_integration",
            "need_help_importing_data",
            "need_migration_assistance",
        }

        # Practice area fields (replaced entirely, not merged)
        practice_area_fields = {
            "primary_practice_areas",
            "secondary_practice_areas",
            "specializations",
        }

        db_payload = {}
        nested_settings_updates = {}
        simple_settings_updates = {}
        practice_areas_updates = {}

        # Separate fields by type
        for field, value in update_data.items():
            if field in direct_columns:
                db_payload[field] = value
            elif field in practice_area_fields:
                practice_areas_updates[field] = value
            elif field in nested_settings_fields:
                nested_settings_updates[field] = value
            elif field in simple_settings_fields:
                simple_settings_updates[field] = value

        return db_payload, nested_settings_updates, simple_settings_updates, practice_areas_updates

    def _merge_nested_settings(
        self, merged_settings: dict[str, Any], nested_settings_updates: dict[str, Any]
    ) -> None:
        """Apply partial updates to nested JSON objects (deep merge subfields).

        Args:
            merged_settings: Settings dictionary to update in-place
            nested_settings_updates: Dictionary of nested settings to merge
        """
        for field, value in nested_settings_updates.items():
            if value is not None:
                existing_value = merged_settings.get(field, {})

                # Deep merge nested dictionaries for partial updates
                if isinstance(existing_value, dict) and isinstance(value, dict):
                    merged_settings[field] = self._deep_merge_dict(existing_value, value)
                else:
                    # If existing is not a dict, replace it
                    merged_settings[field] = value

    def _update_practice_areas(
        self, merged_settings: dict[str, Any], practice_areas_updates: dict[str, Any]
    ) -> None:
        """Update practice areas in merged settings.

        Args:
            merged_settings: Settings dictionary to update in-place
            practice_areas_updates: Dictionary of practice area updates
        """
        # Get existing practice_areas or initialize empty dict
        existing_practice_areas = merged_settings.get("practice_areas")
        if existing_practice_areas is None or not isinstance(existing_practice_areas, dict):
            practice_areas = {}
        else:
            # Copy existing practice areas to preserve fields not being updated
            practice_areas = existing_practice_areas.copy()

        # Update only the practice area lists that are provided in the update
        if "primary_practice_areas" in practice_areas_updates:
            practice_areas["primary"] = practice_areas_updates["primary_practice_areas"]
        if "secondary_practice_areas" in practice_areas_updates:
            practice_areas["secondary"] = practice_areas_updates["secondary_practice_areas"]
        if "specializations" in practice_areas_updates:
            practice_areas["specializations"] = practice_areas_updates["specializations"]

        merged_settings["practice_areas"] = practice_areas

    def _build_update_payload(
        self, existing_settings: dict[str, Any], update_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Build database update payload with simplified logic.

        Rules:
        - Direct database columns: Update directly
        - Nested JSON objects in settings (address, compliance_security,
          enterprise_features, team_setup):
          Partial merge - only updated subfields are merged
        - Simple settings fields (preferred_integration, need_help_importing_data, etc.):
          Replace entirely
        - Practice areas: Replace entirely (come as final lists from UI)

        Args:
            existing_settings: Existing settings JSON from database
            update_data: Update payload with only fields being updated

        Returns:
            Database payload ready for update
        """
        (
            db_payload,
            nested_settings_updates,
            simple_settings_updates,
            practice_areas_updates,
        ) = self._categorize_update_fields(update_data)

        # Build settings object if any settings fields are being updated
        if nested_settings_updates or simple_settings_updates or practice_areas_updates:
            # Start with existing settings to preserve fields not being updated
            merged_settings = existing_settings.copy()

            # Apply partial updates to nested JSON objects (deep merge subfields)
            self._merge_nested_settings(merged_settings, nested_settings_updates)

            # Replace simple settings fields entirely (no merging)
            for field, value in simple_settings_updates.items():
                merged_settings[field] = value

            # Update practice areas
            if practice_areas_updates:
                self._update_practice_areas(merged_settings, practice_areas_updates)

            db_payload["settings"] = merged_settings

        return db_payload

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
    def _format_organization_for_audit(org_data: dict[str, Any]) -> dict[str, Any]:
        """Format organization data for audit logging.

        Extracts and formats all organization fields including nested settings
        into a flat structure suitable for audit log comparison.

        Args:
            org_data: Raw organization data from database

        Returns:
            Dictionary with formatted organization data for audit logging
        """
        existing_settings = parse_json_field(org_data.get("settings"))
        is_settings_dict = isinstance(existing_settings, dict)
        practice_areas = existing_settings.get("practice_areas", {}) if is_settings_dict else {}
        is_practice_areas_dict = isinstance(practice_areas, dict)

        return {
            "organization_id": str(org_data["id"]),
            "name": org_data.get("name"),
            "slug": org_data.get("slug"),
            "domain": org_data.get("domain"),
            "logo_url": org_data.get("logo_url"),
            "status": org_data.get("status"),
            "timezone": org_data.get("timezone"),
            "description": org_data.get("description"),
            "company_size": org_data.get("company_size"),
            "industry": org_data.get("industry"),
            "referral_source": org_data.get("referral_source"),
            "address": existing_settings.get("address") if is_settings_dict else None,
            "preferred_integration": (
                existing_settings.get("preferred_integration") if is_settings_dict else None
            ),
            "need_help_importing_data": (
                existing_settings.get("need_help_importing_data") if is_settings_dict else None
            ),
            "need_migration_assistance": (
                existing_settings.get("need_migration_assistance") if is_settings_dict else None
            ),
            "compliance_security": (
                existing_settings.get("compliance_security") if is_settings_dict else None
            ),
            "enterprise_features": (
                existing_settings.get("enterprise_features") if is_settings_dict else None
            ),
            "team_setup": (existing_settings.get("team_setup") if is_settings_dict else None),
            "primary_practice_areas": (
                practice_areas.get("primary") if is_practice_areas_dict else None
            ),
            "secondary_practice_areas": (
                practice_areas.get("secondary") if is_practice_areas_dict else None
            ),
            "specializations": (
                practice_areas.get("specializations") if is_practice_areas_dict else None
            ),
        }

    @staticmethod
    def _map_to_organization_info(org_data: dict[str, Any]) -> OrganizationInfo:
        """Map raw DB row to OrganizationInfo schema."""
        settings = parse_json_field(org_data.get("settings"))
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
            "status": OrganizationStatus.ACTIVE.value,
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
            "status": OrganizationMemberStatus.ACTIVE.value,
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
            # Only raise exception if all emails failed
            if len(email_failures) == len(super_admin_emails):
                raise InternalServerErrorException(
                    message_key="organizations.errors.email_notification_failed",
                    custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
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
            send_organization_deletion_rejected_email(
                email=requester["email"],
                organization_name=organization_name,
                rejection_reason=reason,
            )

        return {
            "request_id": str(updated_request["id"]),
            "organization_id": str(updated_request["organization_id"]),
            "status": updated_request["status"],
            "decision_reason": updated_request.get("decision_reason"),
            "decision_at": format_iso_datetime(updated_request.get("decision_at")) or "",
        }
