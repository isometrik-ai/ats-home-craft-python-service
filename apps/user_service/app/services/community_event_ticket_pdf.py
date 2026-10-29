"""One-page PDF ticket for a community event booking."""

from __future__ import annotations

from typing import Any

from apps.user_service.app.utils.qr_code import qr_matrix

_PURPLE = (0.486, 0.227, 0.929)
_INK = (0.122, 0.161, 0.216)
_MUTED = (0.420, 0.447, 0.502)
_WHITE = (1, 1, 1)
_LINE = (0.898, 0.906, 0.922)
_HEAD = (0.953, 0.957, 0.965)


def build_community_event_ticket_pdf(ticket: dict[str, Any]) -> bytes:
    """Render a scannable ticket the resident can open or print."""
    return _pdf_document(_draw(ticket))


def _draw(ticket: dict[str, Any]) -> list[str]:
    """Header, booking facts, and an optional gate QR."""
    ops = _header(ticket)
    ops.extend(_facts(ticket))
    token = str(ticket.get("gate_qr_token") or "").strip()
    if token:
        ops.extend(_qr(token))
        ops.extend(_text(56, 268, "Show this QR at the gate", 11, "F1", _MUTED))
    return ops


def _header(ticket: dict[str, Any]) -> list[str]:
    """Event title and booking code on the colour band."""
    title = _fit(str(ticket.get("event_title") or "Community event"), 16, 360)
    code = str(ticket.get("display_code") or "")
    ops = _fill(40, 748, 515, 64, _PURPLE)
    ops.extend(_text(56, 778, title, 16, "F2", _WHITE))
    ops.extend(_text_right(547, 780, "Event ticket", 11, "F1", _WHITE))
    ops.extend(_text(56, 758, code, 12, "F2", _WHITE))
    return ops


def _facts(ticket: dict[str, Any]) -> list[str]:
    """When, venue, tickets, amount, payment, and booking code."""
    rows = (
        ("When", str(ticket.get("event_when") or "—")),
        ("Venue", str(ticket.get("venue") or "—")),
        ("Tickets", str(ticket.get("tickets_label") or "—")),
        ("Amount", str(ticket.get("amount_label") or "—")),
        ("Payment", str(ticket.get("payment_label") or "—")),
        ("Booking", str(ticket.get("display_code") or "—")),
    )
    ops: list[str] = []
    y = 700
    for label, value in rows:
        ops.extend(_text(56, y, label, 9, "F1", _MUTED))
        ops.extend(_text(160, y, _fit(value, 12, 360), 12, "F2", _INK))
        y -= 28
        ops.append(_rule(56, y + 18, 540))
    return ops


def _qr(token: str) -> list[str]:
    """Draw the gate QR as filled modules on the page."""
    matrix = qr_matrix(token)
    size = len(matrix)
    origin_x = 198.0
    origin_y = 300.0
    module = 6.0
    ops = _fill(
        origin_x - 8,
        origin_y - 8,
        size * module + 16,
        size * module + 16,
        _HEAD,
    )
    for row in range(size):
        for col in range(size):
            if not matrix[row][col]:
                continue
            x = origin_x + col * module
            y = origin_y + (size - 1 - row) * module
            ops.extend(_fill(x, y, module, module, _INK))
    return ops


def _fit(value: str, size: int, limit: float) -> str:
    """Truncate *value* with an ellipsis so it fits *limit* points."""
    if _text_width(value, size) <= limit:
        return value
    trimmed = value
    while trimmed and _text_width(f"{trimmed}...", size) > limit:
        trimmed = trimmed[:-1]
    return f"{trimmed}..."


def _text_width(value: str, size: int, *, bold: bool = False) -> float:
    """Approximate Helvetica width in points for *value*."""
    width = sum(520 if char != " " else 278 for char in value) * size / 1000
    return width * 1.06 if bold else width


def _fill(
    x: float,
    y: float,
    width: float,
    height: float,
    rgb: tuple[float, float, float],
) -> list[str]:
    """Filled rectangle in PDF content-stream operators."""
    red, green, blue = rgb
    return [
        f"{red:.3f} {green:.3f} {blue:.3f} rg",
        f"{x:.1f} {y:.1f} {width:.1f} {height:.1f} re f",
    ]


def _rule(start: float, y_pos: float, end: float) -> str:
    """Horizontal divider between fact rows."""
    red, green, blue = _LINE
    return (
        f"{red:.3f} {green:.3f} {blue:.3f} RG 0.6 w "
        f"{start:.1f} {y_pos:.1f} m {end:.1f} {y_pos:.1f} l S"
    )


def _text(
    x: float,
    y: float,
    value: str,
    size: int,
    font: str,
    rgb: tuple[float, float, float],
) -> list[str]:
    """Left-aligned text run at (*x*, *y*)."""
    red, green, blue = rgb
    return [
        "BT",
        f"{red:.3f} {green:.3f} {blue:.3f} rg",
        f"/{font} {size} Tf",
        f"1 0 0 1 {x:.1f} {y:.1f} Tm",
        f"({_escape(value)}) Tj",
        "ET",
    ]


def _text_right(
    right: float,
    y: float,
    value: str,
    size: int,
    font: str,
    rgb: tuple[float, float, float],
) -> list[str]:
    """Right-aligned text whose right edge sits at *right*."""
    width = _text_width(value, size, bold=font == "F2")
    return _text(right - width, y, value, size, font, rgb)


def _escape(value: str) -> str:
    """Escape parentheses and backslashes for a PDF string literal."""
    cleaned = value.encode("latin-1", errors="replace").decode("latin-1")
    return cleaned.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_document(commands: list[str]) -> bytes:
    """Wrap drawing operators in a one-page A4 PDF."""
    stream = "\n".join(commands).encode("latin-1", errors="replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>"
        ),
        (
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    return _assemble(objects)


def _assemble(objects: list[bytes]) -> bytes:
    """Build a PDF 1.4 file from numbered objects."""
    header = b"%PDF-1.4\n"
    body = bytearray(header)
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(body))
        body.extend(f"{index} 0 obj\n".encode("ascii"))
        body.extend(obj)
        body.extend(b"\nendobj\n")
    xref_at = len(body)
    body.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    body.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        body.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    body.extend(
        (
            f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(body)
