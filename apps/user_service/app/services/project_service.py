"""Service for project business logic

This service handles all business logic related to projects, including
validation, formatting, and orchestration of project operations.
"""

from decimal import Decimal
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories import (
    ClientRepository,
    ProjectRepository,
    TeamRepository,
)
from apps.user_service.app.schemas.clients import PrimaryContactInfo
from apps.user_service.app.schemas.enums import TeamRoles
from apps.user_service.app.schemas.projects import (
    BillingInfo,
    ClientInfo,
    CreateProjectRequest,
    ProjectDetailData,
    ProjectListItem,
    ProjectListQueryParams,
    TeamMemberInput,
    TechStack,
    UpdateProjectRequest,
)
from apps.user_service.app.schemas.teams import MemberData, TeamDbDelete, TeamDbIn
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_field,
)
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    ConflictException,
    NotFoundException,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("project_service")


class ProjectService:
    """Service for project business logic.

    Handles all business logic related to projects, including validation,
    formatting, and orchestration of project operations.
    """

    def __init__(
        self,
        user_context: UserContext,
        db_connection: asyncpg.Connection,
    ) -> None:
        """Initialize ProjectService with user context and database connection.

        Args:
            user_context: Authenticated user context
            db_connection: database connection for postgresql
        """
        self.user_context = user_context
        self.db_connection = db_connection
        self.project_repository = ProjectRepository(db_connection=db_connection)
        self.team_repository = TeamRepository(db_connection=db_connection)
        self.client_repository = ClientRepository(db_connection=db_connection)

    @staticmethod
    def _generate_project_id(project_title: str) -> str:
        """Generate a URL-friendly project ID from project title.

        Args:
            project_title: Project title

        Returns:
            str: Generated project ID (e.g., "ecommerce-platform-redesign")
        """
        clean_title = project_title.lower().strip()
        normalized = "".join(c if c.isalnum() or c in ("-", "_") else "-" for c in clean_title)
        compact = "-".join(filter(None, normalized.split("-")))
        # Ensure it's not empty
        if not compact:
            compact = "project"
        return compact

    async def _generate_unique_project_id(
        self,
        project_title: str,
        exclude_id: str | None = None,
    ) -> str:
        """Generate a project_id from project title.

        Args:
            project_title: Project title
            exclude_id: Optional project UUID to exclude from check (for updates)
        Returns:
            str: Generated project ID

        Raises:
            ConflictException: If project_id already exists in the organization
        """
        project_id = self._generate_project_id(project_title)
        is_unique = await self.project_repository.check_project_id_unique(
            project_id, self.user_context.organization_id, exclude_id
        )
        if not is_unique:
            raise ConflictException(
                message_key="projects.errors.project_title_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

        return project_id

    async def _validate_create_project_request(self, request_data: CreateProjectRequest) -> None:
        """Validate client exists and project title is unique.

        Args:
            request_data: Project creation request data

        Raises:
            NotFoundException: If client not found
        """
        organization_id = self.user_context.organization_id

        # Validate client exists
        client_exists = await self.client_repository.get_client_details_with_primary_contact(
            request_data.client_id, organization_id
        )
        if not client_exists:
            raise NotFoundException(
                message_key="projects.errors.client_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

    async def _validate_and_create_team(self, request_data: CreateProjectRequest) -> str | None:
        """Validate team members and create team if provided.

        Args:
            request_data: Project creation request data

        Returns:
            Team ID if team was created, None otherwise

        Raises:
            NotFoundException: If team member not found
        """
        if not request_data.team_members:
            return None

        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id

        # Validate team members exist
        member_ids = [m.member_id for m in request_data.team_members]
        members_valid = await self.team_repository.validate_organization_members(
            member_ids, organization_id
        )
        if not members_valid:
            raise NotFoundException(
                message_key="projects.errors.team_member_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Prepare member_data with additional_data for team creation
        member_data = []
        for member in request_data.team_members:
            additional_data = {
                "role": member.role,
                "allocation_percentage": member.allocation_percentage,
                "hourly_rate": float(member.hourly_rate) if member.hourly_rate else None,
                "role_description": member.role_description,
            }
            member_data.append(
                MemberData(member_id=member.member_id, additional_data=additional_data)
            )

        # Create team with project title as name and members with additional_data
        team_db_in = TeamDbIn(
            organization_id=organization_id,
            name=request_data.project_title,
            description=f"Team for project: {request_data.project_title}",
            created_by=user_id,
            member_data=member_data,
        )
        team_id = await self.team_repository.create_team(team_db_in)
        return team_id

    def _prepare_billing_info_dict(self, billing_info: BillingInfo | None) -> dict[str, Any] | None:
        """Prepare billing info dictionary from request.

        Args:
            billing_info: Billing info from request

        Returns:
            Billing info dictionary or None
        """
        if not billing_info:
            return None

        billing_dict: dict[str, Any] = {
            "billing_type": billing_info.billing_type.value,
        }
        if billing_info.hourly_rate is not None:
            billing_dict["hourly_rate"] = float(billing_info.hourly_rate)
        if billing_info.currency:
            billing_dict["currency"] = billing_info.currency
        if billing_info.payment_terms:
            billing_dict["payment_terms"] = billing_info.payment_terms.value
        if billing_info.budget:
            billing_dict["budget"] = {
                "total": float(billing_info.budget.total),
            }
        return billing_dict

    def _prepare_tech_stack_dict(self, tech_stack: TechStack | None) -> dict[str, list[str]]:
        """Prepare tech stack dictionary from request.

        Args:
            tech_stack: Tech stack from request

        Returns:
            Tech stack dictionary with default empty lists
        """
        if not tech_stack:
            return {
                "frontend": [],
                "backend": [],
                "database": [],
                "cloud": [],
                "mobile": [],
                "ai_ml": [],
                "other": [],
            }

        return {
            "frontend": tech_stack.frontend or [],
            "backend": tech_stack.backend or [],
            "database": tech_stack.database or [],
            "cloud": tech_stack.cloud or [],
            "mobile": tech_stack.mobile or [],
            "ai_ml": tech_stack.ai_ml or [],
            "other": tech_stack.other or [],
        }

    def _prepare_project_data(
        self,
        request_data: CreateProjectRequest,
        project_id: str,
        team_id: str | None,
    ) -> dict[str, Any]:
        """Prepare project data dictionary from request.

        Args:
            request_data: Project creation request data
            project_id: Generated project ID
            team_id: Optional team ID

        Returns:
            Project data dictionary ready for database insertion
        """
        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id

        project_data = self._initialize_project_base_data(
            request_data=request_data,
            project_id=project_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        self._apply_optional_project_fields(project_data, request_data)
        self._apply_project_flags(project_data, request_data)
        self._apply_structured_project_fields(project_data, request_data, team_id)
        self._apply_primary_relationships(project_data, request_data)
        return project_data

    def _initialize_project_base_data(
        self,
        request_data: CreateProjectRequest,
        project_id: str,
        organization_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """Build base project data with mandatory attributes."""
        return {
            "organization_id": organization_id,
            "project_id": project_id,
            "project_title": request_data.project_title,
            "client_id": request_data.client_id,
            "status": request_data.status.value,
            "priority": request_data.priority.value,
            "created_by": user_id,
            "updated_by": user_id,
        }

    def _apply_optional_project_fields(
        self,
        project_data: dict[str, Any],
        request_data: CreateProjectRequest,
    ) -> None:
        """Populate optional scalar fields when provided."""
        optional_fields = (
            "project_description",
            "project_category",
            "practice_areas",
            "start_date",
            "target_end_date",
            "project_goals",
            "success_criteria",
            "additional_ai_context",
            "tags",
        )

        for field_name in optional_fields:
            value = getattr(request_data, field_name)
            if value:
                project_data[field_name] = value

    def _apply_project_flags(
        self,
        project_data: dict[str, Any],
        request_data: CreateProjectRequest,
    ) -> None:
        """Attach optional boolean flags when explicitly set."""
        if request_data.is_billable is not None:
            project_data["is_billable"] = request_data.is_billable
        if request_data.is_internal is not None:
            project_data["is_internal"] = request_data.is_internal

    def _apply_structured_project_fields(
        self,
        project_data: dict[str, Any],
        request_data: CreateProjectRequest,
        team_id: str | None,
    ) -> None:
        """Attach nested and collection based fields."""
        billing_info_dict = self._prepare_billing_info_dict(request_data.billing_info)
        if billing_info_dict:
            project_data["billing_info"] = billing_info_dict

        project_data["tech_stack"] = self._prepare_tech_stack_dict(request_data.tech_stack)

        if request_data.custom_fields:
            project_data["custom_fields"] = request_data.custom_fields

        if team_id:
            project_data["team_id"] = team_id

    def _apply_primary_relationships(
        self,
        project_data: dict[str, Any],
        request_data: CreateProjectRequest,
    ) -> None:
        """Set primary repo metadata when available."""
        if request_data.repositories:
            primary_repo = next(
                (repo for repo in request_data.repositories if repo.is_primary), None
            )
            if primary_repo:
                project_data["primary_repo_url"] = primary_repo.repository_url

    async def _create_project_repositories(
        self,
        project_uuid: str,
        request_data: CreateProjectRequest,
    ) -> None:
        """Create project repositories if provided.

        Args:
            project_uuid: Project UUID
            request_data: Project creation request data
        """
        if not request_data.repositories:
            return

        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id

        repositories_data = []
        for repo in request_data.repositories:
            repositories_data.append(
                {
                    "platform": repo.platform.value,
                    "repository_name": repo.repository_name,
                    "repository_owner": repo.repository_owner,
                    "repository_url": repo.repository_url,
                    "purpose": repo.purpose,
                    "primary_branch": repo.primary_branch,
                    "is_private": repo.is_private,
                    "is_primary": repo.is_primary,
                }
            )

        await self.project_repository.create_project_repositories(
            project_id=project_uuid,
            organization_id=organization_id,
            repositories=repositories_data,
            created_by=user_id,
        )

    async def _create_project_integrations(
        self,
        project_uuid: str,
        request_data: CreateProjectRequest,
    ) -> None:
        """Create project integrations if provided.

        Args:
            project_uuid: Project UUID
            request_data: Project creation request data
        """
        if not request_data.integrations:
            return

        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id

        integrations_data = []
        for integration in request_data.integrations:
            integration_dict: dict[str, Any] = {
                "integration_type": integration.integration_type.value,
                "sync_enabled": integration.sync_enabled,
                "sync_direction": integration.sync_direction.value,
                "auto_sync": integration.auto_sync,
                "sync_interval_minutes": integration.sync_interval_minutes,
            }
            if integration.integration_name:
                integration_dict["integration_name"] = integration.integration_name
            if integration.external_project_id:
                integration_dict["external_project_id"] = integration.external_project_id
            if integration.external_project_key:
                integration_dict["external_project_key"] = integration.external_project_key
            if integration.external_workspace_id:
                integration_dict["external_workspace_id"] = integration.external_workspace_id
            if integration.external_board_id:
                integration_dict["external_board_id"] = integration.external_board_id
            if integration.integration_purpose:
                integration_dict["integration_purpose"] = integration.integration_purpose
            if integration.integration_config:
                integration_dict["integration_config"] = integration.integration_config

            integrations_data.append(integration_dict)

        await self.project_repository.create_project_integrations(
            project_id=project_uuid,
            organization_id=organization_id,
            integrations=integrations_data,
            connected_by=user_id,
        )

    async def create_project(self, request_data: CreateProjectRequest) -> None:
        """Create a new project with complete flow.

        Flow:
        - Validate client exists
        - Validate team members exist
        - Create team (if team_members provided)
        - Create team members with additional_data
        - Generate project_id
        - Create project record
        - Create repositories (if provided)
        - Create integrations (if provided)

        Args:
            request_data: Project creation request data

        Raises:
            NotFoundException: If client or team member not found
            BadRequestException: If validation fails
            ConflictException: If project title already exists
        """
        # Validate request
        await self._validate_create_project_request(request_data)

        # Generate unique project_id from project title
        project_id = await self._generate_unique_project_id(request_data.project_title)

        # Validate and create team if team_members provided
        team_id = await self._validate_and_create_team(request_data)

        # Prepare project data
        project_data = self._prepare_project_data(request_data, project_id, team_id)

        # Create project
        project_record = await self.project_repository.create_project(project_data)
        project_uuid = str(project_record["id"])

        # Create repositories if provided
        await self._create_project_repositories(project_uuid, request_data)

        # Create integrations if provided
        await self._create_project_integrations(project_uuid, request_data)

    def _build_project_update_dict(
        self, request_data: UpdateProjectRequest, updated_by: str
    ) -> dict[str, Any]:
        """Build partial project update dict from request (only non-None scalar fields)."""
        data: dict[str, Any] = {"updated_by": updated_by}
        scalar_fields = (
            "project_title",
            "project_description",
            "status",
            "priority",
            "project_category",
            "practice_areas",
            "start_date",
            "target_end_date",
            "project_goals",
            "success_criteria",
            "additional_ai_context",
            "tags",
            "custom_fields",
            "is_billable",
            "is_internal",
        )
        for field in scalar_fields:
            value = getattr(request_data, field, None)
            if value is not None:
                if hasattr(value, "value"):  # Enum
                    data[field] = value.value
                else:
                    data[field] = value
        if request_data.billing_info is not None:
            data["billing_info"] = self._prepare_billing_info_dict(request_data.billing_info)
        if request_data.tech_stack is not None:
            data["tech_stack"] = self._prepare_tech_stack_dict(request_data.tech_stack)
        return data

    @staticmethod
    def _format_project_for_audit(project_data: dict[str, Any]) -> dict[str, Any]:
        """Format project data for audit logging.

        Extracts and formats project fields into a structure suitable for audit log comparison.
        """
        return {
            "project_id": str(project_data.get("id")),
            "project_title": project_data.get("project_title"),
            "project_description": project_data.get("project_description"),
            "status": project_data.get("status"),
            "priority": project_data.get("priority"),
            "project_category": project_data.get("project_category"),
            "practice_areas": project_data.get("practice_areas"),
            "start_date": format_iso_datetime(project_data.get("start_date")),
            "target_end_date": format_iso_datetime(project_data.get("target_end_date")),
            "billing_info": parse_json_field(project_data.get("billing_info")),
            "tech_stack": parse_json_field(project_data.get("tech_stack")),
            "project_goals": project_data.get("project_goals"),
            "success_criteria": project_data.get("success_criteria"),
            "additional_ai_context": project_data.get("additional_ai_context"),
            "tags": project_data.get("tags"),
            "custom_fields": parse_json_field(project_data.get("custom_fields")),
            "is_billable": project_data.get("is_billable"),
            "is_internal": project_data.get("is_internal"),
        }

    @staticmethod
    def _partial_update_payload(
        dumped: dict[str, Any],
        exclude_keys: frozenset[str] | None = None,
    ) -> dict[str, Any]:
        """Build DB update payload from model_dump: exclude keys and serialize enums."""
        exclude = exclude_keys or frozenset()
        return {
            k: (v.value if hasattr(v, "value") else v)
            for k, v in dumped.items()
            if k not in exclude
        }

    @staticmethod
    def _member_input_to_member_data(member: TeamMemberInput) -> MemberData:
        """Build MemberData from a single TeamMemberInput."""
        return MemberData(
            member_id=member.member_id,
            additional_data={
                "role": member.role,
                "allocation_percentage": member.allocation_percentage,
                "hourly_rate": float(member.hourly_rate)
                if member.hourly_rate is not None
                else None,
                "role_description": member.role_description,
            },
        )

    async def _ensure_project_team(
        self,
        project: dict[str, Any],
        request_data: UpdateProjectRequest,
    ) -> tuple[str | None, str | None]:
        """If project has no team and request is team_members.add, create team with that member.
        Returns (team_id, new_team_id_for_project or None). Single operation only.
        """
        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id
        team_id = str(project["team_id"]) if project.get("team_id") else None
        new_team_id: str | None = None
        team_members = request_data.team_members

        if not team_members or team_id or not team_members.add:
            return team_id, new_team_id

        valid = await self.team_repository.validate_organization_members(
            [team_members.add.member_id], organization_id
        )
        if not valid:
            raise NotFoundException(
                message_key="projects.errors.team_member_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        project_title = project.get("project_title") or "Project"
        team_db_in = TeamDbIn(
            organization_id=organization_id,
            name=project_title,
            description=f"Team for project: {project_title}",
            created_by=user_id,
            member_data=[self._member_input_to_member_data(team_members.add)],
        )
        new_team_id = await self.team_repository.create_team(team_db_in)
        return new_team_id, new_team_id

    async def _apply_team_members_changes(
        self,
        team_id: str,
        request_data: UpdateProjectRequest,
        skip_add: bool,
    ) -> None:
        """Apply exactly one team member operation: remove, update, or add."""
        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id
        team_member = request_data.team_members
        if not team_member:
            return

        if team_member.remove:
            await self.team_repository.delete_team_members_by_user_ids(
                team_id, [team_member.remove]
            )
        elif team_member.update:
            await self.team_repository.update_team_members_additional_data(
                team_id,
                organization_id,
                [
                    {
                        "user_id": team_member.update.id,
                        "role": team_member.update.role,
                        "allocation_percentage": team_member.update.allocation_percentage,
                        "hourly_rate": (
                            float(team_member.update.hourly_rate)
                            if team_member.update.hourly_rate is not None
                            else None
                        ),
                        "role_description": team_member.update.role_description,
                    }
                ],
            )
        elif team_member.add and not skip_add:
            await self.team_repository._insert_team_members(
                team_id=team_id,
                member_data=[self._member_input_to_member_data(team_member.add)],
                added_by=user_id,
            )

    async def _ensure_single_primary_repository(
        self,
        project_uuid: str,
        organization_id: str,
        *,
        exclude_id: str | None = None,
    ) -> None:
        """Enforce at most one primary repo:
        clear is_primary on current primary (if different from exclude_id)."""
        current_primary = await self.project_repository.get_project_repositories(
            project_uuid, organization_id, primary_only=True
        )
        if current_primary and current_primary[0].get("id") != exclude_id:
            await self.project_repository.update_project_repository(
                project_uuid, organization_id, str(current_primary[0]["id"]), {"is_primary": False}
            )

    async def _apply_repositories_changes(
        self,
        project_uuid: str,
        organization_id: str,
        user_id: str,
        request_data: UpdateProjectRequest,
    ) -> None:
        """Apply single remove, update, or add for repositories. Enforces at most one primary."""
        repos_req = request_data.repositories
        if not repos_req:
            return
        if repos_req.remove:
            await self.project_repository.delete_project_repositories_by_ids(
                project_uuid, organization_id, [repos_req.remove]
            )
        elif repos_req.update:
            item = repos_req.update
            data = self._partial_update_payload(
                item.model_dump(exclude_none=True), exclude_keys=frozenset({"id"})
            )
            if data.get("is_primary") is True:
                await self._ensure_single_primary_repository(
                    project_uuid, organization_id, exclude_id=item.id
                )
            await self.project_repository.update_project_repository(
                project_uuid, organization_id, item.id, data
            )
        elif repos_req.add:
            add_item = repos_req.add
            if add_item.is_primary:
                await self._ensure_single_primary_repository(
                    project_uuid, organization_id, exclude_id=None
                )
            repo_row = self._partial_update_payload(add_item.model_dump())
            await self.project_repository.create_project_repositories(
                project_id=project_uuid,
                organization_id=organization_id,
                repositories=[repo_row],
                created_by=user_id,
            )

    async def _apply_integrations_changes(
        self,
        project_uuid: str,
        organization_id: str,
        user_id: str,
        request_data: UpdateProjectRequest,
    ) -> None:
        """Apply single remove, update, or add for integrations."""
        integrations_req = request_data.integrations
        if not integrations_req:
            return
        if integrations_req.remove:
            await self.project_repository.delete_project_integrations_by_ids(
                project_uuid, organization_id, [integrations_req.remove]
            )
        elif integrations_req.update:
            item = integrations_req.update
            data = self._partial_update_payload(
                item.model_dump(exclude_none=True), exclude_keys=frozenset({"id"})
            )
            await self.project_repository.update_project_integration(
                project_uuid, organization_id, item.id, data
            )
        elif integrations_req.add:
            add_item = integrations_req.add
            integration_row = self._partial_update_payload(add_item.model_dump())
            await self.project_repository.create_project_integrations(
                project_id=project_uuid,
                organization_id=organization_id,
                integrations=[integration_row],
                connected_by=user_id,
            )

    async def _apply_project_row_update(
        self,
        project_uuid: str,
        organization_id: str,
        request_data: UpdateProjectRequest,
        new_team_id: str | None,
    ) -> None:
        """Build project update dict; set primary_repo if needed."""
        user_id = self.user_context.user_id
        project_data = self._build_project_update_dict(request_data, user_id)
        if new_team_id is not None:
            project_data["team_id"] = new_team_id
        if request_data.repositories:
            # Optimize: only fetch if we can't determine primary from request data
            primary_repo_url = None

            # Check if we can determine primary repo from request
            repos_req = request_data.repositories
            if repos_req.add and repos_req.add.is_primary:
                primary_repo_url = repos_req.add.repository_url
            elif (
                repos_req.update and repos_req.update.is_primary and repos_req.update.repository_url
            ):
                primary_repo_url = repos_req.update.repository_url

            # Fetch only if we couldn't determine from request
            if primary_repo_url is None:
                repos = await self.project_repository.get_project_repositories(
                    project_uuid, organization_id, primary_only=True
                )
                primary_repo = repos[0] if repos else None
                primary_repo_url = primary_repo["repository_url"] if primary_repo else None

            project_data["primary_repo_url"] = primary_repo_url
        if project_data:
            await self.project_repository.update_project(
                project_uuid, organization_id, project_data
            )

    async def update_project(
        self, project_id: str, request_data: UpdateProjectRequest
    ) -> dict[str, Any] | None:
        """Update a project. Only provided fields are updated. List fields use add/update/remove.

        Invariant: at most one primary repository per project.
        When a repo is set to primary, others are cleared first.

        Order: remove (team_members, repos, integrations), then update, then add, then project row.
        All operations run in the same transaction (caller must use db_uow).

        Returns:
            dict | None: Result with old_data for audit when update is applied, None when no-op

        Raises:
            NotFoundException: Project, repository, or integration not found
            BadRequestException: Team would have zero members or project has no team when needed
        """
        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id

        project = await self.project_repository.get_project_with_client(project_id, organization_id)
        if not project:
            raise NotFoundException(
                message_key="projects.errors.project_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        project_uuid = str(project["id"])
        team_id, new_team_id = await self._ensure_project_team(project, request_data)

        team_members_req = request_data.team_members
        has_team_member_change = (
            team_members_req
            and (team_members_req.update or team_members_req.remove)
            and not team_id
        )
        if has_team_member_change:
            raise BadRequestException(
                message_key="projects.errors.project_has_no_team",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        if team_id:
            await self._apply_team_members_changes(
                team_id, request_data, skip_add=(new_team_id is not None)
            )
        await self._apply_repositories_changes(project_uuid, organization_id, user_id, request_data)
        await self._apply_integrations_changes(project_uuid, organization_id, user_id, request_data)
        await self._apply_project_row_update(
            project_uuid, organization_id, request_data, new_team_id
        )
        old_data = self._format_project_for_audit(project)
        return {"old_data": old_data}

    async def delete_project(self, project_id: str) -> None:
        """Delete a project: hard delete related entities, soft delete project.

        Hard deletes: team, team members, repositories, integrations.
        Soft deletes: project (sets status='archived').

        All operations run in a single transaction (caller must use db_uow).

        Args:
            project_id: Project UUID or human-readable ID

        Raises:
            NotFoundException: If project not found or already archived
        """
        organization_id = self.user_context.organization_id
        user_id = self.user_context.user_id

        project = await self.project_repository.get_project_basic_information(
            project_id=project_id,
            organization_id=organization_id,
        )
        if not project:
            raise NotFoundException(
                message_key="projects.errors.project_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        project_uuid = str(project["id"])
        team_id = project["team_id"]

        if team_id:
            await self.team_repository.delete_team_and_members(
                TeamDbDelete(
                    team_id=str(team_id),
                    organization_id=organization_id,
                )
            )
        await self.project_repository.delete_all_project_repositories(project_uuid, organization_id)
        await self.project_repository.delete_all_project_integrations(project_uuid, organization_id)
        await self.project_repository.soft_delete_project(
            project_uuid,
            organization_id,
            updated_by=user_id,
        )

    async def list_projects(
        self, filters: ProjectListQueryParams
    ) -> tuple[list[ProjectListItem], int]:
        """Retrieve paginated list of projects.

        Args:
            filters: Filters for filtering and pagination

        Returns:
            Tuple containing (list of projects, total count)
        """
        projects_data, total_count = await self.project_repository.get_projects_list(
            organization_id=self.user_context.organization_id,
            filters=filters,
        )

        project_items = []
        for project in projects_data:
            # Parse tech_stack JSONB and normalize for list response
            tech_stack_data = parse_json_field(project.get("tech_stack"))
            tech_stack_dict = tech_stack_data if isinstance(tech_stack_data, dict) else {}
            tech_stack = TechStack(
                frontend=tech_stack_dict.get("frontend") or [],
                backend=tech_stack_dict.get("backend") or [],
                database=tech_stack_dict.get("database") or [],
                cloud=tech_stack_dict.get("cloud") or [],
                mobile=tech_stack_dict.get("mobile") or [],
                ai_ml=tech_stack_dict.get("ai_ml") or [],
                other=tech_stack_dict.get("other") or [],
            )

            project_items.append(
                ProjectListItem(
                    id=str(project["id"]),
                    project_id=project["project_id"],
                    project_title=project["project_title"],
                    client={
                        "id": str(project["client_id"]),
                        "name": project.get("client_name") or "",
                        "type": project.get("client_type") or "",
                    },
                    project_lead={
                        "id": str(project["project_lead_id"]),
                        "full_name": project.get("project_lead_name") or "",
                    }
                    if project.get("project_lead_id")
                    else None,
                    team_size=project.get("team_size", 0),
                    status=project["status"],
                    priority=project["priority"],
                    category=(
                        project.get("project_category")[0]
                        if project.get("project_category")
                        else None
                    ),
                    practice_areas=project.get("practice_areas", []) or [],
                    start_date=format_iso_datetime(project.get("start_date")),
                    tags=project.get("tags", []) or [],
                    tech_stack=tech_stack,
                )
            )

        return project_items, total_count

    @staticmethod
    def _build_team_info(
        team_data: dict | None,
        member_rows: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Build team info dict from team data and member rows."""
        if not team_data:
            return None
        project_lead: dict[str, Any] | None = None
        tech_lead: dict[str, Any] | None = None
        members: list[dict[str, Any]] = []

        for member_dict in member_rows:
            additional_data = parse_json_field(member_dict.get("additional_data"))
            role = additional_data.get("role") or member_dict.get("role") or ""
            first = member_dict.get("first_name", "") or ""
            last = member_dict.get("last_name", "") or ""
            member_info = {
                "id": str(member_dict["user_id"]),
                "full_name": f"{first} {last}".strip() or "",
                "email": member_dict.get("email") or "",
                "role": role,
                "allocation_percentage": additional_data.get("allocation_percentage", 0),
                "hourly_rate": str(additional_data.get("hourly_rate", "0.00")),
                "role_description": additional_data.get("role_description"),
            }
            if role == TeamRoles.PROJECT_LEAD.value:
                project_lead = member_info
            elif role == TeamRoles.TECH_LEAD.value:
                tech_lead = member_info
            else:
                members.append(member_info)

        return {
            "id": str(team_data["id"]),
            "name": team_data["name"],
            "project_lead": project_lead,
            "tech_lead": tech_lead,
            "members": members,
        }

    @staticmethod
    def _map_repository_to_detail(repository_data: dict[str, Any]) -> dict[str, Any]:
        """Map repository row to detail response format."""
        return {
            "id": str(repository_data["id"]),
            "platform": repository_data["platform"],
            "external_repository_id": repository_data.get("external_repository_id"),
            "repository_owner": repository_data.get("repository_owner"),
            "repository_name": repository_data["repository_name"],
            "repository_url": repository_data["repository_url"],
            "purpose": repository_data.get("purpose"),
            "primary_branch": repository_data.get("primary_branch", "main"),
            "is_private": repository_data.get("is_private", True),
            "is_primary": repository_data.get("is_primary", False),
            "is_connected": repository_data.get("is_connected", False),
            "connection_status": repository_data.get("connection_status"),
            "webhook_url": repository_data.get("webhook_url"),
            "webhook_secret": repository_data.get("webhook_secret"),
            "webhook_events": repository_data.get("webhook_events"),
            "last_synced_at": format_iso_datetime(repository_data.get("last_synced_at")),
            "total_commits": repository_data.get("total_commits", 0),
            "total_branches": repository_data.get("total_branches", 0),
            "total_contributors": repository_data.get("total_contributors", 0),
            "description": repository_data.get("description"),
            "created_at": format_iso_datetime(repository_data.get("created_at")) or "",
            "updated_at": format_iso_datetime(repository_data.get("updated_at")) or "",
        }

    @staticmethod
    def _map_integration_to_detail(i: dict[str, Any]) -> dict[str, Any]:
        """Map integration row to detail response format."""
        return {
            "id": str(i["id"]),
            "integration_type": i["integration_type"],
            "integration_name": i.get("integration_name"),
            "is_connected": i.get("is_connected", False),
            "connection_status": i.get("connection_status"),
            "external_project_id": i.get("external_project_id"),
            "external_project_key": i.get("external_project_key"),
            "external_workspace_id": i.get("external_workspace_id"),
            "external_board_id": i.get("external_board_id"),
            "nango_connection_id": i.get("nango_connection_id"),
            "webhook_url": i.get("webhook_url"),
            "webhook_events": i.get("webhook_events"),
            "outgoing_webhook_url": i.get("outgoing_webhook_url"),
            "sync_enabled": i.get("sync_enabled", True),
            "sync_direction": i.get("sync_direction", "bidirectional"),
            "auto_sync": i.get("auto_sync", True),
            "sync_interval_minutes": i.get("sync_interval_minutes", 15),
            "last_synced_at": format_iso_datetime(i.get("last_synced_at")),
            "last_sync_status": i.get("last_sync_status"),
            "last_sync_error": i.get("last_sync_error"),
            "next_sync_at": format_iso_datetime(i.get("next_sync_at")),
            "integration_purpose": i.get("integration_purpose"),
            "created_at": format_iso_datetime(i.get("created_at")) or "",
            "updated_at": format_iso_datetime(i.get("updated_at")) or "",
        }

    @staticmethod
    def _build_billing_info(billing_raw: Any) -> dict[str, Any] | None:
        """Build billing info dict from raw JSONB value."""
        data = parse_json_field(billing_raw)
        if not isinstance(data, dict):
            return None
        hourly = data.get("hourly_rate")
        retainer = data.get("retainer_amount")
        return (
            {
                "billing_type": data.get("billing_type"),
                "hourly_rate": Decimal(str(hourly)) if hourly else None,
                "currency": data.get("currency"),
                "billing_cycle": data.get("billing_cycle"),
                "billing_contact_id": data.get("billing_contact_id"),
                "payment_terms": data.get("payment_terms"),
                "retainer_amount": Decimal(str(retainer)) if retainer else None,
                "budget": data.get("budget"),
            }
            if data
            else None
        )

    async def get_project_details(self, project_id: str) -> ProjectDetailData:
        """Get complete project details.

        Args:
            project_id: Project UUID or human-readable ID

        Returns:
            ProjectDetailData: Complete project details

        Raises:
            NotFoundException: If project not found
        """
        # Single JOIN query for project + client (replaces 2 separate calls)
        project = await self.project_repository.get_project_with_client(
            project_id, self.user_context.organization_id
        )
        if not project:
            raise NotFoundException(
                message_key="projects.errors.project_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        project_uuid = str(project["id"])
        org_id = self.user_context.organization_id

        # Fetch team (when team_id exists), repositories, integrations
        team_data: dict | None = None
        member_rows: list[dict[str, Any]] = []
        if project.get("team_id"):
            team_data, member_rows = await self.team_repository.get_team_detail(
                str(project["team_id"]), org_id
            )

        repositories = await self.project_repository.get_project_repositories(project_uuid, org_id)
        integrations = await self.project_repository.get_project_integrations(project_uuid, org_id)

        team_info = self._build_team_info(team_data, member_rows)
        project_lead_info = None
        if team_info and team_info.get("project_lead"):
            project_lead_data = team_info["project_lead"]
            project_lead_info = {
                "id": project_lead_data["id"],
                "full_name": project_lead_data.get("full_name") or "",
            }

        tech_raw = parse_json_field(project.get("tech_stack"))
        tech_stack_data = tech_raw if isinstance(tech_raw, dict) else {}
        custom_fields_data = parse_json_field(project.get("custom_fields"))
        custom_fields = custom_fields_data if isinstance(custom_fields_data, dict) else {}

        return ProjectDetailData(
            id=project_uuid,
            organization_id=str(project["organization_id"]),
            project_id=project["project_id"],
            project_title=project["project_title"],
            project_description=project.get("project_description"),
            client=ClientInfo(
                id=str(project["client_uuid"]),
                name=project.get("client_name") or "",
                type=project.get("client_type") or "",
                primary_contact=PrimaryContactInfo(
                    first_name=project.get("client_first_name"),
                    last_name=project.get("client_last_name"),
                    title=project.get("client_title"),
                    email=project.get("client_email"),
                    phone_isd_code=project.get("client_phone_isd_code"),
                    phone=project.get("client_phone_number"),
                ),
            ),
            project_lead=project_lead_info,
            status=project["status"],
            priority=project["priority"],
            project_category=project.get("project_category", []) or [],
            practice_areas=project.get("practice_areas", []) or [],
            start_date=project.get("start_date"),
            target_end_date=project.get("target_end_date"),
            actual_end_date=project.get("actual_end_date"),
            billing_info=self._build_billing_info(project.get("billing_info")),
            total_billed=str(project.get("total_billed", "0.00")),
            total_hours=str(project.get("total_hours", "0.00")),
            tech_stack={
                "frontend": tech_stack_data.get("frontend", []),
                "backend": tech_stack_data.get("backend", []),
                "database": tech_stack_data.get("database", []),
                "cloud": tech_stack_data.get("cloud", []),
                "mobile": tech_stack_data.get("mobile", []),
                "ai_ml": tech_stack_data.get("ai_ml", []),
                "other": tech_stack_data.get("other", []),
            },
            project_goals=project.get("project_goals"),
            success_criteria=project.get("success_criteria"),
            additional_ai_context=project.get("additional_ai_context"),
            primary_pm_tool=project.get("primary_pm_tool"),
            primary_repo_url=project.get("primary_repo_url"),
            tags=project.get("tags", []) or [],
            custom_fields=custom_fields,
            is_billable=project.get("is_billable", True),
            is_internal=project.get("is_internal", False),
            team=team_info,
            repositories=[self._map_repository_to_detail(r) for r in repositories],
            integrations=[self._map_integration_to_detail(i) for i in integrations],
            created_at=format_iso_datetime(project.get("created_at")) or "",
            updated_at=format_iso_datetime(project.get("updated_at")) or "",
            created_by=str(project["created_by"]) if project.get("created_by") else None,
            updated_by=str(project["updated_by"]) if project.get("updated_by") else None,
        )
