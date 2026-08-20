"""Community events persistence."""

from __future__ import annotations

import json
from typing import Any

from apps.user_service.app.db.repositories.base_repository import BaseRepository
from apps.user_service.app.schemas.enums import (
    CommunityEventListTab,
    CommunityEventRecordStatus,
    ResidentEventTimeframe,
)

_EVENT_SELECT = """
  e.id::text AS id,
  e.organization_id::text AS organization_id,
  e.project_id::text AS project_id,
  e.display_code,
  e.sequence_number,
  e.title,
  e.description,
  e.category::text AS category,
  e.publish_status::text AS publish_status,
  e.record_status::text AS record_status,
  e.facility_id::text AS facility_id,
  e.is_multi_day,
  e.start_date,
  e.end_date,
  e.start_time,
  e.end_time,
  e.event_type::text AS event_type,
  e.total_capacity,
  e.max_tickets_per_resident,
  e.booking_closes_at,
  e.adult_price_minor,
  e.child_ticket_mode::text AS child_ticket_mode,
  e.child_price_minor,
  e.apply_tax,
  e.tax_rate,
  e.currency,
  e.cover_image_path,
  e.published_at,
  e.completed_at,
  e.cancelled_at,
  e.cancelled_reason,
  e.deleted_at,
  e.tickets_booked,
  e.bookings_count,
  e.paid_bookings_count,
  e.revenue_collected_minor,
  e.created_by_user_id::text AS created_by_user_id,
  e.updated_by_user_id::text AS updated_by_user_id,
  e.created_at,
  e.updated_at,
  f.name AS facility_name,
  f.facility_type,
  f.facility_subtype,
  f.location_notes,
  f.floor_level,
  f.wing,
  t.name AS tower_name
"""

_BOOKING_SELECT = """
  b.id::text AS id,
  b.organization_id::text AS organization_id,
  b.project_id::text AS project_id,
  b.event_id::text AS event_id,
  b.display_code,
  b.sequence_number,
  b.contact_id::text AS contact_id,
  b.unit_id::text AS unit_id,
  b.adult_tickets,
  b.child_tickets,
  b.total_tickets,
  b.subtotal_minor,
  b.tax_minor,
  b.total_amount_minor,
  b.currency,
  b.booking_status::text AS booking_status,
  b.payment_status::text AS payment_status,
  b.paid_at,
  b.paid_by_user_id::text AS paid_by_user_id,
  b.payment_notes,
  b.booked_at,
  b.cancelled_at,
  b.cancelled_by_contact_id::text AS cancelled_by_contact_id,
  b.cancelled_by_user_id::text AS cancelled_by_user_id,
  b.gate_qr_token,
  b.created_at,
  b.updated_at
"""


