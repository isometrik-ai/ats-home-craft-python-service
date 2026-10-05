"""One-page PDF for a fee invoice."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

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
_PURPLE = (0.486, 0.227, 0.929)
_INK = (0.122, 0.161, 0.216)
_MUTED = (0.420, 0.447, 0.502)
_WHITE = (1, 1, 1)
_LINE = (0.898, 0.906, 0.922)
_HEAD = (0.953, 0.957, 0.965)
_DUE = (0.945, 0.910, 1)
_LEFT = 48
_RIGHT = 547
_WIDTHS = {
    " ": 278,
    ",": 278,
    ".": 278,
    "-": 333,
    "(": 333,
    ")": 333,
    "R": 667,
    "s": 500,
    **{digit: 556 for digit in "0123456789"},
}


def build_fee_invoice_pdf(invoice: dict[str, Any]) -> bytes:
    """Render the invoice as a one-page PDF the resident can open."""
    return _pdf_document(_draw(invoice))


def _draw(invoice: dict[str, Any]) -> list[str]:
    """Header, the four dates, the charge table, and the amount due."""
    ops = _header(invoice)
    ops.extend(_facts(invoice))
    ops.extend(_table(invoice))
    return ops


def _header(invoice: dict[str, Any]) -> list[str]:
    """Project name and invoice number on the colour band."""
    project = str(invoice.get("project_name") or "Fee invoice")
    number = str(invoice["invoice_number"])
    ops = _fill(40, 748, 515, 64, _PURPLE)
    ops.extend(_text(56, 778, _fit(project, 16, 320), 16, "F2", _WHITE))
    ops.extend(_text_right(547, 780, "Fee invoice", 11, "F1", _WHITE))
    ops.extend(_text(56, 758, number, 12, "F2", _WHITE))
    return ops


def _facts(invoice: dict[str, Any]) -> list[str]:
    """Unit, billing month, invoice date, and due date."""
    rows = (
        ("Unit", str(invoice.get("unit_code") or "")),
        ("Billing month", _month_label(invoice["billing_month"])),
        ("Invoice date", _long_date(invoice["invoice_date"])),
        ("Due date", _long_date(invoice["due_date"])),
    )
    ops: list[str] = []
    for index, (label, value) in enumerate(rows):
        column = _LEFT + index * 128
        ops.extend(_text(column, 712, label, 8, "F1", _MUTED))
        ops.extend(_text(column, 696, _fit(value, 11, 120), 11, "F2", _INK))
    ops.append(_rule(40, 680, 555))
    return ops


def _table(invoice: dict[str, Any]) -> list[str]:
    """Charge lines, then taxable, tax, round-off, and amount due."""
    ops = _fill(40, 644, 515, 24, _HEAD)
    ops.extend(_text(_LEFT, 652, "Description", 8, "F2", _MUTED))
    ops.extend(_text(168, 652, "Calculation", 8, "F2", _MUTED))
    ops.extend(_text_right(400, 652, "Taxable", 8, "F2", _MUTED))
    ops.extend(_text_right(488, 652, "Tax", 8, "F2", _MUTED))
    ops.extend(_text_right(_RIGHT, 652, "Amount", 8, "F2", _MUTED))
    cursor = 632.0
    for line in invoice.get("lines") or []:
        ops.append(_rule(40, cursor, 555))
        baseline = cursor - 16
        ops.extend(_line_cells(line, baseline))
        cursor -= 26
    ops.append(_rule(40, cursor, 555))
    return ops + _totals(invoice, cursor - 36)


def _line_cells(line: dict[str, Any], baseline: float) -> list[str]:
    """One charge row, with the sq ft working and the tax percent."""
    description = _fit(str(line["description"]), 9, 110)
    ops = _text(_LEFT, baseline, description, 9, "F1", _INK)
    ops.extend(_text(168, baseline, _fit(_calculation(line), 8, 145), 8, "F1", _INK))
    ops.extend(_text_right(400, baseline, _rupees(line["taxable_amount"]), 8, "F1", _INK))
    ops.extend(_text_right(488, baseline, _tax_cell(line), 8, "F1", _INK))
    ops.extend(_text_right(_RIGHT, baseline, _rupees(line["line_total"]), 8, "F1", _INK))
    return ops


def _totals(invoice: dict[str, Any], top: float) -> list[str]:
    """Right-hand totals, with the amount due on its own band."""
    percent = _shared_percent(invoice)
    tax_label = f"Tax {percent}%" if percent else "Tax"
    rows = (
        ("Taxable", _rupees(invoice["taxable_amount"])),
        (tax_label, _rupees(invoice["tax_amount"])),
        ("Round off", _rupees(invoice["round_off_amount"])),
    )
    ops: list[str] = []
    y = top
    for label, value in rows:
        ops.extend(_text(360, y, label, 10, "F1", _MUTED))
        ops.extend(_text_right(_RIGHT, y, value, 10, "F1", _INK))
        y -= 18
    ops.extend(_fill(348, y - 8, 207, 26, _DUE))
    ops.extend(_text(360, y, "Amount due", 11, "F2", _INK))
    ops.extend(_text_right(_RIGHT, y, _rupees(invoice["total_amount"]), 11, "F2", _INK))
    return ops


def _calculation(line: dict[str, Any]) -> str:
    """Sq ft times the rate, or the flat club amount."""
    kind = str(line.get("kind") or "")
    area = line.get("area_or_quantity")
    rate = line.get("rate")
    if kind == "maintenance" and area not in (None, "") and rate not in (None, ""):
        return f"{_quantity(area)} sq ft x Rs {_plain(rate)}"
    if kind == "club":
        return _rupees(line["taxable_amount"])
    return ""


def _tax_cell(line: dict[str, Any]) -> str:
    """Tax amount, with the percent when the line has tax."""
    amount = _rupees(line["tax_amount"])
    percent = _percent(line.get("taxable_amount"), line.get("tax_amount"))
    if percent is None:
        return amount
    return f"{percent}% · {amount}"


def _shared_percent(invoice: dict[str, Any]) -> str | None:
    """One percent for the totals when every taxed line uses it."""
    found: set[str] = set()
    for line in invoice.get("lines") or []:
        percent = _percent(line.get("taxable_amount"), line.get("tax_amount"))
        if percent:
            found.add(percent)
    if len(found) == 1:
        return found.pop()
    return None


def _percent(taxable: Any, tax_amount: Any) -> str | None:
    """Percent implied by the taxable amount and the tax, such as 18."""
    base = Decimal(str(taxable or "0"))
    tax = Decimal(str(tax_amount or "0"))
    if base <= 0 or tax <= 0:
        return None
    rate = (tax * Decimal("100") / base).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rate:.2f}".rstrip("0").rstrip(".")


def _quantity(value: Any) -> str:
    """Area with Indian grouping, such as 1,245."""
    amount = Decimal(str(value))
    if amount == amount.to_integral():
        return _group(int(amount))
    whole = int(amount)
    fraction = f"{(amount - whole):.2f}"[1:]
    return f"{_group(whole)}{fraction}"


def _plain(value: Any) -> str:
    """A rate with trailing zeros removed, such as 4.34."""
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def format_rupees(value: Any) -> str:
    """Indian-grouped rupees with paise, such as Rs 7,595.00."""
    return _rupees(value)


def _rupees(value: Any) -> str:
    """Indian-grouped rupees with paise, such as Rs 7,595.00."""
    amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    whole = int(amount)
    paise = int((amount - whole) * 100)
    return f"{sign}Rs {_group(whole)}.{paise:02d}"


def _group(whole: int) -> str:
    """Indian digit grouping."""
    digits = str(whole)
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while head:
        parts.append(head[-2:])
        head = head[:-2]
    return ",".join(reversed(parts)) + "," + tail


def _month_label(value: Any) -> str:
    """October 2026 from a billing-month date."""
    day = _as_date(value)
    return f"{_MONTHS[day.month - 1]} {day.year}"


def _long_date(value: Any) -> str:
    """5 October 2026 from an invoice or due date."""
    day = _as_date(value)
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def _as_date(value: Any) -> date:
    """Accept a date or an ISO date string."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _fit(value: str, size: int, limit: float) -> str:
    """Shorten text that would run into the next column."""
    if _text_width(value, size) <= limit:
        return value
    trimmed = value
    while trimmed and _text_width(f"{trimmed}...", size) > limit:
        trimmed = trimmed[:-1]
    return f"{trimmed}..."


def _text_width(value: str, size: int, *, bold: bool = False) -> float:
    """Approximate Helvetica width so amounts can sit on the right."""
    units = sum(_WIDTHS.get(char, 520) for char in value)
    width = units * size / 1000
    return width * 1.06 if bold else width


def _fill(
    x: float,
    y: float,
    width: float,
    height: float,
    rgb: tuple[float, float, float],
) -> list[str]:
    """A filled rectangle."""
    red, green, blue = rgb
    colour = f"{red:.3f} {green:.3f} {blue:.3f} rg"
    box = f"{x:.1f} {y:.1f} {width:.1f} {height:.1f} re f"
    return [colour, box]


def _rule(start: float, y_pos: float, end: float) -> str:
    """A hairline across the page."""
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
    """One text run."""
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
    """Right-align one text run."""
    width = _text_width(value, size, bold=font == "F2")
    return _text(right - width, y, value, size, font, rgb)


def _escape(value: str) -> str:
    """Escape text for a PDF literal string."""
    cleaned = value.encode("latin-1", errors="replace").decode("latin-1")
    return cleaned.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_document(commands: list[str]) -> bytes:
    """A single A4 page with Helvetica and Helvetica-Bold."""
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
    """Write a PDF 1.4 file with a xref table."""
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
