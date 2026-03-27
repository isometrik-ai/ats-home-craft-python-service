"""Auth-related dependencies for external (non-JWT) APIs."""

from __future__ import annotations

import asyncpg
from fastapi import Depends

from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.dependencies.db import db_conn
from libs.shared_middleware.isometrik_external_auth import (
    IsometrikExternalContext,
    isometrik_auth_without_token_middleware,
)
from libs.shared_utils.http_exceptions import UnauthorizedException


async def external_organization_id(
    db_connection: asyncpg.Connection = Depends(db_conn),
    ctx: IsometrikExternalContext = Depends(isometrik_auth_without_token_middleware),
) -> str:
    """Resolve internal organization_id from Isometrik decode payload `projectId`."""
    repo = OrganizationRepository(db_connection=db_connection)
    org_id = await repo.get_organization_id_by_isometrik_project_id(ctx.project_id)
    if not org_id:
        raise UnauthorizedException(message_key="errors.unauthorized")
    return org_id
