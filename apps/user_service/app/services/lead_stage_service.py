"""Service for lead stage business logic."""

import re

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.lead_stage_repository import (
    LeadStageRepository,
)
from apps.user_service.app.schemas.lead_stages import CreateLeadStageRequest
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ConflictException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class LeadStageService:
    """Service for lead stage business logic and orchestration."""

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
    ) -> None:
        """Initialize LeadStageService with user context and database connection."""
        self.user_context = user_context
        self.db_connection = db_connection
        self.lead_stage_repository = LeadStageRepository(db_connection=db_connection)

    @staticmethod
    def generate_stage_key(stage_name: str) -> str:
        """Generate stage_key from stage_name."""
        key = stage_name.lower().strip()
        key = re.sub(r"[\s\-]+", "_", key)
        key = re.sub(r"[^a-z0-9_]", "", key)
        key = re.sub(r"_+", "_", key).strip("_")
        if not key:
            raise ValidationException(
                message_key="lead_stages.errors.invalid_stage_name",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return key

    async def _resolve_sort_order_on_create(
        self,
        organization_id: str,
        requested_sort_order: int | None,
    ) -> int:
        """Resolve sort_order for create: append or insert with shift."""
        max_order = await self.lead_stage_repository.get_max_sort_order(organization_id)

        if requested_sort_order is None:
            return max_order + 1

        if not 1 <= requested_sort_order <= max_order + 1:
            raise ValidationException(
                message_key="lead_stages.errors.invalid_sort_order_range",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"min": 1, "max": max_order + 1},
            )

        await self.lead_stage_repository.shift_sort_orders_for_insert(
            organization_id=organization_id,
            target_position=requested_sort_order,
        )
        return requested_sort_order

    async def create_lead_stage(self, body: CreateLeadStageRequest) -> dict:
        """Create a lead stage with first-stage bootstrap and sort-order invariants."""
        organization_id = self.user_context.organization_id
        stage_name = body.stage_name.strip()
        stage_key = self.generate_stage_key(stage_name)

        # Defensive pre-check for cleaner conflict messaging.
        if await self.lead_stage_repository.check_stage_key_exists(organization_id, stage_key):
            raise ConflictException(
                message_key="lead_stages.errors.stage_key_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

        existing_count = await self.lead_stage_repository.count_stages(organization_id)
        is_first_stage = existing_count == 0

        if is_first_stage:
            # First-stage bootstrap rule from ADR: force both flags true and order=1.
            resolved_sort_order = 1
            is_initial = True
            is_final = True
        else:
            resolved_sort_order = await self._resolve_sort_order_on_create(
                organization_id=organization_id,
                requested_sort_order=body.sort_order,
            )
            is_initial = body.is_initial
            is_final = body.is_final

        stage_data = {
            "organization_id": organization_id,
            "stage_name": stage_name,
            "stage_key": stage_key,
            "description": body.description,
            "color": body.color.value if body.color is not None else None,
            "sort_order": resolved_sort_order,
            "is_initial": is_initial,
            "is_final": is_final,
        }

        try:
            return await self.lead_stage_repository.create_stage(stage_data)
        except UniqueViolationError as exc:
            # Race-safe fallback in case uniqueness changed after pre-checks.
            constraint = getattr(exc, "constraint_name", "") or ""
            if constraint in {"uq_lsd_stage_key"}:
                raise ConflictException(
                    message_key="lead_stages.errors.stage_key_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            if constraint in {"uq_lsd_stage_name"}:
                raise ConflictException(
                    message_key="lead_stages.errors.stage_name_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            if constraint in {"uq_lsd_sort_order"}:
                raise ConflictException(
                    message_key="lead_stages.errors.sort_order_conflict",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            raise
