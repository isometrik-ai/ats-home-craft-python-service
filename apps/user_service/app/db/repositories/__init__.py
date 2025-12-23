"""Database Repositories Package
This package contains all database repository classes for the user service.
Repositories provide a clean interface for database operations and encapsulate
SQL queries and data access logic.
Available Repositories:
    TeamRepository: Handles all team-related database operations
    PermissionsRepository: Handles all permission related database operations
Usage:
    from apps.user_service.app.db.repositories import TeamRepository

    repo = TeamRepository(db_connection)
    teams = await repo.get_teams_list(organization_id)
"""

from .invite_repository import InviteRepository
from .organisation_member_repository import OrganisationMemberRepository
from .organisation_repository import OrganisationRepository
from .permission_repository import PermissionsRepository
from .role_repository import RoleRepository
from .session_repository import SessionRepository
from .team_repository import TeamRepository
from .user_repository import UserRepository
from .verification_code_repository import VerificationCodeRepository

__all__ = [
    "TeamRepository",
    "PermissionsRepository",
    "OrganisationRepository",
    "OrganisationMemberRepository",
    "RoleRepository",
    "SessionRepository",
    "UserRepository",
    "VerificationCodeRepository",
    "InviteRepository",
]
