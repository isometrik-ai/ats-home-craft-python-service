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
    OrganizationMemberRepository,
    OrganizationRepository,
    PermissionsRepository,
    RoleRepository,
)
from apps.user_service.app.schemas.auth import AccountType, PlanType, Subscription
from apps.user_service.app.schemas.organizations import (
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
from libs.shared_utils.http_exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from libs.shared_utils.isometrik_service import (
    create_isometrik_application,
    create_isometrik_user,
)
from libs.shared_utils.status_codes import CustomStatusCode


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