class CommunityEventsRepository(BaseRepository):
    """Database operations for community events tables."""

    async def allocate_event_sequence(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> int:
        """Return next monotonic event sequence number for a project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_sequence
            FROM community_events
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return int(row["next_sequence"])

    async def allocate_booking_sequence(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> int:
        """Return next monotonic booking sequence number for a project."""
        row = await self.db_connection.fetchrow(
            """
            SELECT COALESCE(MAX(sequence_number), 0) + 1 AS next_sequence
            FROM community_event_bookings
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return int(row["next_sequence"])

    async def insert_event(
        self,
        *,
        organization_id: str,
        project_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Insert community_events row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO community_events (
                organization_id, project_id, display_code, sequence_number,
                title, description, category, publish_status, record_status,
                facility_id, is_multi_day, start_date, end_date, start_time, end_time,
                event_type, total_capacity, max_tickets_per_resident, booking_closes_at,
                adult_price_minor, child_ticket_mode, child_price_minor,
                apply_tax, tax_rate, currency, cover_image_path,
                published_at, created_by_user_id, updated_by_user_id
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4, $5, $6,
                $7::community_event_category, $8::community_event_publish_status,
                $9::community_event_record_status,
                $10::uuid, $11, $12, $13, $14, $15,
                $16::community_event_type, $17, $18, $19,
                $20, $21::community_event_child_ticket_mode, $22,
                $23, $24, $25, $26, $27, $28::uuid, $29::uuid
            )
            RETURNING id::text AS id
            """,
            organization_id,
            project_id,
            data["display_code"],
            data["sequence_number"],
            data["title"],
            data.get("description", ""),
            data["category"],
            data["publish_status"],
            data.get("record_status", CommunityEventRecordStatus.ACTIVE.value),
            data.get("facility_id"),
            data.get("is_multi_day", False),
            data["start_date"],
            data["end_date"],
            data.get("start_time"),
            data.get("end_time"),
            data["event_type"],
            data.get("total_capacity"),
            data.get("max_tickets_per_resident", 4),
            data.get("booking_closes_at"),
            data.get("adult_price_minor", 0),
            data.get("child_ticket_mode", "not_applicable"),
            data.get("child_price_minor", 0),
            data.get("apply_tax", False),
            data.get("tax_rate", 18.0),
            data.get("currency", "INR"),
            data.get("cover_image_path"),
            data.get("published_at"),
            data.get("created_by_user_id"),
            data.get("updated_by_user_id"),
        )
        return await self.fetch_event_by_id(
            organization_id=organization_id,
            project_id=project_id,
            event_id=str(row["id"]),
        )

    async def update_event_fields(
        self,
        *,
        organization_id: str,
        project_id: str,
        event_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch event header fields."""
        if not fields:
            return await self.fetch_event_by_id(
                organization_id=organization_id,
                project_id=project_id,
                event_id=event_id,
            )
        casts = {
            "category": "::community_event_category",
            "publish_status": "::community_event_publish_status",
            "record_status": "::community_event_record_status",
            "event_type": "::community_event_type",
            "child_ticket_mode": "::community_event_child_ticket_mode",
            "facility_id": "::uuid",
            "created_by_user_id": "::uuid",
            "updated_by_user_id": "::uuid",
        }
        set_parts: list[str] = []
        values: list[Any] = [organization_id, project_id, event_id]
        idx = 4
        for key, value in fields.items():
            set_parts.append(f"{key} = ${idx}{casts.get(key, '')}")
            values.append(value)
            idx += 1
        set_parts.append("updated_at = now()")
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE community_events e
            SET {", ".join(set_parts)}
            WHERE e.organization_id = $1::uuid
              AND e.project_id = $2::uuid
              AND e.id = $3::uuid
            RETURNING e.id::text AS id
            """,
            *values,
        )
        if row is None:
            return None
        return await self.fetch_event_by_id(
            organization_id=organization_id,
            project_id=project_id,
            event_id=event_id,
        )

    async def fetch_event_by_id(
        self,
        *,
        organization_id: str,
        project_id: str,
        event_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one event with facility join."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT {_EVENT_SELECT}
            FROM community_events e
            LEFT JOIN facilities f ON f.id = e.facility_id
            LEFT JOIN towers t ON t.id = f.tower_id
            WHERE e.organization_id = $1::uuid
              AND e.project_id = $2::uuid
              AND e.id = $3::uuid
            """,
            organization_id,
            project_id,
            event_id,
        )
        return dict(row) if row else None

    async def fetch_resident_event_by_id(
        self,
        *,
        organization_id: str,
        event_id: str,
    ) -> dict[str, Any] | None:
        """Fetch published active event for resident."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT {_EVENT_SELECT}
            FROM community_events e
            LEFT JOIN facilities f ON f.id = e.facility_id
            LEFT JOIN towers t ON t.id = f.tower_id
            WHERE e.organization_id = $1::uuid
              AND e.id = $2::uuid
              AND e.publish_status = 'published'::community_event_publish_status
              AND e.record_status = 'active'::community_event_record_status
            """,
            organization_id,
            event_id,
        )
        return dict(row) if row else None

    async def list_events(
        self,
        *,
        organization_id: str,
        project_id: str,
        tab: str,
        search: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Admin paginated event list."""
        conditions = ["e.organization_id = $1::uuid", "e.project_id = $2::uuid"]
        values: list[Any] = [organization_id, project_id]
        idx = 3

        if tab == CommunityEventListTab.DELETED.value:
            conditions.append("e.record_status = 'deleted'::community_event_record_status")
        else:
            conditions.append("e.record_status = 'active'::community_event_record_status")
            if tab != CommunityEventListTab.ALL.value:
                conditions.append(f"e.publish_status = ${idx}::community_event_publish_status")
                values.append(tab)
                idx += 1

        if search and search.strip():
            conditions.append(f"e.title ILIKE ${idx}")
            values.append(f"%{search.strip()}%")
            idx += 1

        where_sql = " AND ".join(conditions)
        total_row = await self.db_connection.fetchrow(
            f"SELECT COUNT(*) AS total FROM community_events e WHERE {where_sql}",
            *values,
        )
        total = int(total_row["total"]) if total_row else 0

        rows = await self.db_connection.fetch(
            f"""
            SELECT
              {_EVENT_SELECT},
              COALESCE(agg.adult_tickets, 0) AS ticket_breakdown_adult,
              COALESCE(agg.child_tickets, 0) AS ticket_breakdown_child
            FROM community_events e
            LEFT JOIN facilities f ON f.id = e.facility_id
            LEFT JOIN towers t ON t.id = f.tower_id
            LEFT JOIN LATERAL (
              SELECT
                SUM(b.adult_tickets) AS adult_tickets,
                SUM(b.child_tickets) AS child_tickets
              FROM community_event_bookings b
              WHERE b.event_id = e.id
                AND b.booking_status IN ('confirmed', 'waitlisted')
            ) agg ON true
            WHERE {where_sql}
            ORDER BY e.start_date DESC, e.created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *values,
            limit,
            offset,
        )
        return [dict(row) for row in rows], total

    async def get_summary_counts(
        self,
        *,
        organization_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """Dashboard summary aggregates."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE record_status = 'active'::community_event_record_status
              ) AS total_events,
              COUNT(*) FILTER (
                WHERE record_status = 'active'::community_event_record_status
                  AND publish_status = 'published'::community_event_publish_status
                  AND end_date >= CURRENT_DATE
              ) AS upcoming,
              COALESCE(SUM(tickets_booked) FILTER (
                WHERE record_status = 'active'::community_event_record_status
                  AND publish_status = 'published'::community_event_publish_status
              ), 0) AS total_rsvps,
              COALESCE(SUM(revenue_collected_minor) FILTER (
                WHERE record_status = 'active'::community_event_record_status
              ), 0) AS revenue_collected_minor,
              COUNT(*) FILTER (
                WHERE record_status = 'active'::community_event_record_status
              ) AS tab_all,
              COUNT(*) FILTER (
                WHERE record_status = 'active'::community_event_record_status
                  AND publish_status = 'draft'::community_event_publish_status
              ) AS tab_draft,
              COUNT(*) FILTER (
                WHERE record_status = 'active'::community_event_record_status
                  AND publish_status = 'published'::community_event_publish_status
              ) AS tab_published,
              COUNT(*) FILTER (
                WHERE record_status = 'active'::community_event_record_status
                  AND publish_status = 'completed'::community_event_publish_status
              ) AS tab_completed,
              COUNT(*) FILTER (
                WHERE record_status = 'active'::community_event_record_status
                  AND publish_status = 'cancelled'::community_event_publish_status
              ) AS tab_cancelled,
              COUNT(*) FILTER (
                WHERE record_status = 'deleted'::community_event_record_status
              ) AS tab_deleted
            FROM community_events
            WHERE organization_id = $1::uuid AND project_id = $2::uuid
            """,
            organization_id,
            project_id,
        )
        return dict(row) if row else {}

    async def replace_gallery(
        self,
        *,
        organization_id: str,
        project_id: str,
        event_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        """Replace gallery rows for an event."""
        await self.db_connection.execute(
            """
            DELETE FROM community_event_media
            WHERE organization_id = $1::uuid AND event_id = $2::uuid
            """,
            organization_id,
            event_id,
        )
        for item in items:
            await self.db_connection.execute(
                """
                INSERT INTO community_event_media (
                  organization_id, project_id, event_id,
                  file_path, file_name, mime_type, size_bytes, sort_order
                )
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8)
                """,
                organization_id,
                project_id,
                event_id,
                item["file_path"],
                item.get("file_name"),
                item["mime_type"],
                item["size_bytes"],
                item.get("sort_order", 0),
            )

    async def list_gallery(
        self,
        *,
        organization_id: str,
        event_id: str,
    ) -> list[dict[str, Any]]:
        """List gallery rows for an event."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              id::text AS id,
              file_path,
              file_name,
              mime_type,
              size_bytes,
              sort_order
            FROM community_event_media
            WHERE organization_id = $1::uuid AND event_id = $2::uuid
            ORDER BY sort_order, created_at
            """,
            organization_id,
            event_id,
        )
        return [dict(row) for row in rows]

    async def insert_booking(
        self,
        *,
        organization_id: str,
        project_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Insert booking row."""
        row = await self.db_connection.fetchrow(
            """
            INSERT INTO community_event_bookings (
              organization_id, project_id, event_id, display_code, sequence_number,
              contact_id, unit_id, adult_tickets, child_tickets, total_tickets,
              subtotal_minor, tax_minor, total_amount_minor, currency,
              booking_status, payment_status, gate_qr_token
            )
            VALUES (
              $1::uuid, $2::uuid, $3::uuid, $4, $5,
              $6::uuid, $7::uuid, $8, $9, $10,
              $11, $12, $13, $14,
              $15::community_event_booking_status,
              $16::community_event_payment_status,
              $17
            )
            RETURNING id::text AS id
            """,
            organization_id,
            project_id,
            data["event_id"],
            data["display_code"],
            data["sequence_number"],
            data["contact_id"],
            data["unit_id"],
            data["adult_tickets"],
            data["child_tickets"],
            data["total_tickets"],
            data["subtotal_minor"],
            data["tax_minor"],
            data["total_amount_minor"],
            data.get("currency", "INR"),
            data["booking_status"],
            data["payment_status"],
            data.get("gate_qr_token"),
        )
        return await self.fetch_booking_by_id(
            organization_id=organization_id,
            booking_id=str(row["id"]),
        )

    async def fetch_booking_by_id(
        self,
        *,
        organization_id: str,
        booking_id: str,
        event_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Fetch one booking."""
        conditions = ["b.organization_id = $1::uuid", "b.id = $2::uuid"]
        values: list[Any] = [organization_id, booking_id]
        if event_id:
            conditions.append("b.event_id = $3::uuid")
            values.append(event_id)
        row = await self.db_connection.fetchrow(
            f"""
            SELECT {_BOOKING_SELECT},
              c.display_name AS contact_name,
              u.code AS unit_code
            FROM community_event_bookings b
            LEFT JOIN contacts c ON c.id = b.contact_id
            LEFT JOIN units u ON u.id = b.unit_id
            WHERE {" AND ".join(conditions)}
            """,
            *values,
        )
        return dict(row) if row else None

    async def fetch_booking_by_gate_token(
        self,
        *,
        organization_id: str,
        gate_qr_token: str,
    ) -> dict[str, Any] | None:
        """Fetch booking by gate QR token."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT {_BOOKING_SELECT},
              c.display_name AS contact_name,
              u.code AS unit_code,
              ev.title AS event_title,
              ev.start_date AS event_start_date
            FROM community_event_bookings b
            LEFT JOIN contacts c ON c.id = b.contact_id
            LEFT JOIN units u ON u.id = b.unit_id
            LEFT JOIN community_events ev ON ev.id = b.event_id
            WHERE b.organization_id = $1::uuid
              AND b.gate_qr_token = $2
            """,
            organization_id,
            gate_qr_token,
        )
        return dict(row) if row else None

    async def list_bookings_for_event(
        self,
        *,
        organization_id: str,
        project_id: str,
        event_id: str,
        booking_status: str | None,
        payment_status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Admin bookings list for an event."""
        conditions = [
            "b.organization_id = $1::uuid",
            "b.project_id = $2::uuid",
            "b.event_id = $3::uuid",
        ]
        values: list[Any] = [organization_id, project_id, event_id]
        idx = 4
        if booking_status:
            conditions.append(f"b.booking_status = ${idx}::community_event_booking_status")
            values.append(booking_status)
            idx += 1
        if payment_status:
            conditions.append(f"b.payment_status = ${idx}::community_event_payment_status")
            values.append(payment_status)
            idx += 1
        where_sql = " AND ".join(conditions)
        total_row = await self.db_connection.fetchrow(
            f"SELECT COUNT(*) AS total FROM community_event_bookings b WHERE {where_sql}",
            *values,
        )
        total = int(total_row["total"]) if total_row else 0
        rows = await self.db_connection.fetch(
            f"""
            SELECT {_BOOKING_SELECT},
              c.display_name AS contact_name,
              u.code AS unit_code
            FROM community_event_bookings b
            LEFT JOIN contacts c ON c.id = b.contact_id
            LEFT JOIN units u ON u.id = b.unit_id
            WHERE {where_sql}
            ORDER BY b.booked_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
            """,
            *values,
            limit,
            offset,
        )
        return [dict(row) for row in rows], total

    async def count_active_tickets_for_contact(
        self,
        *,
        organization_id: str,
        event_id: str,
        contact_id: str,
    ) -> int:
        """Sum active tickets for per-resident cap."""
        row = await self.db_connection.fetchrow(
            """
            SELECT COALESCE(SUM(total_tickets), 0) AS total
            FROM community_event_bookings
            WHERE organization_id = $1::uuid
              AND event_id = $2::uuid
              AND contact_id = $3::uuid
              AND booking_status IN ('confirmed', 'waitlisted')
            """,
            organization_id,
            event_id,
            contact_id,
        )
        return int(row["total"]) if row else 0

    async def update_booking_fields(
        self,
        *,
        organization_id: str,
        booking_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Patch booking row."""
        if not fields:
            return await self.fetch_booking_by_id(
                organization_id=organization_id,
                booking_id=booking_id,
            )
        casts = {
            "booking_status": "::community_event_booking_status",
            "payment_status": "::community_event_payment_status",
            "paid_by_user_id": "::uuid",
            "cancelled_by_contact_id": "::uuid",
            "cancelled_by_user_id": "::uuid",
        }
        set_parts: list[str] = []
        values: list[Any] = [organization_id, booking_id]
        idx = 3
        for key, value in fields.items():
            set_parts.append(f"{key} = ${idx}{casts.get(key, '')}")
            values.append(value)
            idx += 1
        set_parts.append("updated_at = now()")
        row = await self.db_connection.fetchrow(
            f"""
            UPDATE community_event_bookings b
            SET {", ".join(set_parts)}
            WHERE b.organization_id = $1::uuid AND b.id = $2::uuid
            RETURNING b.id::text AS id
            """,
            *values,
        )
        if row is None:
            return None
        return await self.fetch_booking_by_id(
            organization_id=organization_id,
            booking_id=booking_id,
        )

    async def adjust_event_aggregates_on_booking(
        self,
        *,
        organization_id: str,
        event_id: str,
        tickets_delta: int,
        bookings_delta: int,
    ) -> None:
        """Increment/decrement tickets_booked and bookings_count."""
        await self.db_connection.execute(
            """
            UPDATE community_events
            SET
              tickets_booked = GREATEST(0, tickets_booked + $3),
              bookings_count = GREATEST(0, bookings_count + $4),
              updated_at = now()
            WHERE organization_id = $1::uuid AND id = $2::uuid
            """,
            organization_id,
            event_id,
            tickets_delta,
            bookings_delta,
        )

    async def increment_paid_revenue(
        self,
        *,
        organization_id: str,
        event_id: str,
        amount_minor: int,
    ) -> None:
        """Increment paid booking count and revenue."""
        await self.db_connection.execute(
            """
            UPDATE community_events
            SET
              paid_bookings_count = paid_bookings_count + 1,
              revenue_collected_minor = revenue_collected_minor + $3,
              updated_at = now()
            WHERE organization_id = $1::uuid AND id = $2::uuid
            """,
            organization_id,
            event_id,
            amount_minor,
        )

    async def fetch_oldest_waitlisted_booking(
        self,
        *,
        organization_id: str,
        event_id: str,
    ) -> dict[str, Any] | None:
        """FIFO waitlisted booking for promotion."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT {_BOOKING_SELECT}
            FROM community_event_bookings b
            WHERE b.organization_id = $1::uuid
              AND b.event_id = $2::uuid
              AND b.booking_status = 'waitlisted'::community_event_booking_status
            ORDER BY b.booked_at ASC
            LIMIT 1
            """,
            organization_id,
            event_id,
        )
        return dict(row) if row else None

    async def list_resident_events(
        self,
        *,
        organization_id: str,
        project_id: str,
        timeframe: str,
        category: str | None,
        search: str | None,
        contact_id: str,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Resident upcoming/past event list."""
        conditions = [
            "e.organization_id = $1::uuid",
            "e.project_id = $2::uuid",
            "e.publish_status = 'published'::community_event_publish_status",
            "e.record_status = 'active'::community_event_record_status",
        ]
        values: list[Any] = [organization_id, project_id]
        idx = 3

        if timeframe == ResidentEventTimeframe.UPCOMING.value:
            conditions.append("e.end_date >= CURRENT_DATE")
        else:
            conditions.append("e.end_date < CURRENT_DATE")

        if category:
            conditions.append(f"e.category = ${idx}::community_event_category")
            values.append(category)
            idx += 1

        if search and search.strip():
            conditions.append(f"e.title ILIKE ${idx}")
            values.append(f"%{search.strip()}%")
            idx += 1

        where_sql = " AND ".join(conditions)
        order = (
            "e.start_date ASC"
            if timeframe == ResidentEventTimeframe.UPCOMING.value
            else "e.start_date DESC"
        )

        total_row = await self.db_connection.fetchrow(
            f"SELECT COUNT(*) AS total FROM community_events e WHERE {where_sql}",
            *values,
        )
        total = int(total_row["total"]) if total_row else 0

        values_with_contact = [*values, contact_id]
        contact_idx = len(values) + 1
        limit_idx = contact_idx + 1
        offset_idx = contact_idx + 2

        rows = await self.db_connection.fetch(
            f"""
            SELECT
              {_EVENT_SELECT},
              COALESCE(my.my_tickets, 0) AS my_tickets_count
            FROM community_events e
            LEFT JOIN facilities f ON f.id = e.facility_id
            LEFT JOIN towers t ON t.id = f.tower_id
            LEFT JOIN LATERAL (
              SELECT SUM(b.total_tickets) AS my_tickets
              FROM community_event_bookings b
              WHERE b.event_id = e.id
                AND b.contact_id = ${contact_idx}::uuid
                AND b.booking_status IN ('confirmed', 'waitlisted')
            ) my ON true
            WHERE {where_sql}
            ORDER BY {order}
            LIMIT ${limit_idx} OFFSET ${offset_idx}
            """,
            *values_with_contact,
            limit,
            offset,
        )
        return [dict(row) for row in rows], total

    async def sum_my_active_tickets(
        self,
        *,
        organization_id: str,
        project_id: str,
        contact_id: str,
        unit_id: str,
    ) -> dict[str, int]:
        """Resident badge summary."""
        row = await self.db_connection.fetchrow(
            """
            SELECT
              COALESCE(SUM(b.total_tickets), 0) AS active_ticket_count,
              COUNT(DISTINCT b.id) AS active_booking_count
            FROM community_event_bookings b
            JOIN community_events e ON e.id = b.event_id
            WHERE b.organization_id = $1::uuid
              AND e.project_id = $2::uuid
              AND b.contact_id = $3::uuid
              AND b.unit_id = $4::uuid
              AND b.booking_status IN ('confirmed', 'waitlisted')
              AND e.publish_status = 'published'::community_event_publish_status
              AND e.record_status = 'active'::community_event_record_status
            """,
            organization_id,
            project_id,
            contact_id,
            unit_id,
        )
        if not row:
            return {"active_ticket_count": 0, "active_booking_count": 0}
        return {
            "active_ticket_count": int(row["active_ticket_count"]),
            "active_booking_count": int(row["active_booking_count"]),
        }

    async def list_my_bookings(
        self,
        *,
        organization_id: str,
        project_id: str,
        contact_id: str,
        unit_id: str,
    ) -> list[dict[str, Any]]:
        """All active bookings for resident."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              b.id::text AS booking_id,
              b.display_code,
              b.event_id::text AS event_id,
              e.title AS event_title,
              e.start_date AS event_start_date,
              b.total_tickets,
              b.total_amount_minor,
              b.payment_status::text AS payment_status,
              b.booking_status::text AS booking_status,
              b.gate_qr_token
            FROM community_event_bookings b
            JOIN community_events e ON e.id = b.event_id
            WHERE b.organization_id = $1::uuid
              AND e.project_id = $2::uuid
              AND b.contact_id = $3::uuid
              AND b.unit_id = $4::uuid
              AND b.booking_status IN ('confirmed', 'waitlisted')
            ORDER BY e.start_date ASC
            """,
            organization_id,
            project_id,
            contact_id,
            unit_id,
        )
        return [dict(row) for row in rows]

    async def get_my_booking_for_event(
        self,
        *,
        organization_id: str,
        event_id: str,
        contact_id: str,
        unit_id: str,
    ) -> dict[str, Any] | None:
        """Resident booking for one event."""
        row = await self.db_connection.fetchrow(
            f"""
            SELECT {_BOOKING_SELECT}
            FROM community_event_bookings b
            WHERE b.organization_id = $1::uuid
              AND b.event_id = $2::uuid
              AND b.contact_id = $3::uuid
              AND b.unit_id = $4::uuid
              AND b.booking_status IN ('confirmed', 'waitlisted')
            ORDER BY b.booked_at DESC
            LIMIT 1
            """,
            organization_id,
            event_id,
            contact_id,
            unit_id,
        )
        return dict(row) if row else None

    async def contact_has_owner_or_tenant_on_unit(
        self,
        *,
        organization_id: str,
        contact_id: str,
        unit_id: str,
    ) -> bool:
        """True when contact has active Owner or Tenant role on unit."""
        row = await self.db_connection.fetchrow(
            """
            SELECT 1
            FROM contact_roles
            WHERE organization_id = $1::uuid
              AND contact_id = $2::uuid
              AND unit_id = $3::uuid
              AND status = 'active'::contact_role_status
              AND ended_at IS NULL
              AND role_type IN ('Owner'::contact_role_type, 'Tenant'::contact_role_type)
            LIMIT 1
            """,
            organization_id,
            contact_id,
            unit_id,
        )
        return row is not None

    async def insert_audit_log(
        self,
        *,
        organization_id: str,
        project_id: str,
        event_id: str | None,
        booking_id: str | None,
        action: str,
        actor_user_id: str | None = None,
        actor_contact_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Append audit log row."""
        await self.db_connection.execute(
            """
            INSERT INTO community_event_audit_log (
              organization_id, project_id, event_id, booking_id,
              action, actor_user_id, actor_contact_id, payload
            )
            VALUES (
              $1::uuid, $2::uuid, $3::uuid, $4::uuid,
              $5::community_event_audit_action,
              $6::uuid, $7::uuid, $8::jsonb
            )
            """,
            organization_id,
            project_id,
            event_id,
            booking_id,
            action,
            actor_user_id,
            actor_contact_id,
            json.dumps(payload or {}),
        )

    async def complete_past_events(self) -> list[str]:
        """Mark published past events as completed."""
        rows = await self.db_connection.fetch(
            """
            UPDATE community_events
            SET publish_status = 'completed'::community_event_publish_status,
                completed_at = now(),
                updated_at = now()
            WHERE publish_status = 'published'::community_event_publish_status
              AND record_status = 'active'::community_event_record_status
              AND (
                end_date < CURRENT_DATE
                OR (end_date = CURRENT_DATE AND end_time IS NOT NULL AND end_time < CURRENT_TIME)
              )
            RETURNING id::text AS id
            """
        )
        return [str(row["id"]) for row in rows]

    async def list_events_for_export(
        self,
        *,
        organization_id: str,
        project_id: str,
        tab: str,
        search: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Export events without pagination beyond cap."""
        items, _ = await self.list_events(
            organization_id=organization_id,
            project_id=project_id,
            tab=tab,
            search=search,
            limit=limit,
            offset=0,
        )
        return items

    async def list_bookings_for_export(
        self,
        *,
        organization_id: str,
        project_id: str,
        event_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Export bookings for an event."""
        items, _ = await self.list_bookings_for_event(
            organization_id=organization_id,
            project_id=project_id,
            event_id=event_id,
            booking_status=None,
            payment_status=None,
            limit=limit,
            offset=0,
        )
        return items

    async def list_confirmed_bookers_for_reminder(
        self,
        *,
        organization_id: str,
        event_id: str,
    ) -> list[dict[str, Any]]:
        """Contacts with confirmed bookings for event reminder push."""
        rows = await self.db_connection.fetch(
            """
            SELECT DISTINCT
              b.contact_id::text AS contact_id,
              c.user_id::text AS user_id
            FROM community_event_bookings b
            JOIN contacts c ON c.id = b.contact_id
            WHERE b.organization_id = $1::uuid
              AND b.event_id = $2::uuid
              AND b.booking_status = 'confirmed'::community_event_booking_status
              AND c.user_id IS NOT NULL
            """,
            organization_id,
            event_id,
        )
        return [dict(row) for row in rows]

    async def list_events_due_for_reminder(self, *, hours_ahead: int = 24) -> list[dict[str, Any]]:
        """Published events starting within the reminder window (default 24h)."""
        rows = await self.db_connection.fetch(
            """
            SELECT
              e.id::text AS id,
              e.organization_id::text AS organization_id,
              e.project_id::text AS project_id,
              e.title,
              e.start_date,
              e.start_time
            FROM community_events e
            WHERE e.publish_status = 'published'::community_event_publish_status
              AND e.record_status = 'active'::community_event_record_status
              AND (
                (
                  e.start_time IS NOT NULL
                  AND (e.start_date + e.start_time) >= now() + (($1::int - 1) || ' hours')::interval
                  AND (e.start_date + e.start_time) <= now() + (($1::int + 1) || ' hours')::interval
                )
                OR (
                  e.start_time IS NULL
                  AND e.start_date = (now() + ($1::int || ' hours')::interval)::date
                )
              )
            """,
            hours_ahead,
        )
        return [dict(row) for row in rows]
