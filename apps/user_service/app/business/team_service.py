"""Service for team business logic"""

import asyncpg

from apps.user_service.app.db.repositories.team_repository import TeamRepository
from apps.user_service.app.schemas.teams import (
    CreateTeamRequest,
    TeamDetailItem,
    TeamDetailResponse,
    TeamItem,
    TeamMemberItem,
    TeamsListResponse,
    UpdateTeamRequest,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    validate_uuid_format,
)
from libs.shared_utils.http_exceptions import (
    BadRequestException,
    DuplicateValueException,
    NotFoundException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class TeamService:
    """Service for team business logic.

    User context is provided during initialization.
    """

    def __init__(
        self,
        user_context: UserContext,
        db_connection: asyncpg.Connection,
    ) -> None:
        """Initialize TeamService with user context.

        Args:
            user_context: Authenticated user context
            db_connection: database connection for postgresql
        """
        self.user_context = user_context
        # Initialize database operations class once
        self.team_repository = TeamRepository(db_connection=db_connection)

    @staticmethod
    def _compute_member_changes(
        current_member_ids: set[str],
        new_member_ids: set[str],
    ) -> tuple[list[str], list[str]]:
        """Compute members to add and remove.

        Args:
            current_member_ids: Set of current member user IDs
            new_member_ids: Set of new member user IDs

        Returns:
            Tuple containing (members_to_add, members_to_remove)
        """
        members_to_add = list(new_member_ids - current_member_ids)
        members_to_remove = list(current_member_ids - new_member_ids)
        return members_to_add, members_to_remove

    async def create_team(
        self,
        request: CreateTeamRequest,
    ) -> None:
        """Create a new team with validation and member assignment.

        Args:
            request: Team creation request

        Raises:
            HTTPException: For validation or creation failures
        """
        # Validate team name uniqueness
        await self._validate_team_name(
            request.name,
        )

        # Validate and deduplicate member IDs
        member_ids = []
        if request.member_ids is not None:
            unique_member_ids = set(request.member_ids)
            await self._validate_member_ids(
                unique_member_ids,
            )
            member_ids = list(unique_member_ids)

        # Create team
        await self.team_repository.create_team(
            organization_id=self.user_context.organization_id,
            name=request.name,
            description=request.description,
            created_by=self.user_context.user_id,
            member_ids=member_ids,
        )

    async def list_teams(
        self,
        page: int,
        page_size: int,
        search: str | None = None,
    ) -> TeamsListResponse:
        """Retrieve paginated list of teams.

        Args:
            page: Page number for pagination (1-indexed)
            page_size: Number of teams per page
            search: Optional search term for team name filtering

        Returns:
            TeamsListResponse: Paginated team list

        Raises:
            HTTPException: 400 if user doesn't belong to an organization
        """

        teams_data, total_count = await self.team_repository.get_teams_list(
            organization_id=self.user_context.organization_id,
            search=search,
            page=page,
            page_size=page_size,
        )

        team_items = [
            TeamItem(
                id=str(team["id"]),
                name=team["name"],
                description=team.get("description"),
                member_count=team.get("member_count", 0),
                created_at=format_iso_datetime(team.get("created_at")) or "",
                updated_at=format_iso_datetime(team.get("updated_at")) or "",
            )
            for team in teams_data
        ]

        return TeamsListResponse(
            data=team_items,
            total_count=total_count,
            page=page,
            page_size=page_size,
        )

    async def get_team_detail(
        self,
        team_id: str,
    ) -> TeamDetailResponse:
        """Retrieve detailed information of a specific team.

        Args:
            team_id: UUID of the team

        Returns:
            TeamDetailResponse: Detailed team info including members

        Raises:
            HTTPException: 404 if team not found
        """

        team_data, members = await self.team_repository.get_team_detail(
            team_id=team_id,
            organization_id=self.user_context.organization_id,
        )

        if not team_data:
            raise NotFoundException(
                message_key="teams.errors.team_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        member_items = [
            TeamMemberItem(
                user_id=str(m["id"]),
                name=m.get("name"),
                email=m.get("email"),
                role=m.get("role"),
                added_at=format_iso_datetime(m.get("added_at")),
            )
            for m in members
        ]

        team_detail_item = TeamDetailItem(
            id=str(team_data["id"]),
            name=team_data["name"],
            description=team_data.get("description"),
            members=member_items,
            created_at=format_iso_datetime(team_data.get("created_at")),
            updated_at=format_iso_datetime(team_data.get("updated_at")),
        )

        return TeamDetailResponse(data=team_detail_item)

    async def update_team(
        self,
        team_id: str,
        request: UpdateTeamRequest,
    ) -> None:
        """Update a team with validation and member changes.

        Args:
            team_id: UUID of the team
            request: Update request with optional fields

        Raises:
            HTTPException: For validation or update failures
        """
        # Validate team ID
        validate_uuid_format(team_id, "team ID")

        # Get current member IDs
        current_member_ids = set(
            await self.team_repository.get_team_member_ids(
                team_id,
                self.user_context.organization_id,
            )
        )

        # Compute member changes
        members_to_add: list[str] = []
        members_to_remove: list[str] = []
        if request.member_ids is not None:
            new_member_ids = set(request.member_ids)
            await self._validate_member_ids(
                new_member_ids,
            )
            members_to_add, members_to_remove = self._compute_member_changes(
                current_member_ids,
                new_member_ids,
            )

        # Validate team name if provided
        if request.name:
            await self._validate_team_name(request.name, team_id=team_id)

        # Update team
        await self.team_repository.update_team(
            team_id=team_id,
            organization_id=self.user_context.organization_id,
            name=request.name,
            description=request.description,
            members_to_add=members_to_add,
            members_to_remove=members_to_remove,
            added_by=self.user_context.user_id,
        )

    async def delete_team(
        self,
        team_id: str,
    ) -> None:
        """Soft delete a team and hard delete its members.

        Args:
            team_id: UUID of the team

        Raises:
            HTTPException: 404 if team not found
        """

        await self.team_repository.delete_team_and_members(
            team_id=team_id,
            organization_id=self.user_context.organization_id,
        )

    async def _validate_team_name(
        self,
        new_name: str,
        team_id: str | None = None,
    ):
        """Validate team name uniqueness.

        Args:
            new_name: Team name to validate
            team_id: Team Id
        Raises:
            HTTPException: 409 if team name already exists
        """
        is_unique = await self.team_repository.check_team_name_unique(
            new_name, self.user_context.organization_id, team_id
        )
        if not is_unique:
            raise DuplicateValueException(
                message_key="teams.errors.team_name_exists",
                custom_code=CustomStatusCode.DUPLICATE_ENTRY,
            )

    async def _validate_member_ids(
        self,
        member_ids: set[str],
    ) -> None:
        """Validate member IDs belong to organization.

        Args:
            member_ids: Set of user IDs to validate

        Raises:
            HTTPException: 400 if any member ID is invalid or doesn't belong to organization
        """
        for user_id in member_ids:
            validate_uuid_format(user_id, "user ID")

        if member_ids:
            valid = await self.team_repository.validate_organization_members(
                list(member_ids),
                self.user_context.organization_id,
            )
            if not valid:
                raise BadRequestException(
                    message_key="teams.errors.invalid_member_ids",
                    custom_code=CustomStatusCode.INVALID_DATA,
                )
