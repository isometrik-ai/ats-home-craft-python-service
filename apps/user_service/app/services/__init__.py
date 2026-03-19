"""Services Package
This package contains all service classes for the user service.
Services provide business logic and orchestration of operations.
"""

from apps.user_service.app.services.auth_service import AuthService
from apps.user_service.app.services.client_service import ClientService
from apps.user_service.app.services.event_service import EventService
from apps.user_service.app.services.invite_service import InviteService
from apps.user_service.app.services.kafka_event_service import (
    KafkaEventService,
    get_kafka_event_service,
)
from apps.user_service.app.services.organization_service import (
    OrganizationService,
)
from apps.user_service.app.services.permission_service import PermissionsService
from apps.user_service.app.services.role_service import RoleService
from apps.user_service.app.services.session_service import SessionService
from apps.user_service.app.services.team_service import TeamService
from apps.user_service.app.services.user_service import UserService
from apps.user_service.app.services.verification_code_service import (
    VerificationCodeService,
)

__all__ = [
    "AuthService",
    "ClientService",
    "EventService",
    "InviteService",
    "KafkaEventService",
    "OrganizationService",
    "PermissionsService",
    "RoleService",
    "SessionService",
    "TeamService",
    "UserService",
    "VerificationCodeService",
    "get_kafka_event_service",
]
