"""Service for lead stage business logic."""

import re

import asyncpg
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories import LeadStageRepository
from apps.user_service.app.schemas.lead_stages import (
    CreateLeadStageRequest,
    LeadStageResponse,
    Unset,
    UpdateLeadStageRequest,
)
from apps.user_service.app.utils.common_utils import UserContext, format_iso_datetime
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from libs.shared_utils.status_codes import CustomStatusCode


class LeadStageService:
    """Service for lead stage business logic and orchestration."""

    @staticmethod
    def _is_initial_stage(stage_row: dict) -> bool:
        """Derive initial-stage state from sort order."""
        return stage_row["sort_order"] == 1

    @staticmethod
    def _is_final_stage(stage_row: dict, max_sort_order: int) -> bool:
        """Derive final-stage state from sort order."""
        return stage_row["sort_order"] == max_sort_order

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
        lead_stage_repository: LeadStageRepository | None = None,
    ) -> None:
        """Initialize LeadStageService with user context and database connection."""
        self.user_context = user_context
        self.db_connection = db_connection
        self.lead_stage_repository = lead_stage_repository or LeadStageRepository(
            db_connection=db_connection
        )

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

    @staticmethod
    def _reorder_intermediate_window(
        current_sort_order: int,
        target_sort_order: int,
    ) -> tuple[int, int, int] | None:
        """Inclusive bounds of rows strictly between two positions, and delta per row.

        Returns None when no rows need shifting (e.g. adjacent positions).
        """
        if current_sort_order < target_sort_order:
            low, high, delta = current_sort_order + 1, target_sort_order, -1
        else:
            low, high, delta = target_sort_order, current_sort_order - 1, 1
        if low > high:
            return None
        return low, high, delta

    async def _resolve_sort_order_on_create(
        self,
        organization_id: str,
        requested_sort_order: int | None,
        *,
        max_sort_order: int,
    ) -> int:
        """Resolve sort_order for create: append or insert with shift."""
        if requested_sort_order is None:
            return max_sort_order + 1

        if not 1 <= requested_sort_order <= max_sort_order + 1:
            raise ValidationException(
                message_key="lead_stages.errors.invalid_sort_order_range",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"min": 1, "max": max_sort_order + 1},
            )

        await self.lead_stage_repository.adjust_sort_orders(
            organization_id,
            min_sort_order=requested_sort_order,
            max_sort_order=None,
            delta=1,
        )
        return requested_sort_order

    async def create_lead_stage(self, body: CreateLeadStageRequest) -> dict:
        """Create a lead stage with first-stage bootstrap and sort-order invariants."""
        organization_id = self.user_context.organization_id
        stage_name = body.stage_name.strip()
        stage_key = self.generate_stage_key(stage_name)

        metrics = await self.lead_stage_repository.summarize_organization_for_new_stage(
            organization_id, stage_key
        )
        if metrics["stage_key_exists"]:
            raise ConflictException(
                message_key="lead_stages.errors.stage_key_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

        existing_count = metrics["total_stages"]
        is_first_stage = existing_count == 0

        if is_first_stage:
            # First stage always starts at sort_order=1.
            resolved_sort_order = 1
        else:
            resolved_sort_order = await self._resolve_sort_order_on_create(
                organization_id=organization_id,
                requested_sort_order=body.sort_order,
                max_sort_order=metrics["max_sort_order"],
            )

        stage_data = {
            "organization_id": organization_id,
            "stage_name": stage_name,
            "stage_key": stage_key,
            "description": body.description,
            "color": body.color.value if body.color is not None else None,
            "sort_order": resolved_sort_order,
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
            # Deferred uq_lsd_sort_order: violation may surface at commit, not on INSERT alone.
            if constraint in {"uq_lsd_sort_order"}:
                raise ConflictException(
                    message_key="lead_stages.errors.sort_order_conflict",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            raise

    @staticmethod
    def _build_stage_response(stage_row: dict, *, max_sort_order: int) -> LeadStageResponse:
        """Map repository row to API response schema."""
        return LeadStageResponse(
            id=str(stage_row["id"]),
            stage_name=stage_row["stage_name"],
            stage_key=stage_row["stage_key"],
            description=stage_row.get("description"),
            color=stage_row.get("color"),
            sort_order=stage_row["sort_order"],
            is_initial=LeadStageService._is_initial_stage(stage_row),
            is_final=LeadStageService._is_final_stage(stage_row, max_sort_order),
            created_at=format_iso_datetime(stage_row.get("created_at")),
            updated_at=format_iso_datetime(stage_row.get("updated_at")),
        )

    async def list_lead_stages(self) -> tuple[list[dict], int]:
        """List all lead stages for the current organization."""
        organization_id = self.user_context.organization_id
        stage_rows = await self.lead_stage_repository.list_stages_by_organization(organization_id)
        if not stage_rows:
            return [], 0
        max_sort_order = max(row["sort_order"] for row in stage_rows)
        items = [
            self._build_stage_response(row, max_sort_order=max_sort_order).model_dump(mode="json")
            for row in stage_rows
        ]
        return items, len(items)

    async def get_lead_stage(self, stage_id: str) -> dict:
        """Get a single lead stage by id for the current organization."""
        organization_id = self.user_context.organization_id
        stage_row = await self.lead_stage_repository.get_stage_by_id_with_max_sort_order(
            organization_id=organization_id,
            stage_id=stage_id,
        )
        if not stage_row:
            raise NotFoundException(
                message_key="lead_stages.errors.stage_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        max_sort_order = stage_row["max_sort_order"]
        return self._build_stage_response(stage_row, max_sort_order=max_sort_order).model_dump(
            mode="json"
        )

    def _run_update_validations(self, ctx: dict, *, body: UpdateLeadStageRequest) -> None:
        """Validate update against pre-fetched context (no DB calls)."""
        if body.stage_name is not None and ctx["key_conflict_count"] > 0:
            raise ConflictException(
                message_key="lead_stages.errors.stage_key_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

        if body.sort_order is not None and not 1 <= body.sort_order <= ctx["total_stages"]:
            raise ValidationException(
                message_key="lead_stages.errors.invalid_sort_order_range",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"min": 1, "max": ctx["total_stages"]},
            )

    def _build_update_data(
        self,
        body: UpdateLeadStageRequest,
        *,
        generated_stage_key: str | None = None,
        sort_order_to_apply: int | None = None,
    ) -> dict[str, object]:
        """Build DB update payload based on PATCH semantics."""
        update_data: dict[str, object] = {}

        if body.stage_name is not None:
            normalized_stage_name = body.stage_name.strip()
            update_data["stage_name"] = normalized_stage_name
            update_data["stage_key"] = (
                generated_stage_key
                if generated_stage_key is not None
                else self.generate_stage_key(normalized_stage_name)
            )

        if not isinstance(body.description, Unset):
            update_data["description"] = body.description

        if not isinstance(body.color, Unset):
            update_data["color"] = body.color.value if body.color else None

        if sort_order_to_apply is not None:
            update_data["sort_order"] = sort_order_to_apply

        return update_data

    async def _persist_stage_update(
        self,
        organization_id: str,
        stage_id: str,
        update_data: dict[str, object],
    ) -> dict | None:
        """Persist stage updates and return the updated row."""
        try:
            return await self.lead_stage_repository.update_stage(
                organization_id=organization_id,
                stage_id=stage_id,
                update_data=update_data,
            )
        except UniqueViolationError as exc:
            constraint = getattr(exc, "constraint_name", "") or ""
            if constraint == "uq_lsd_stage_key":
                raise ConflictException(
                    message_key="lead_stages.errors.stage_key_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            if constraint == "uq_lsd_stage_name":
                raise ConflictException(
                    message_key="lead_stages.errors.stage_name_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            raise

    async def update_lead_stage(self, stage_id: str, body: UpdateLeadStageRequest) -> dict:
        """Update lead stage fields and/or reorder stage."""
        organization_id = self.user_context.organization_id

        generated_stage_key: str | None = None
        if body.stage_name is not None:
            generated_stage_key = self.generate_stage_key(body.stage_name.strip())

        stage_with_ctx = await self.lead_stage_repository.get_stage_by_id_with_organization_metrics(
            organization_id=organization_id,
            stage_id=stage_id,
            proposed_stage_key=generated_stage_key,
        )
        if not stage_with_ctx:
            raise NotFoundException(
                message_key="lead_stages.errors.stage_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        self._run_update_validations(stage_with_ctx, body=body)

        sort_order_to_apply: int | None = None
        if body.sort_order is not None and body.sort_order != stage_with_ctx["sort_order"]:
            sort_order_to_apply = body.sort_order
            # Intermediate sort_order values may duplicate until txn commit
            # (deferred uq_lsd_sort_order).
            window = self._reorder_intermediate_window(
                stage_with_ctx["sort_order"],
                body.sort_order,
            )
            if window is not None:
                low, high, delta = window
                await self.lead_stage_repository.adjust_sort_orders(
                    organization_id,
                    min_sort_order=low,
                    max_sort_order=high,
                    delta=delta,
                )

        update_data = self._build_update_data(
            body,
            generated_stage_key=generated_stage_key,
            sort_order_to_apply=sort_order_to_apply,
        )
        if not update_data:
            stage_rows = await self.lead_stage_repository.list_stages_by_organization(
                organization_id
            )
            max_sort_order = max(row["sort_order"] for row in stage_rows)
            return self._build_stage_response(
                stage_with_ctx, max_sort_order=max_sort_order
            ).model_dump(mode="json")

        updated_stage = await self._persist_stage_update(
            organization_id=organization_id,
            stage_id=stage_id,
            update_data=update_data,
        )

        if not updated_stage:
            raise NotFoundException(
                message_key="lead_stages.errors.stage_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        stage_rows = await self.lead_stage_repository.list_stages_by_organization(organization_id)
        max_sort_order = max(row["sort_order"] for row in stage_rows)
        return self._build_stage_response(updated_stage, max_sort_order=max_sort_order).model_dump(
            mode="json"
        )

    async def delete_lead_stage(self, stage_id: str) -> dict:
        """Delete a lead stage and shift down sort_order for stages above the gap (same txn)."""
        organization_id = self.user_context.organization_id

        deleted_row = await self.lead_stage_repository.delete_stage(
            organization_id=organization_id,
            stage_id=stage_id,
        )
        if not deleted_row:
            raise NotFoundException(
                message_key="lead_stages.errors.stage_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        deleted_sort = deleted_row["sort_order"]
        await self.lead_stage_repository.adjust_sort_orders(
            organization_id,
            min_sort_order=deleted_sort + 1,
            max_sort_order=None,
            delta=-1,
        )

        remaining_stage_rows = await self.lead_stage_repository.list_stages_by_organization(
            organization_id
        )
        max_sort_order = (
            deleted_sort
            if not remaining_stage_rows
            else max(row["sort_order"] for row in remaining_stage_rows)
        )
        return self._build_stage_response(deleted_row, max_sort_order=max_sort_order).model_dump(
            mode="json"
        )
