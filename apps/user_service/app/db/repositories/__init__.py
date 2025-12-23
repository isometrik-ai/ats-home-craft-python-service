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
from apps.user_service.app.db.repositories.invite_repository import InviteRepository
from apps.user_service.app.db.repositories.organisation_member_repository import (
    OrganisationMemberRepository,
)
from apps.user_service.app.db.repositories.organisation_repository import (
    OrganisationRepository,
)
from apps.user_service.app.db.repositories.permission_repository import (
    PermissionsRepository,
)
from apps.user_service.app.db.repositories.role_repository import RoleRepository
from apps.user_service.app.db.repositories.session_repository import (
    SessionRepository,
)
from apps.user_service.app.db.repositories.team_repository import TeamRepository
from apps.user_service.app.db.repositories.user_repository import UserRepository
from apps.user_service.app.db.repositories.verification_code_repository import (
    VerificationCodeRepository,
)

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
