"""Service for organization business logic."""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories import (
    LeadStageRepository,
    OrganizationDeleteRequestRepository,
    OrganizationMemberRepository,
    OrganizationRepository,
    PermissionsRepository,
    RoleRepository,
    TeamRepository,
)
from apps.user_service.app.db.repositories.email_template_repository import (
    EmailTemplateRepository,
)
from apps.user_service.app.schemas.ai_overview_settings import AiOverviewSettings
from apps.user_service.app.schemas.common import OrganizationBasicDetails, Subscription
from apps.user_service.app.schemas.enums import (
    AccountType,
    DeleteRequestStatus,
    OrganizationMemberRole,
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
from apps.user_service.app.services.ai_overview_settings_ops import (
    coerce_overview_prompts_dict,
)
from apps.user_service.app.services.ai_overview_settings_ops import (
    default_ai_overview_settings as platform_default_ai_overview_settings,
)
from apps.user_service.app.services.ai_overview_settings_ops import (
    merge_ai_overview_settings_into_settings,
    parse_stored_ai_overview_settings,
    resolve_effective_ai_overview_settings,
)
from apps.user_service.app.services.organization_memory_service import (
    effective_organization_memory_enabled,
)
from apps.user_service.app.services.session_management_service import (
    SessionManagementService,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_field,
    serialize_pydantic_models,
    validate_uuid_format,
)
from apps.user_service.app.utils.email_utils import (
    send_organization_delete_request_email,
    send_organization_deletion_approved_email,
    send_organization_deletion_rejected_email,
)
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    ForbiddenException,
    InternalServerErrorException,
    NotFoundException,
    ServiceUnavailableException,
)
from libs.shared_utils.isometrik_service import (
    DEFAULT_ORG_ROLE,
    create_isometrik_application,
    create_isometrik_user,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode
from libs.shared_utils.super_admin_utils import get_system_super_admin_emails

logger = get_logger("organization_service")


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
        self.lead_stage_repository = LeadStageRepository(db_connection=db_connection)
        self.email_template_repository = EmailTemplateRepository(db_connection=db_connection)

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

        items = [
            self._map_to_organization_info(org, include_ai_overview_settings=False)
            for org in organizations_data
        ]
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

    async def get_ai_overview_settings(self, organization_id: str) -> AiOverviewSettings:
        """Return effective AI overview prompts and business background for an organization."""
        org = await self.organization_repository.get_organization_by_id(organization_id)
        if not org:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        settings = parse_json_field(org.get("settings"))
        return resolve_effective_ai_overview_settings(settings)

    async def refetch_ai_overview_settings(self, fields: list[str]) -> dict[str, Any]:
        """Refetch selected AI overview fields for the session organization."""
        from apps.user_service.app.services.org_business_overview_enrichment_service import (
            OrgBusinessOverviewEnrichmentService,
            strands_enrichment_enabled,
        )

        organization_id = self.user_context.organization_id
        if not organization_id:
            raise BadRequestException(
                message_key="organizations.errors.user_not_a_member_of_any_organization",
                custom_code=CustomStatusCode.INVALID_DATA,
            )
        if not strands_enrichment_enabled():
            raise ServiceUnavailableException(
                message_key="errors.service_unavailable",
                custom_code=CustomStatusCode.SERVICE_UNAVAILABLE,
                params={"reason": "strands_enrichment_not_configured"},
            )

        return await OrgBusinessOverviewEnrichmentService.refetch_ai_overview_fields(
            organization_id=organization_id,
            fields=fields,
            organization_repository=self.organization_repository,
        )

    async def _enqueue_business_overview_enrichment(
        self,
        *,
        organization_id: str,
        organization_name: str,
        organization_website: str | None,
        settings: dict[str, Any] | None,
    ) -> None:
        """Publish org enrichment job to Kafka after create (best-effort; never raises)."""
        from apps.user_service.app.services.org_business_overview_enrichment_service import (
            OrgBusinessOverviewEnrichmentService,
        )

        await OrgBusinessOverviewEnrichmentService.enqueue_enrichment_requested(
            organization_id=organization_id,
            organization_name=organization_name,
            organization_website=organization_website,
            settings=settings,
            actor_user_id=self.user_context.user_id,
        )

    async def create_organization(
        self,
        body: NewOrganizationBody,
        slug: str | None,
        session_id: str,
    ) -> dict:
        """Create a new organization after slug uniqueness check.

        Args:
            body: Organization creation data
            slug: Optional slug for the organization
            session_id: Session ID to validate and update organization context

        Raises:
            BadRequestException: If session is already linked to an organization
            ForbiddenException: If user context is invalid
        """
        # Validate user context
        if self.user_context.user_id is None:
            raise ForbiddenException(
                message_key="organizations.errors.forbidden",
                custom_code=CustomStatusCode.FORBIDDEN,
            )

        # Check if session is already linked to an organization before creating a new one
        session_manager = SessionManagementService(db_connection=self.db_connection)

        # Check if session already has an organization context
        current_org_id = await session_manager.session_repository.get_session_organization_id(
            session_id
        )

        # Restrict user from creating an organization if session is already linked with one
        if current_org_id is not None:
            raise BadRequestException(
                message_key="organizations.errors.session_already_linked",
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

        await self.lead_stage_repository.bulk_insert_default_stages_for_organization(
            organization_id
        )
        await self.email_template_repository.insert_default_layout(organization_id)

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

        # Sync session organization context after successful creation
        await session_manager.update_session_organization_context(
            session_id=session_id,
            user_id=self.user_context.user_id,
            organization_id=organization_id,
        )

        await self._enqueue_business_overview_enrichment(
            organization_id=organization_id,
            organization_name=created["name"],
            organization_website=body.company_data.company_website,
            settings=settings,
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

    async def create_organization_for_owner(
        self,
        body: NewOrganizationBody,
        slug: str | None = None,
    ) -> dict:
        """Create an organization for ``user_context`` without session linking.

        Used by superadmin-driven creation; reuses the same internal setup as
        ``create_organization`` but skips session validation and context updates.
        """
        if self.user_context.user_id is None:
            raise ForbiddenException(
                message_key="organizations.errors.forbidden",
                custom_code=CustomStatusCode.FORBIDDEN,
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

        await self.lead_stage_repository.bulk_insert_default_stages_for_organization(
            organization_id
        )
        await self.email_template_repository.insert_default_layout(organization_id)

        permission_ids = await self.permissions_repository.create_default_permissions(
            organization_id=organization_id
        )
        super_admin_role_id = await self._create_super_admin_role(organization_id, permission_ids)
        await self._add_requesting_user_as_member(
            organization_id=organization_id,
            role_id=super_admin_role_id,
            body=body,
            isometrik_creds=isometrik_details,
        )

        await self._enqueue_business_overview_enrichment(
            organization_id=organization_id,
            organization_name=created["name"],
            organization_website=body.company_data.company_website,
            settings=settings,
        )

        return {
            "organization_id": organization_id,
            "organization_name": created["name"],
            "slug": created["slug"],
            "user_id": self.user_context.user_id,
            "user_email": self.user_context.email,
            "role_name": "admin",
        }

    @staticmethod
    def _build_admin_update_payload(update_data: OrganizationAdminUpdate) -> dict[str, Any]:
        """Normalize admin PATCH body, including AI overview and repopulate flags."""
        update_payload = update_data.model_dump(exclude_none=True, exclude_unset=True)
        if "ai_overview_settings" in update_data.model_fields_set:
            ai_patch = update_data.ai_overview_settings
            update_payload["ai_overview_settings"] = (
                ai_patch.model_dump(exclude_unset=True) if ai_patch is not None else {}
            )
        if update_data.repopulate_ai_overview_prompts:
            reset_types = list(update_data.repopulate_ai_overview_prompts)
            update_payload.pop("repopulate_ai_overview_prompts", None)
            patch = update_payload.get("ai_overview_settings") or {}
            if not isinstance(patch, dict):
                patch = {}
            prompts_patch = patch.get("overview_prompts") or {}
            if not isinstance(prompts_patch, dict):
                prompts_patch = {}
            for entity_type in reset_types:
                prompts_patch[str(entity_type)] = None
            patch["overview_prompts"] = prompts_patch
            update_payload["ai_overview_settings"] = patch
        return update_payload

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

        update_payload = self._build_admin_update_payload(update_data)

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
        # serialize_pydantic_models handles all types (dict, BaseModel, list, None, etc.)
        if "settings" in db_payload:
            serialized_settings = serialize_pydantic_models(db_payload["settings"])
            db_payload["settings"] = json.dumps(serialized_settings)

        # Perform the update
        updated = await self.organization_repository.update_organization(
            organization_id=organization_id, update_data=db_payload
        )

        if "organization_memory" in update_payload or "ai_overview_settings" in update_payload:
            from apps.user_service.app.services.organization_memory_service import (
                invalidate_organization_memory_cache,
            )

            invalidate_organization_memory_cache(organization_id)

        # Format old data for audit logging before returning
        old_data = self._format_organization_for_audit(existing)

        result: dict[str, Any] = {
            "organization_id": organization_id,
            "organization_name": updated.get("name", existing.get("name")),
            "slug": updated.get("slug", existing.get("slug")),
            "old_data": old_data,
        }
        if "organization_memory" in update_payload:
            updated_settings = parse_json_field(updated.get("settings"))
            result["organization_memory"] = effective_organization_memory_enabled(updated_settings)
        if "ai_overview_settings" in update_payload:
            updated_settings = parse_json_field(updated.get("settings"))
            result["ai_overview_settings"] = (
                OrganizationService._resolve_effective_ai_overview_settings(
                    updated_settings
                ).model_dump()
            )

        return result

    async def delete_organization(self, organization_id: str) -> None:
        """Soft delete organization."""
        validate_uuid_format(organization_id, "organization_id")
        await self.organization_repository.delete_organization(organization_id)

    async def _validate_slug_unique(self, slug: str, exclude_id: str | None = None) -> None:
        """Ensure organization slug is unique."""
        is_unique = await self.organization_repository.check_slug_unique(slug, exclude_id)
        if not is_unique:
            raise ConflictException(
                message_key="organizations.errors.name_conflict",
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
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Categorize update fields into different types.

        Args:
            update_data: Update payload with only fields being updated

        Returns:
            Tuple of (
                direct_columns,
                nested_settings,
                simple_settings,
                practice_areas,
                ai_overview_settings,
            )
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
            "organization_memory",
            "website_url",
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
        ai_overview_settings_update: dict[str, Any] = {}

        # Separate fields by type
        for field, value in update_data.items():
            if field in direct_columns:
                db_payload[field] = value
            elif field == "ai_overview_settings":
                ai_overview_settings_update = value if isinstance(value, dict) else {}
            elif field in practice_area_fields:
                practice_areas_updates[field] = value
            elif field in nested_settings_fields:
                nested_settings_updates[field] = value
            elif field in simple_settings_fields:
                simple_settings_updates[field] = value

        return (
            db_payload,
            nested_settings_updates,
            simple_settings_updates,
            practice_areas_updates,
            ai_overview_settings_update,
        )

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
            ai_overview_settings_update,
        ) = self._categorize_update_fields(update_data)

        # Build settings object if any settings fields are being updated
        settings_fields_updated = (
            nested_settings_updates
            or simple_settings_updates
            or practice_areas_updates
            or ai_overview_settings_update
        )
        if settings_fields_updated:
            # Start with existing settings (or empty dict when settings/key never set)
            base_settings = existing_settings if isinstance(existing_settings, dict) else {}
            merged_settings = base_settings.copy()

            # Apply partial updates to nested JSON objects (deep merge subfields)
            self._merge_nested_settings(merged_settings, nested_settings_updates)

            if ai_overview_settings_update:
                merge_ai_overview_settings_into_settings(
                    merged_settings,
                    ai_overview_settings_update,
                )

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
    def _coerce_overview_prompts_dict(raw: Any) -> dict[str, str]:
        """Return a dict of non-empty per-entity overview prompt strings."""
        return coerce_overview_prompts_dict(raw)

    @staticmethod
    def _parse_stored_ai_overview_settings(settings: Any) -> dict[str, Any]:
        """Return the raw ``ai_overview_settings`` object from organization settings JSON."""
        return parse_stored_ai_overview_settings(settings)

    @staticmethod
    def _resolve_effective_ai_overview_settings(settings: Any) -> AiOverviewSettings:
        """Merge stored overrides with platform defaults for API responses."""
        return resolve_effective_ai_overview_settings(settings)

    @staticmethod
    def _merge_ai_overview_settings_into_settings(
        settings: dict[str, Any],
        update: dict[str, Any],
    ) -> None:
        """Apply a partial ``AiOverviewSettingsUpdate`` dict into ``settings`` in place."""
        merge_ai_overview_settings_into_settings(settings, update)

    @staticmethod
    def default_ai_overview_settings() -> AiOverviewSettings:
        """Platform defaults for reset or new-org display."""
        return platform_default_ai_overview_settings()

    @staticmethod
    def _extract_settings_fields(
        settings: dict[str, Any],
        *,
        include_ai_overview_settings: bool = True,
    ) -> dict[str, Any]:
        """Extract fields from settings dictionary.

        Args:
            settings: Settings dictionary
            include_ai_overview_settings: When false, omit prompts from list responses

        Returns:
            Dictionary with extracted fields
        """
        practice_areas = settings.get("practice_areas", {}) if isinstance(settings, dict) else {}

        extracted: dict[str, Any] = {
            "address": settings.get("address"),
            "preferred_integration": settings.get("preferred_integration"),
            "need_help_importing_data": settings.get("need_help_importing_data"),
            "need_migration_assistance": settings.get("need_migration_assistance"),
            "organization_memory": effective_organization_memory_enabled(settings),
            "compliance_security": settings.get("compliance_security"),
            "enterprise_features": settings.get("enterprise_features"),
            "team_setup": settings.get("team_setup"),
            "primary_practice_areas": practice_areas.get("primary"),
            "secondary_practice_areas": practice_areas.get("secondary"),
            "specializations": practice_areas.get("specializations"),
            "website_url": settings.get("website_url"),
        }
        if include_ai_overview_settings:
            extracted["ai_overview_settings"] = (
                OrganizationService._resolve_effective_ai_overview_settings(settings)
            )
        return extracted

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
            "website_url": existing_settings.get("website_url") if is_settings_dict else None,
            "preferred_integration": (
                existing_settings.get("preferred_integration") if is_settings_dict else None
            ),
            "need_help_importing_data": (
                existing_settings.get("need_help_importing_data") if is_settings_dict else None
            ),
            "need_migration_assistance": (
                existing_settings.get("need_migration_assistance") if is_settings_dict else None
            ),
            "organization_memory": effective_organization_memory_enabled(existing_settings),
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
            "ai_overview_settings": OrganizationService._resolve_effective_ai_overview_settings(
                existing_settings
            ).model_dump(),
        }

    @staticmethod
    def _map_to_organization_info(
        org_data: dict[str, Any],
        *,
        include_ai_overview_settings: bool = True,
    ) -> OrganizationInfo:
        """Map raw DB row to OrganizationInfo schema."""
        settings = parse_json_field(org_data.get("settings"))
        subscription_obj = OrganizationService._parse_subscription(org_data.get("subscription"))
        settings_fields = OrganizationService._extract_settings_fields(
            settings,
            include_ai_overview_settings=include_ai_overview_settings,
        )

        return OrganizationInfo(
            organization_id=str(org_data["id"]),
            name=org_data.get("name"),
            slug=org_data.get("slug"),
            domain=org_data.get("domain"),
            website_url=settings_fields.get("website_url"),
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
            organization_memory=settings_fields["organization_memory"],
            compliance_security=settings_fields["compliance_security"],
            enterprise_features=settings_fields["enterprise_features"],
            team_setup=settings_fields["team_setup"],
            primary_practice_areas=settings_fields["primary_practice_areas"],
            secondary_practice_areas=settings_fields["secondary_practice_areas"],
            specializations=settings_fields["specializations"],
            ai_overview_settings=settings_fields.get("ai_overview_settings"),
        )

    @staticmethod
    def _map_to_organization_basic_details(org_data: dict[str, Any]) -> OrganizationBasicDetails:
        """Map raw DB row to organization basic details schema."""
        settings = parse_json_field(org_data.get("settings"))
        settings_fields = OrganizationService._extract_settings_fields(settings)

        return OrganizationBasicDetails(
            id=str(org_data["id"]),
            name=org_data.get("name"),
            domain=org_data.get("domain"),
            logo_url=org_data.get("logo_url"),
            description=org_data.get("description"),
            company_size=org_data.get("company_size"),
            address=settings_fields["address"],
            primary_practice_areas=settings_fields["primary_practice_areas"],
            secondary_practice_areas=settings_fields["secondary_practice_areas"],
            subscription=OrganizationService._parse_subscription(org_data.get("subscription")),
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
            "users": 1,
        }

    def _build_settings(self, body: NewOrganizationBody) -> dict:
        """Build settings payload; fall back to derived defaults when not provided."""
        provided_settings = getattr(body.company_data, "settings", None)
        if provided_settings:
            # Convert Pydantic models to dicts if settings are provided
            settings = serialize_pydantic_models(provided_settings)
        else:
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
                "website_url": body.company_data.company_website,
            }
            settings = serialize_pydantic_models(settings)

        if not isinstance(settings, dict):
            settings = {}
        settings["organization_memory"] = True
        return settings

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
        isometrik_project_id: str | None = None
        if isometrik_details is not None:
            settings = settings.copy() if settings else {}
            settings["isometrik_application_details"] = isometrik_details
            project_id_val = isometrik_details.get("projectId")
            if project_id_val is not None:
                if not isinstance(project_id_val, str):
                    raise InternalServerErrorException(
                        message_key="organizations.errors.invalid_isometrik_project_id",
                        custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
                    )
                project_id_val = project_id_val.strip()
                if not project_id_val:
                    raise InternalServerErrorException(
                        message_key="organizations.errors.invalid_isometrik_project_id",
                        custom_code=CustomStatusCode.INTERNAL_SERVER_ERROR,
                    )
                isometrik_project_id = project_id_val

        # Convert any remaining Pydantic models to dicts before JSON serialization
        serialized_settings_dict = serialize_pydantic_models(settings) if settings else None
        serialized_subscription_dict = (
            serialize_pydantic_models(subscription) if subscription else None
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
            "isometrik_project_id": isometrik_project_id,
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
            "phone_number": (
                getattr(body.user_data, "phone_number", None) if body.user_data else None
            ),
            "phone_isd_code": (
                getattr(body.user_data, "phone_isd_code", None) if body.user_data else None
            ),
            "role": "admin",
            "timezone": getattr(body.user_data, "timezone", None) or "UTC",
            "role_id": role_id,
            "member_role": OrganizationMemberRole.OWNER.value,
            "status": OrganizationMemberStatus.ACTIVE.value,
        }

        # Create Isometrik user if enabled and credentials are provided
        if isometrik_creds:
            isometrik_user = await create_isometrik_user(
                user={
                    "user_id": member_data["user_id"],
                    "first_name": member_data["first_name"],
                    "last_name": member_data["last_name"],
                    "email": member_data["email"],
                    "organization_id": organization_id,
                    "role": DEFAULT_ORG_ROLE,
                },
                isometrik_credentials=isometrik_creds,
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
                reviewed_at=format_iso_datetime(request.get("reviewed_at")) or None,
                processed_at=format_iso_datetime(request.get("processed_at")) or None,
                approver_id=str(request["approver_id"]) if request.get("approver_id") else None,
                review_reason=request.get("review_reason"),
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

    async def _permanently_delete_organization_data(self, organization_id: str) -> list[str]:
        """Remove org-related rows and soft-delete the organization.

        Collects member emails before deletion for notification. Same cascade order
        as delete-request approval.
        """
        members = await self.organization_member_repository.get_all_members_by_organization_id(
            organization_id
        )
        member_emails = [member.get("email") for member in members if member.get("email")]

        await self.team_repository.delete_all_teams_by_organization_id(organization_id)
        await self.role_repository.delete_all_roles_by_organization_id(organization_id)
        await self.permissions_repository.delete_all_permissions_by_organization_id(organization_id)
        await self.organization_member_repository.delete_all_members_by_organization_id(
            organization_id
        )
        await self.organization_repository.delete_organization(organization_id)

        return member_emails

    @staticmethod
    def _notify_members_of_organization_deletion(
        member_emails: list[str],
        organization_name: str,
    ) -> None:
        """Send deletion-approved emails to all former members."""
        for email in member_emails:
            send_organization_deletion_approved_email(
                email=email,
                organization_name=organization_name,
            )

    async def permanently_delete_organization(self, organization_id: str) -> dict[str, Any]:
        """Permanently delete an organization and related data (no delete request).

        Same data removal and member notifications as approving a delete request.
        """
        validate_uuid_format(organization_id, "organization_id")

        organization = await self.organization_repository.get_organization_by_id(organization_id)
        if not organization:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        organization_name = organization.get("name") or ""
        member_emails = await self._permanently_delete_organization_data(organization_id)
        self._notify_members_of_organization_deletion(member_emails, organization_name)

        return {
            "organization_id": organization_id,
            "organization_name": organization_name,
        }

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
            review_reason=reason,
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
            "review_reason": updated_request.get("review_reason"),
            "reviewed_at": format_iso_datetime(updated_request.get("reviewed_at")) or "",
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
            review_reason=reason,
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
            "review_reason": updated_request.get("review_reason"),
            "reviewed_at": format_iso_datetime(updated_request.get("reviewed_at")) or "",
        }

    async def delete_organization_member(self, member_user_id: str) -> dict[str, Any]:
        """Delete an organization member.

        Business Rules:
        - Cannot delete organization owner
        - Soft deletes organization member record
        - Hard deletes member from all teams

        Args:
            member_user_id: Organization member user ID

        Returns:
            Dict with ``current_user_data`` (pre-delete profile) and ``audit_new`` for HTTP audit.

        Raises:
            NotFoundException: If member not found
            BadRequestException: If member is organization owner
        """
        profile = await self.organization_member_repository.get_user_profile_by_id(
            member_user_id, self.user_context.organization_id
        )
        if not profile:
            raise NotFoundException(
                message_key="auth.errors.user_not_member_of_organization",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # check if user is owner of the organization
        is_owner = await self.organization_repository.is_user_organization_owner(
            self.user_context.organization_id, member_user_id
        )

        if is_owner:
            raise BadRequestException(
                message_key="organizations.errors.owner_cannot_be_deleted",
                custom_code=CustomStatusCode.BAD_REQUEST,
            )

        # Soft delete organization member
        await self.organization_member_repository.delete_member_by_user_id(
            member_user_id, self.user_context.organization_id
        )

        # Hard delete from all current organization teams
        await self.team_repository.delete_user_from_all_teams(
            user_id=member_user_id, organization_id=self.user_context.organization_id
        )

        audit_new: dict[str, Any] = {
            "user_id": str(profile["user_id"]),
            "email": profile["email"],
            "first_name": profile.get("first_name"),
            "last_name": profile.get("last_name"),
            "phone_number": profile.get("phone_number"),
            "phone_isd_code": profile.get("phone_isd_code"),
            "timezone": profile.get("timezone"),
            "avatar_url": profile.get("avatar_url"),
            "status": OrganizationMemberStatus.DELETED.value,
            "role_id": str(profile.get("role_id", "")),
            "organization_id": str(profile["organization_id"]),
            "deleted_by_user_id": self.user_context.user_id,
            "deleted_by_email": self.user_context.email,
            "removed_at": datetime.now(timezone.utc).isoformat(),
        }
        if profile.get("joined_at"):
            audit_new["joined_at"] = format_iso_datetime(profile["joined_at"])
        if profile.get("last_active_at"):
            audit_new["last_active_at"] = format_iso_datetime(profile["last_active_at"])

        return {"current_user_data": profile, "audit_new": audit_new}
