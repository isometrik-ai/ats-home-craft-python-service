"""Post and query facility booking ledger entries (contact-scoped)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import asyncpg

from apps.user_service.app.db.repositories.contact_units_repository import (
    ContactUnitsRepository,
)
from apps.user_service.app.db.repositories.facility_booking_ledger_repository import (
    FacilityBookingLedgerRepository,
)
from apps.user_service.app.db.repositories.facility_reservations_repository import (
    FacilityReservationsRepository,
)
from apps.user_service.app.schemas.enums import (
    LEDGER_ACTIVITY_LABELS,
    FacilityBookingLedgerType,
)
from apps.user_service.app.schemas.facility_booking import (
    LedgerEntryResponse,
    LedgerStatementResponse,
    RaiseChargeRequest,
    UnbilledContactResponse,
    UnbilledLedgerResponse,
)
from apps.user_service.app.services.facility_booking.money import ts_round
from apps.user_service.app.services.facility_booking.pricing import net_paid
from apps.user_service.app.services.project_setup_service import ProjectSetupService
from apps.user_service.app.utils.common_utils import UserContext
from libs.shared_utils.http_exceptions import NotFoundException, ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class FacilityBookingLedgerService:
    """Contact-owned booking ledger used by reservation lifecycle and staff charges."""

    def __init__(self, *, db_connection: asyncpg.Connection, user_context: UserContext) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.repo = FacilityBookingLedgerRepository(db_connection)
        self.reservations_repo = FacilityReservationsRepository(db_connection)
        self.contact_units_repo = ContactUnitsRepository(db_connection)
        self.setup_service = ProjectSetupService(
            db_connection=db_connection, user_context=user_context
        )

    @property
    def _org_id(self) -> str:
        """Organization id from user context."""
        return self.user_context.organization_id

    async def post(
        self,
        *,
        project_id: str,
        contact_id: str,
        entry_type: str,
        description: str,
        amount: int,
        reservation_id: str | None = None,
        invoice_id: str | None = None,
        method: str | None = None,
        posted_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Insert a ledger entry when amount is non-zero."""
        if amount == 0:
            return {}
        row = await self.repo.insert(
            {
                "organization_id": self._org_id,
                "project_id": project_id,
                "contact_id": contact_id,
                "reservation_id": reservation_id,
                "invoice_id": invoice_id,
                "entry_type": entry_type,
                "description": description,
                "amount": amount,
                "method": method,
                "posted_at": posted_at or datetime.now(timezone.utc),
                "created_by_user_id": self.user_context.user_id,
            }
        )
        return dict(row)

    async def net_paid_for(self, *, project_id: str, reservation_id: str) -> int:
        """Return net amount paid/charged for a reservation."""
        rows = await self.repo.list_for_reservation(
            organization_id=self._org_id,
            project_id=project_id,
            reservation_id=reservation_id,
        )
        return net_paid((str(row["entry_type"]), int(row["amount"])) for row in rows)

    def serialize(self, row: dict[str, Any]) -> dict[str, Any]:
        """Map a ledger row to the API response shape."""
        entry_type = str(row["entry_type"])
        return LedgerEntryResponse(
            id=str(row["id"]),
            contact_id=str(row["contact_id"]),
            contact_name=row.get("contact_name"),
            reservation_id=str(row["reservation_id"]) if row.get("reservation_id") else None,
            facility_name=row.get("facility_name"),
            invoice_id=str(row["invoice_id"]) if row.get("invoice_id") else None,
            entry_type=FacilityBookingLedgerType(entry_type),
            activity_label=LEDGER_ACTIVITY_LABELS.get(entry_type, "Charge"),
            description=str(row["description"]),
            amount=int(row["amount"]),
            method=row.get("method"),
            posted_at=row["posted_at"],
        ).model_dump(mode="json")

    async def list_reservation(
        self, *, project_id: str, reservation_id: str
    ) -> list[dict[str, Any]]:
        """List ledger entries tied to one reservation."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.repo.list_for_reservation(
            organization_id=self._org_id,
            project_id=project_id,
            reservation_id=reservation_id,
        )
        return [self.serialize(row) for row in rows]

    async def statement(
        self,
        *,
        project_id: str,
        contact_id: str,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Return paginated ledger entries and running balance for a contact."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows, _ = await self.repo.list_for_contact(
            organization_id=self._org_id,
            project_id=project_id,
            contact_id=contact_id,
            page=page,
            page_size=page_size,
        )
        balance = await self.repo.contact_balance(
            organization_id=self._org_id,
            project_id=project_id,
            contact_id=contact_id,
        )
        return LedgerStatementResponse(
            entries=[LedgerEntryResponse.model_validate(self.serialize(row)) for row in rows],
            balance=balance,
        ).model_dump(mode="json")

    async def list_project(
        self,
        *,
        project_id: str,
        contact_id: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        """List ledger entries for a project with optional contact filter."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows, total = await self.repo.list_project(
            organization_id=self._org_id,
            project_id=project_id,
            contact_id=contact_id,
            page=page,
            page_size=page_size,
        )
        return [self.serialize(row) for row in rows], total

    async def unbilled(self, *, project_id: str) -> dict[str, Any]:
        """Summarize unbilled ledger entries grouped by contact."""
        await self.setup_service.ensure_project(project_id=project_id)
        rows = await self.repo.list_unbilled(organization_id=self._org_id, project_id=project_id)
        grouped: dict[str, dict[str, Any]] = {}
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            contact_id = str(row["contact_id"])
            amount = int(row["amount"])
            current = grouped.get(contact_id)
            if current is None:
                grouped[contact_id] = {
                    "contact_id": contact_id,
                    "contact_name": row.get("contact_name") or "Resident",
                    "amount": 0,
                    "oldest_at": row["posted_at"],
                }
                current = grouped[contact_id]
            current["amount"] += amount
            counts[contact_id] += 1
            if row["posted_at"] < current["oldest_at"]:
                current["oldest_at"] = row["posted_at"]
        contacts = [
            UnbilledContactResponse(
                contact_id=item["contact_id"],
                contact_name=item["contact_name"],
                amount=item["amount"],
                count=counts[item["contact_id"]],
                oldest_at=item["oldest_at"],
            )
            for item in sorted(grouped.values(), key=lambda item: -item["amount"])
            if item["amount"] != 0
        ]
        entries = [LedgerEntryResponse.model_validate(self.serialize(row)) for row in rows]
        return UnbilledLedgerResponse(
            unbilled_total=sum(item.amount for item in contacts),
            unbilled_contacts=len(contacts),
            contacts=contacts,
            entries=entries,
        ).model_dump(mode="json")

    async def raise_charge(self, *, project_id: str, body: RaiseChargeRequest) -> dict[str, Any]:
        """Post a manual charge to a contact ledger."""
        await self.setup_service.ensure_project(project_id=project_id)
        in_project = await self.contact_units_repo.contact_has_active_project_membership(
            organization_id=self._org_id,
            contact_id=body.contact_id,
            project_id=project_id,
        )
        if not in_project:
            raise ValidationException(
                message_key="facility_booking.errors.host_not_in_project",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        if body.reservation_id:
            reservation = await self.reservations_repo.get_reservation(
                organization_id=self._org_id,
                project_id=project_id,
                reservation_id=body.reservation_id,
            )
            if not reservation:
                raise NotFoundException(
                    message_key="facility_booking.errors.reservation_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
        amount = ts_round(body.amount)
        if amount <= 0:
            raise ValidationException(
                message_key="facility_booking.errors.invalid_charge_amount",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        row = await self.post(
            project_id=project_id,
            contact_id=body.contact_id,
            reservation_id=body.reservation_id,
            entry_type=FacilityBookingLedgerType.MANUAL_CHARGE.value,
            description=body.description.strip(),
            amount=amount,
        )
        return self.serialize(
            {
                **row,
                "contact_name": None,
                "facility_name": None,
            }
        )
