"""Auth-related dependencies for external (non-JWT) APIs."""

from __future__ import annotations

import asyncpg
from fastapi import Depends, Request

from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.dependencies.db import db_conn
from apps.user_service.app.schemas.enums import OrganizationStatus
from apps.user_service.app.utils.common_utils import name_to_email_domain_label
from libs.shared_middleware.isometrik_external_auth import (
    IsometrikExternalContext,
    isometrik_auth_without_token_middleware,
)
from libs.shared_utils.http_exceptions import UnauthorizedException


async def resolve_external_organization_id(
    request: Request,
    organization_id: str,
    db_connection: asyncpg.Connection,
) -> str:
    """Validate ``organization_id`` and set external audit actor email on the request."""
    normalized = organization_id.strip()
    if not normalized:
        raise UnauthorizedException(message_key="errors.unauthorized")

    repo = OrganizationRepository(db_connection=db_connection)
    org = await repo.get_organization_by_id(normalized)
    if not org or org.get("status") == OrganizationStatus.DELETED.value:
        raise UnauthorizedException(message_key="errors.unauthorized")

    org_name = org.get("name") or "organization"
    request.state.external_actor_email = f"api@{name_to_email_domain_label(str(org_name))}.com"
    return normalized


async def get_organization_context(
    request: Request,
    db_connection: asyncpg.Connection = Depends(db_conn),
    ctx: IsometrikExternalContext = Depends(isometrik_auth_without_token_middleware),
) -> str:
    """Resolve org context from Isometrik decode payload `projectId`.

    Returns `organization_id` and also stores `request.state.external_actor_email`
    for downstream logic (e.g., deterministic audit actor email).
    """
    repo = OrganizationRepository(db_connection=db_connection)
    org = await repo.get_organization_context_by_isometrik_project_id(ctx.project_id)
    if not org:
        raise UnauthorizedException(message_key="errors.unauthorized")
    org_id, org_name = org
    request.state.external_actor_email = f"api@{name_to_email_domain_label(org_name)}.com"
    return org_id
