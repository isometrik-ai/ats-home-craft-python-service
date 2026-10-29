"""Build community event booking confirmation email context and attachments."""

from __future__ import annotations

import base64
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from apps.user_service.app.schemas.enums import CommunityEventPaymentStatus
from apps.user_service.app.services.community_event_ticket_pdf import (
    build_community_event_ticket_pdf,
)
from apps.user_service.app.services.fee_invoice_pdf import format_rupees
from apps.user_service.app.utils.qr_code import qr_html_table, qr_png_bytes
from apps.user_service.app.utils.unit_list_serialization import (
    format_primary_contact_email,
)
from libs.shared_config.app_settings import shared_settings

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_PAYMENT_LABELS = {
    CommunityEventPaymentStatus.NOT_APPLICABLE.value: "Free event — no payment needed",
    CommunityEventPaymentStatus.PENDING.value: "Pay at the clubhouse (pending)",
    CommunityEventPaymentStatus.PAID.value: "Paid",
    CommunityEventPaymentStatus.WAIVED.value: "Payment waived",
}


def contact_booking_email(contact: dict[str, Any] | None) -> str | None:
    """Primary contact email, then the linked auth user email."""
    if not contact:
        return None
    primary = format_primary_contact_email(contact.get("emails"))
    if primary:
        return primary
    fallback = str(contact.get("email") or "").strip()
    return fallback or None


def tickets_label(*, adult_tickets: int, child_tickets: int, total_tickets: int) -> str:
    """Human-readable ticket mix, e.g. 2 adult, 1 child (3 total)."""
    parts: list[str] = []
    if adult_tickets:
        parts.append(f"{adult_tickets} adult")
    if child_tickets:
        parts.append(f"{child_tickets} child")
    mix = ", ".join(parts) if parts else "0"
    return f"{mix} ({total_tickets} total)"


def format_event_when(event: dict[str, Any]) -> str:
    """19 August 2026, 4:00 PM — or date only when time is unset."""
    start = event.get("start_date")
    if not start:
        return "—"
    day = start if isinstance(start, date) else date.fromisoformat(str(start)[:10])
    label = f"{day.day} {_MONTHS[day.month - 1]} {day.year}"
    clock = _format_time(event.get("start_time"))
    if clock:
        return f"{label}, {clock}"
    return label


def venue_label(event: dict[str, Any]) -> str:
    """Facility name plus location, matching resident event cards."""
    name = str(event.get("facility_name") or "").strip()
    location = _facility_location_label(event)
    if name and location:
        return f"{name} — {location}"
    return name or location or "—"


def _facility_location_label(event: dict[str, Any]) -> str:
    parts: list[str] = []
    tower = event.get("tower_name")
    if tower:
        parts.append(str(tower))
    for key in ("wing", "floor_level"):
        val = event.get(key)
        if val:
            parts.append(str(val))
    if not parts:
        return str(event.get("location_notes") or "").strip()
    label = ", ".join(parts)
    notes = event.get("location_notes")
    if notes:
        return f"{label} — {notes}"
    return label


def payment_label(booking: dict[str, Any]) -> str:
    """Resident-facing payment line."""
    status = str(booking.get("payment_status") or "")
    return _PAYMENT_LABELS.get(status, status or "—")


def amount_label(booking: dict[str, Any]) -> str:
    """Format stored paise as rupees, or Free."""
    minor = int(booking.get("total_amount_minor") or 0)
    if minor <= 0:
        return "Free"
    rupees = (Decimal(minor) / Decimal(100)).quantize(Decimal("0.01"))
    return format_rupees(rupees)


def build_booking_email_body_context(
    *,
    contact: dict[str, Any],
    event: dict[str, Any],
    booking: dict[str, Any],
    project_name: str,
) -> dict[str, str]:
    """Template variables for booking confirmation / waitlist emails."""
    first_name = str(contact.get("first_name") or "").strip() or "there"
    adult = int(booking.get("adult_tickets") or 0)
    child = int(booking.get("child_tickets") or 0)
    total = int(booking.get("total_tickets") or (adult + child))
    token = str(booking.get("gate_qr_token") or "").strip()
    display_code = str(booking.get("display_code") or "").strip()
    return {
        "app_name": shared_settings.app_name,
        "first_name": first_name,
        "event_title": str(event.get("title") or "Community event").strip(),
        "project_name": project_name.strip() or "—",
        "display_code": display_code or "—",
        "event_when": format_event_when(event),
        "venue": venue_label(event),
        "tickets_label": tickets_label(
            adult_tickets=adult,
            child_tickets=child,
            total_tickets=total,
        ),
        "adult_tickets": str(adult),
        "child_tickets": str(child),
        "total_tickets": str(total),
        "amount_label": amount_label(booking),
        "payment_label": payment_label(booking),
        "qr_html": qr_html_table(token) if token else "",
        "qr_note": (
            "Show the QR code below or the attached ticket PDF at the gate."
            if token
            else "Your tickets will be issued when a spot opens up."
        ),
    }


def build_ticket_attachments(
    *, booking: dict[str, Any], event: dict[str, Any]
) -> list[dict[str, str]]:
    """PDF ticket plus PNG QR when the booking has a gate token."""
    token = str(booking.get("gate_qr_token") or "").strip()
    if not token:
        return []
    display_code = str(booking.get("display_code") or "ticket").strip() or "ticket"
    ticket = {
        "event_title": str(event.get("title") or "Community event"),
        "display_code": display_code,
        "event_when": format_event_when(event),
        "venue": venue_label(event),
        "tickets_label": tickets_label(
            adult_tickets=int(booking.get("adult_tickets") or 0),
            child_tickets=int(booking.get("child_tickets") or 0),
            total_tickets=int(booking.get("total_tickets") or 0),
        ),
        "amount_label": amount_label(booking),
        "payment_label": payment_label(booking),
        "gate_qr_token": token,
    }
    pdf = build_community_event_ticket_pdf(ticket)
    png = qr_png_bytes(token)
    return [
        {
            "filename": f"{display_code}-ticket.pdf",
            "content": base64.b64encode(pdf).decode("ascii"),
        },
        {
            "filename": f"{display_code}-qr.png",
            "content": base64.b64encode(png).decode("ascii"),
        },
    ]


def _format_time(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        hour = value.hour
        minute = value.minute
    else:
        text = str(value)
        parsed = text[:8] if len(text) >= 5 else text
        try:
            hour, minute = (int(part) for part in parsed.split(":")[:2])
        except ValueError:
            return None
    suffix = "AM" if hour < 12 else "PM"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute:02d} {suffix}"
