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

from apps.user_service.app.db.repositories.client_repository import ClientRepository
from apps.user_service.app.db.repositories.companies_repository import (
    CompaniesRepository,
)
from apps.user_service.app.db.repositories.contact_companies_repository import (
    ContactCompaniesRepository,
)
from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.events_repository import EventsRepository
from apps.user_service.app.db.repositories.invite_repository import InviteRepository
from apps.user_service.app.db.repositories.lead_repository import LeadRepository
from apps.user_service.app.db.repositories.lead_stage_repository import (
    LeadStageRepository,
)
from apps.user_service.app.db.repositories.organization_delete_request_repository import (
    OrganizationDeleteRequestRepository,
)
from apps.user_service.app.db.repositories.organization_member_repository import (
    OrganizationMemberRepository,
)
from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.db.repositories.permission_repository import (
    PermissionsRepository,
)
from apps.user_service.app.db.repositories.project_repository import (
    ProjectRepository,
)
from apps.user_service.app.db.repositories.role_repository import RoleRepository
from apps.user_service.app.db.repositories.session_repository import (
    SessionRepository,
)
from apps.user_service.app.db.repositories.team_repository import TeamRepository
from apps.user_service.app.db.repositories.user_event_repository import (
    UserEventRepository,
)
from apps.user_service.app.db.repositories.user_repository import UserRepository
from apps.user_service.app.db.repositories.verification_code_repository import (
    VerificationCodeRepository,
)

__all__ = [
    "ClientRepository",
    "ContactsRepository",
    "CompaniesRepository",
    "ContactCompaniesRepository",
    "TeamRepository",
    "UserEventRepository",
    "PermissionsRepository",
    "ProjectRepository",
    "OrganizationRepository",
    "OrganizationMemberRepository",
    "OrganizationDeleteRequestRepository",
    "RoleRepository",
    "SessionRepository",
    "UserRepository",
    "VerificationCodeRepository",
    "InviteRepository",
    "EventsRepository",
    "LeadRepository",
    "LeadStageRepository",
]
