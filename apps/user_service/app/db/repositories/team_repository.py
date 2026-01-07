"""Team Database Repository Module - AsyncPG Implementation

This module contains all team-related database operations using asyncpg.
All SQL queries for team management are centralized here with proper
transaction handling and efficient batch operations.
"""

import asyncpg

from apps.user_service.app.schemas.teams import (
    TeamDbDelete,
    TeamDbIn,
    TeamDbUpdate,
    TeamRoles,
)
from libs.shared_utils.http_exceptions import NotFoundException
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("team_repository")


class TeamRepository:
    """Database operations class for team management using asyncpg.

    Provides efficient, transaction-safe operations with proper error handling.
    """

    def __init__(self, db_connection: asyncpg.Connection) -> None:
        """Initialize with asyncpg connection.

        Args:
            db_connection: Active asyncpg connection (potentially in transaction)
        """
        self.db_connection = db_connection

    # CREATE OPERATIONS
    async def create_team(self, team_input: TeamDbIn) -> str:
        """Create a new team with optional members in a single transaction.

        This function creates a team record and optionally adds members to it.
        All operations are atomic within the provided transaction.

        Args:
            team_input: Validated team creation data

        Returns:
            str: The created team ID
        """
        # Insert team record
        team_query = """
            INSERT INTO teams (
                organization_id, name, description, created_by,
                created_at, updated_at, deleted_at
            )
            VALUES ($1, $2, $3, $4, NOW(), NOW(), NULL)
            RETURNING id
        """

        team_record = await self.db_connection.fetchrow(
            team_query,
            team_input.organization_id,
            team_input.name,
            team_input.description,
            team_input.created_by,
        )

        team_id = team_record["id"]

        # Add members if provided
        if team_input.member_ids:
            await self._insert_team_members(
                team_id=team_id, member_ids=team_input.member_ids, added_by=team_input.created_by
            )

            logger.info(
                "Successfully added %s members to team - Team ID: %s",
                len(team_input.member_ids),
                team_id,
            )

        return team_id

    async def _insert_team_members(
        self,
        team_id: str,
        member_ids: list[str],
        added_by: str,
    ) -> None:
        """Add members to a team in a single query.

        This function adds members to a team using a list of member user IDs.
        It handles duplicate entries gracefully.

        Args:
            team_id: Team UUID to add members to
            member_ids: List of user IDs to add as members
            added_by: User ID who is adding the members
        """
        if not member_ids:
            return

        # Perform a single batch insert using UNNEST
        count = len(member_ids)

        insert_query = """
            INSERT INTO team_members (team_id, user_id, role, added_by, added_at)
            SELECT
                t.team_id,
                t.user_id,
                t.role,
                t.added_by,
                NOW()
            FROM UNNEST(
                $1::uuid[],
                $2::uuid[],
                $3::text[],
                $4::uuid[]
            ) AS t(team_id, user_id, role, added_by)
            ON CONFLICT (team_id, user_id) DO NOTHING;
        """

        await self.db_connection.execute(
            insert_query,
            [team_id] * count,
            member_ids,
            [TeamRoles.MEMBER] * count,
            [added_by] * count,
        )

    # READ OPERATIONS
    def _build_team_filters(
        self,
        organization_id: str,
        search: str | None = None,
    ) -> tuple[str, list[object]]:
        """Build WHERE clause and parameters for team queries.

        Args:
            organization_id: Organization UUID to filter by
            search: Optional search term for team name

        Returns:
            Tuple containing (where_clause, params) for use in SQL query
        """
        conditions = ["t.organization_id = $1", "t.deleted_at IS NULL"]
        params = [organization_id]

        if search and search.strip():
            params.append(f"%{search.strip()}%")
            conditions.append(f"t.name ILIKE ${len(params)}")

        where_clause = "WHERE " + " AND ".join(conditions)
        return where_clause, params

    async def _get_team_member_count(self, team_id: str) -> int:
        """Return the total number of members in a team.

        Args:
            team_id: Team UUID.

        Returns:
            int: Number of members in the team.
        """
        query = "SELECT COUNT(*) FROM team_members WHERE team_id = $1"
        count = await self.db_connection.fetchval(query, team_id)
        return count

    async def get_teams_list(
        self,
        organization_id: str,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[dict[str, object]], int]:
        """Retrieve paginated list of teams with member counts.

        Args:
            organization_id: Organization UUID
            search: Optional search term to filter teams by name
            page: Page number (1-indexed)
            page_size: Number of teams per page

        Returns:
            Tuple[list[dict], int]: (list of teams, total count of filtered teams)
        """
        offset = (page - 1) * page_size
        where_clause, params = self._build_team_filters(organization_id, search)

        # Count query
        count_query = f"""
            SELECT COUNT(*)::int
            FROM teams t
            {where_clause}
        """
        total_count = await self.db_connection.fetchval(count_query, *params)

        # List query with pagination
        list_query = f"""
            SELECT
                t.id,
                t.name,
                t.description,
                t.created_at,
                t.updated_at,
                COALESCE(tm.member_count, 0)::int AS member_count
            FROM teams t
            LEFT JOIN (
                SELECT team_id, COUNT(*)::int AS member_count
                FROM team_members
                GROUP BY team_id
            ) tm ON t.id = tm.team_id
            {where_clause}
            ORDER BY t.created_at DESC
            LIMIT ${len(params) + 1}
            OFFSET ${len(params) + 2}
        """

        list_params = params + [page_size, offset]
        rows = await self.db_connection.fetch(list_query, *list_params)

        teams = [dict(row) for row in rows]
        return teams, total_count

    def _extract_team_data(self, row: dict[str, any]) -> dict[str, object]:
        """Extract team data from database row."""
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _extract_member_data(self, row: dict[str, object]) -> dict[str, object]:
        """Extract member data from database row."""
        first_name = row.get("first_name") or ""
        last_name = row.get("last_name") or ""
        full_name = f"{first_name} {last_name}".strip() or None

        return {
            "id": row["user_id"],
            "name": full_name,
            "email": row["email"],
            "role": row["role"],
            "added_at": row["added_at"],
        }

    async def get_team_detail(
        self, team_id: str, organization_id: str
    ) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
        """Retrieve a team and its members using two clean and maintainable queries.

        Args:
            team_id: UUID of the team.
            organization_id: UUID of the organization.

        Returns:
            A tuple containing:
                - team: A dictionary with team data, or None if not found.
                - members: A list of dictionaries representing team members.
        """

        team_query = """
            SELECT id, name, description, created_at, updated_at
            FROM teams
            WHERE id = $1
            AND organization_id = $2
            AND deleted_at IS NULL
            LIMIT 1;
        """

        team_row = await self.db_connection.fetchrow(team_query, team_id, organization_id)
        if not team_row:
            return None, []

        team = self._extract_team_data(team_row)

        member_query = """
            SELECT
                tm.user_id,
                tm.role,
                tm.added_at,
                om.first_name,
                om.last_name,
                om.email
            FROM team_members tm
            LEFT JOIN organization_members om
                ON tm.user_id = om.user_id
                AND om.organization_id = $2
                AND om.status != 'deleted'
            WHERE tm.team_id = $1
            ORDER BY tm.added_at ASC;
        """

        member_rows = await self.db_connection.fetch(member_query, team_id, organization_id)
        members = [self._extract_member_data(row) for row in member_rows]

        return team, members

    async def get_team_member_ids(
        self,
        team_id: str,
        organization_id: str,
    ) -> list[str]:
        """Fetch all member IDs of a team within an organization.
        Uses JOIN to ensure team belongs to organization.

        Args:
            team_id: UUID of the team
            organization_id: Organization UUID (for scoping)

        Returns:
            List[str]: List of member user IDs
        """
        query = """
            SELECT tm.user_id
            FROM team_members tm
            INNER JOIN teams t ON tm.team_id = t.id
            WHERE tm.team_id = $1
              AND t.organization_id = $2
              AND t.deleted_at IS NULL
        """

        rows = await self.db_connection.fetch(query, team_id, organization_id)
        return [str(row["user_id"]) for row in rows]

    # VALIDATION OPERATIONS
    async def check_team_name_unique(
        self, name: str, organization_id: str, team_id: str | None = None
    ) -> bool:
        """Check if team name is unique within organization (case-insensitive).

        Args:
            name: Team name to check
            organization_id: Organization UUID
            team_id: Optional team ID to exclude from check (during update)

        Returns:
            bool: True if name is unique, False otherwise
        """
        exclude_clause = ""
        params = [name, organization_id]

        if team_id:
            exclude_clause = "AND id != $3"
            params.append(team_id)

        query = f"""
            SELECT EXISTS(
                SELECT 1 FROM teams
                WHERE LOWER(name) = LOWER($1)
                AND organization_id = $2
                AND deleted_at IS NULL
                {exclude_clause}
            ) AS exists
        """.format(exclude_clause=exclude_clause)

        exists = await self.db_connection.fetchval(query, *params)
        return not exists

    async def validate_organization_members(
        self,
        user_ids: list[str],
        organization_id: str,
    ) -> bool:
        """Validate that all user IDs belong to the organization.
        Uses efficient COUNT comparison.

        Args:
            user_ids: List of user IDs to validate
            organization_id: Organization UUID

        Returns:
            bool: True if all users are valid, False otherwise
        """
        if not user_ids:
            return True

        unique_user_ids = list(set(user_ids))

        query = """
            SELECT COUNT(DISTINCT user_id)::int as count
            FROM organization_members
            WHERE organization_id = $1
              AND user_id = ANY($2::uuid[])
        """

        count = await self.db_connection.fetchval(query, organization_id, unique_user_ids)
        return count == len(unique_user_ids)

    # UPDATE OPERATIONS
    async def update_team(self, team_input: TeamDbUpdate) -> None:
        """Update team fields and members in a single transaction.

        Args:
            team_input: Validated team update data

        Raises:
            HTTPException: 404 if team not found
        """
        # Build dynamic UPDATE query
        params = []

        fields_to_update = {}
        if team_input.name is not None:
            fields_to_update["name"] = team_input.name
        if team_input.description is not None:
            fields_to_update["description"] = team_input.description

        # Check if we need to update the team (either fields or members)
        needs_update = bool(
            fields_to_update or team_input.members_to_add or team_input.members_to_remove
        )

        # Execute team update if there are field changes
        if needs_update:
            set_clauses = []
            params = []

            # Add regular fields to update
            for field, value in fields_to_update.items():
                set_clauses.append(f"{field} = ${len(params) + 1}")
                params.append(value)

            # Always update updated_at if there are any changes
            set_clauses.append("updated_at = NOW()")

            # Add team_id and organization_id to params
            params.extend([team_input.team_id, team_input.organization_id])

            # Build the final SET clause
            set_clause = ", ".join(set_clauses)

            update_query = f"""
                UPDATE teams
                SET {set_clause}
                WHERE id = ${len(params) - 1}
                AND organization_id = ${len(params)}
                AND deleted_at IS NULL
                RETURNING id
            """

            result = await self.db_connection.fetchrow(update_query, *params)
            if not result:
                raise NotFoundException(
                    message_key="teams.errors.team_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )

        # Add new members
        if team_input.members_to_add:
            await self._insert_team_members(
                team_id=team_input.team_id,
                member_ids=team_input.members_to_add,
                added_by=team_input.added_by,
            )

        # Remove members
        if team_input.members_to_remove:
            delete_query = """
                DELETE FROM team_members
                WHERE team_id = $1
                  AND user_id = ANY($2::uuid[])
            """
            await self.db_connection.execute(
                delete_query, team_input.team_id, team_input.members_to_remove
            )

    # DELETE OPERATIONS
    async def delete_team_and_members(self, team_input: TeamDbDelete) -> None:
        """Hard-delete members and soft-delete team in a single transaction.

        Args:
            team_input: Validated team deletion data
        """
        # Soft delete team
        soft_delete_query = """
            UPDATE teams
            SET deleted_at = NOW(), updated_at = NOW()
            WHERE id = $1
                AND organization_id = $2
                AND deleted_at IS NULL
            RETURNING id
        """

        updated_team = await self.db_connection.fetchrow(
            soft_delete_query, team_input.team_id, team_input.organization_id
        )

        # If team does not exist or is already deleted
        if not updated_team:
            raise NotFoundException(
                message_key="teams.errors.team_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        # Hard delete members
        delete_members_query = """
            DELETE FROM team_members
            WHERE team_id = $1
        """
        await self.db_connection.execute(delete_members_query, team_input.team_id)
        logger.info("Soft-deleted team %s successfully", team_input.team_id)

    async def delete_all_teams_by_organization_id(self, organization_id: str) -> int:
        """Delete all teams and their members for an organization.
        team member deletion happens automatically by db constraint

        Args:
            organization_id: Organization ID

        Returns:
            int: Number of teams deleted
        """
        # Then delete all teams
        delete_teams_query = """
            DELETE FROM teams
            WHERE organization_id = $1
        """
        result = await self.db_connection.execute(delete_teams_query, organization_id)
        return int(result.split()[-1]) if result else 0
