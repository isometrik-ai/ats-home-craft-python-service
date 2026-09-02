"""Resident notice feed business logic."""

from __future__ import annotations

from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.notices_repository import NoticesRepository
from apps.user_service.app.schemas.enums import NOTICE_CATEGORY_LABELS, NoticeScopeType
from apps.user_service.app.schemas.notices import (
    NoticeAttachmentResponse,
    ResidentBannerQuery,
    ResidentNoticeDetailResponse,
    ResidentNoticeListItemResponse,
    ResidentNoticeListQuery,
)
from apps.user_service.app.services.notice_recipient_resolution_service import (
    NoticeRecipientResolutionService,
)
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import ForbiddenException, NotFoundException
from libs.shared_utils.status_codes import CustomStatusCode


class NoticesResidentService:
    """Resident-facing notice feed and interactions."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = NoticesRepository(db_connection=db_connection)
        self.recipient_service = NoticeRecipientResolutionService(db_connection=db_connection)

    @property
    def organization_id(self) -> str:
        """Organization id from context."""
        return self.user_context.organization_id

    async def list_notices(
        self,
        *,
        contact_id: str | None,
        contact_user_id: str | None,
        query: ResidentNoticeListQuery,
    ) -> tuple[list[ResidentNoticeListItemResponse], int]:
        """Return paginated resident feed."""
        visible_ids = await self._visible_notice_ids(
            project_id=query.project_id,
            contact_id=contact_id,
            contact_user_id=contact_user_id,
        )
        offset = (query.page - 1) * query.page_size
        rows, total = await self.repo.list_live_notices_for_resident(
            organization_id=self.organization_id,
            project_id=query.project_id,
            notice_ids=visible_ids,
            category=query.category.value if query.category else None,
            search=query.search,
            limit=query.page_size,
            offset=offset,
        )
        items = [
            await self._to_list_item(row, contact_id=contact_id, contact_user_id=contact_user_id)
            for row in rows
        ]
        return items, total

    async def get_banner(
        self,
        *,
        contact_id: str | None,
        contact_user_id: str | None,
        query: ResidentBannerQuery,
    ) -> list[ResidentNoticeListItemResponse]:
        """Return visible pinned notices for resident banner."""
        visible_ids = await self._visible_notice_ids(
            project_id=query.project_id,
            contact_id=contact_id,
            contact_user_id=contact_user_id,
        )
        visible_set = set(visible_ids)
        pins = await self.repo.list_active_pins_with_notices(
            organization_id=self.organization_id,
            project_id=query.project_id,
        )
        items: list[ResidentNoticeListItemResponse] = []
        for pin in pins:
            notice_id = str(pin["notice_id"])
            if notice_id not in visible_set:
                continue
            row = await self.repo.fetch_notice_for_resident(
                organization_id=self.organization_id,
                notice_id=notice_id,
            )
            if row is None:
                continue
            items.append(
                await self._to_list_item(
                    row, contact_id=contact_id, contact_user_id=contact_user_id
                )
            )
        return items

    async def get_notice(
        self,
        *,
        contact_id: str | None,
        contact_user_id: str | None,
        notice_id: str,
        increment_view: bool = True,
    ) -> ResidentNoticeDetailResponse:
        """Return notice detail if visible to resident."""
        context = await self.repo.fetch_notice_visibility_context(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        if context is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        visible = await self.recipient_service.is_visible_to_contact(
            organization_id=self.organization_id,
            project_id=str(context["project_id"]),
            notice=context,
            contact_id=contact_id,
            contact_user_id=contact_user_id,
        )
        if not visible:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        if increment_view:
            await self.repo.increment_view_count(
                organization_id=self.organization_id,
                notice_id=notice_id,
            )

        row = await self.repo.fetch_notice_for_resident(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        if row is None:
            raise NotFoundException(
                message_key="notices.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return await self._to_detail(row, contact_id=contact_id, contact_user_id=contact_user_id)

    async def like_notice(
        self,
        *,
        contact_id: str | None,
        contact_user_id: str | None,
        notice_id: str,
    ) -> ResidentNoticeDetailResponse:
        """Like a visible notice."""
        like_kwargs = self._like_identity_kwargs(contact_id, contact_user_id)
        await self.get_notice(
            contact_id=contact_id,
            contact_user_id=contact_user_id,
            notice_id=notice_id,
            increment_view=False,
        )
        await self.repo.upsert_like(
            organization_id=self.organization_id,
            notice_id=notice_id,
            **like_kwargs,
        )
        return await self.get_notice(
            contact_id=contact_id,
            contact_user_id=contact_user_id,
            notice_id=notice_id,
            increment_view=False,
        )

    async def unlike_notice(
        self,
        *,
        contact_id: str | None,
        contact_user_id: str | None,
        notice_id: str,
    ) -> ResidentNoticeDetailResponse:
        """Remove like from a visible notice."""
        like_kwargs = self._like_identity_kwargs(contact_id, contact_user_id)
        await self.get_notice(
            contact_id=contact_id,
            contact_user_id=contact_user_id,
            notice_id=notice_id,
            increment_view=False,
        )
        await self.repo.delete_like(
            organization_id=self.organization_id,
            notice_id=notice_id,
            **like_kwargs,
        )
        return await self.get_notice(
            contact_id=contact_id,
            contact_user_id=contact_user_id,
            notice_id=notice_id,
            increment_view=False,
        )

    async def _visible_notice_ids(
        self,
        *,
        project_id: str,
        contact_id: str | None,
        contact_user_id: str | None,
    ) -> list[str]:
        """Return notice IDs visible to the resident contact."""
        notice_ids = await self.repo.list_live_notice_ids_for_project(
            organization_id=self.organization_id,
            project_id=project_id,
        )
        contexts = await self.recipient_service.load_notice_contexts(
            organization_id=self.organization_id,
            notice_ids=notice_ids,
        )
        return await self.recipient_service.filter_visible_notice_ids(
            organization_id=self.organization_id,
            project_id=project_id,
            notice_contexts=contexts,
            contact_id=contact_id,
            contact_user_id=contact_user_id,
        )

    @staticmethod
    def _like_identity_kwargs(
        contact_id: str | None,
        contact_user_id: str | None,
    ) -> dict[str, str]:
        """Prefer contact likes for residents; staff/security use user_id."""
        if contact_id:
            return {"contact_id": contact_id}
        if contact_user_id:
            return {"user_id": contact_user_id}
        raise ForbiddenException(
            message_key="notices.errors.likes_require_contact",
            custom_code=CustomStatusCode.FORBIDDEN,
        )

    async def _liked_by_me(
        self,
        *,
        notice_id: str,
        contact_id: str | None,
        contact_user_id: str | None,
    ) -> bool:
        """Return whether the resident has liked the notice."""
        if contact_id:
            return await self.repo.contact_has_liked(
                organization_id=self.organization_id,
                notice_id=notice_id,
                contact_id=contact_id,
            )
        if contact_user_id:
            return await self.repo.contact_has_liked(
                organization_id=self.organization_id,
                notice_id=notice_id,
                user_id=contact_user_id,
            )
        return False

    async def _to_list_item(
        self,
        row: dict[str, Any],
        *,
        contact_id: str | None,
        contact_user_id: str | None,
    ) -> ResidentNoticeListItemResponse:
        """Map a notice row to a resident list item."""
        notice_id = str(row["id"])
        attachments = await self.repo.list_attachments(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        category = str(row["category"])
        liked = await self._liked_by_me(
            notice_id=notice_id,
            contact_id=contact_id,
            contact_user_id=contact_user_id,
        )
        row.get("scope_label")
        if str(row.get("scope_type")) == NoticeScopeType.WHOLE_SOCIETY.value:
            pass
        return ResidentNoticeListItemResponse(
            id=notice_id,
            display_code=str(row["display_code"]),
            title=str(row["title"]),
            description=str(row["description"]),
            category=category,  # type: ignore[arg-type]
            category_label=NOTICE_CATEGORY_LABELS.get(category, category),
            published_at=row.get("published_at"),
            attachments=[NoticeAttachmentResponse(**item) for item in attachments],
            view_count=int(row.get("view_count") or 0),
            like_count=int(row.get("like_count") or 0),
            liked_by_me=liked,
            pinned=bool(row.get("pinned")),
            slot_index=int(row["pin_slot_index"]) if row.get("pin_slot_index") else None,
        )

    async def _to_detail(
        self,
        row: dict[str, Any],
        *,
        contact_id: str | None,
        contact_user_id: str | None,
    ) -> ResidentNoticeDetailResponse:
        """Map a notice row to resident detail."""
        notice_id = str(row["id"])
        attachments = await self.repo.list_attachments(
            organization_id=self.organization_id,
            notice_id=notice_id,
        )
        category = str(row["category"])
        liked = await self._liked_by_me(
            notice_id=notice_id,
            contact_id=contact_id,
            contact_user_id=contact_user_id,
        )
        scope_label = row.get("scope_label")
        if str(row.get("scope_type")) == NoticeScopeType.WHOLE_SOCIETY.value:
            scope_label = "Whole society"
        return ResidentNoticeDetailResponse(
            id=notice_id,
            display_code=str(row["display_code"]),
            title=str(row["title"]),
            description=str(row["description"]),
            category=category,  # type: ignore[arg-type]
            category_label=NOTICE_CATEGORY_LABELS.get(category, category),
            scope_label=scope_label,
            published_at=row.get("published_at"),
            attachments=[NoticeAttachmentResponse(**item) for item in attachments],
            view_count=int(row.get("view_count") or 0),
            like_count=int(row.get("like_count") or 0),
            liked_by_me=liked,
            pinned=bool(row.get("pinned")),
            slot_index=int(row["pin_slot_index"]) if row.get("pin_slot_index") else None,
        )
