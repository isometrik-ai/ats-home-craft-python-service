"""Database Repositories Package
This package contains all database repository classes for the user service.
Repositories provide a clean interface for database operations and encapsulate
SQL queries and data access logic.
Available Repositories:
    TeamRepository: Handles all team-related database operations
Usage:
    from apps.user_service.app.db.repositories import TeamRepository

    repo = TeamRepository(db_connection)
    teams = await repo.get_teams_list(organization_id)
"""

from .team_repository import TeamRepository

__all__ = ["TeamRepository"]
