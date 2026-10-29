"""Byte-mode QR matrix, PNG, and HTML helpers (no extra dependencies)."""

from __future__ import annotations

import struct
import zlib

# Version → (size, data codewords, ecc per block, block count). ECC-M, equal blocks.
_VERSIONS: tuple[tuple[int, int, int, int], ...] = (
    (21, 16, 10, 1),
    (25, 28, 16, 1),
    (29, 44, 26, 1),
    (33, 64, 18, 2),
    (37, 86, 24, 2),
    (41, 108, 16, 4),
)

_ALIGN: dict[int, tuple[int, ...]] = {
    25: (18,),
    29: (22,),
    33: (26,),
    37: (30,),
    41: (34,),
}

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def qr_matrix(data: str) -> list[list[int]]:
    """Return a square 0/1 matrix (1 = dark) for *data*."""
    payload = data.encode("utf-8")
    size, data_cw, ecc_len, blocks = _pick_version(len(payload))
    codewords = _encode_bytes(payload, data_cw)
    ecc_blocks = _interleave(codewords, data_cw, ecc_len, blocks)
    return _place(size, ecc_blocks)


def qr_png_bytes(data: str, *, scale: int = 6, border: int = 4) -> bytes:
    """PNG of the QR so it can be attached to mail."""
    return matrix_to_png(qr_matrix(data), scale=scale, border=border)


def qr_html_table(data: str, *, module_px: int = 4) -> str:
    """Nested-table QR that many mail clients can render without images."""
    matrix = qr_matrix(data)
    size = len(matrix)
    dim = (size + 8) * module_px
    rows: list[str] = []
    for y in range(-4, size + 4):
        cells: list[str] = []
        for x in range(-4, size + 4):
            dark = 0 <= y < size and 0 <= x < size and matrix[y][x]
            color = "#111827" if dark else "#ffffff"
            cells.append(
                f'<td width="{module_px}" height="{module_px}" '
                f'style="width:{module_px}px;height:{module_px}px;background:{color};'
                f'padding:0;line-height:0;font-size:0;">&nbsp;</td>'
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<table width="{dim}" cellpadding="0" cellspacing="0" border="0" '
        f'role="presentation" style="border-collapse:collapse;margin:0 auto;">'
        + "".join(rows)
        + "</table>"
    )


def matrix_to_png(matrix: list[list[int]], *, scale: int = 6, border: int = 4) -> bytes:
    """Encode a 0/1 matrix as an 8-bit grayscale PNG."""
    size = len(matrix)
    dim = (size + border * 2) * scale
    raw = bytearray()
    for y in range(dim):
        raw.append(0)
        my = y // scale - border
        for x in range(dim):
            mx = x // scale - border
            dark = 0 <= my < size and 0 <= mx < size and matrix[my][mx]
            raw.append(0x11 if dark else 0xFF)
    compressed = zlib.compress(bytes(raw), 9)
    ihdr = struct.pack(">IIBBBBB", dim, dim, 8, 0, 0, 0, 0)

    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


def _pick_version(byte_len: int) -> tuple[int, int, int, int]:
    """Choose the smallest ECC-M version that fits the payload."""
    need = byte_len + 2
    for size, data_cw, ecc_len, blocks in _VERSIONS:
        if need + 1 <= data_cw:
            return size, data_cw, ecc_len, blocks
    raise ValueError("QR payload is too long")


def _gf_mul(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    return _EXP[_LOG[left] + _LOG[right]]


def _rs_generator(degree: int) -> list[int]:
    poly = [1]
    for i in range(degree):
        next_poly = [0] * (len(poly) + 1)
        factor = _EXP[i]
        for idx, coeff in enumerate(poly):
            next_poly[idx] ^= _gf_mul(coeff, factor)
            next_poly[idx + 1] ^= coeff
        poly = next_poly
    return poly


def _rs_encode(data: list[int], degree: int) -> list[int]:
    gen = _rs_generator(degree)
    ecc = [0] * degree
    for byte in data:
        factor = byte ^ ecc[0]
        ecc = ecc[1:] + [0]
        for idx, coeff in enumerate(gen[1:]):
            ecc[idx] ^= _gf_mul(coeff, factor)
    return ecc


def _encode_bytes(payload: bytes, data_cw: int) -> list[int]:
    bits: list[int] = [0, 1, 0, 0]
    count = len(payload)
    bits.extend((count >> shift) & 1 for shift in range(7, -1, -1))
    for byte in payload:
        bits.extend((byte >> shift) & 1 for shift in range(7, -1, -1))
    bits.extend([0] * min(4, data_cw * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    codewords = []
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits[i : i + 8]:
            value = (value << 1) | bit
        codewords.append(value)
    pad = (0xEC, 0x11)
    idx = 0
    while len(codewords) < data_cw:
        codewords.append(pad[idx % 2])
        idx += 1
    return codewords[:data_cw]


def _interleave(
    codewords: list[int],
    data_cw: int,
    ecc_len: int,
    blocks: int,
) -> list[int]:
    block_data = data_cw // blocks
    data_blocks = [codewords[i * block_data : (i + 1) * block_data] for i in range(blocks)]
    ecc_blocks = [_rs_encode(block, ecc_len) for block in data_blocks]
    interleaved: list[int] = []
    for col in range(block_data):
        for block in data_blocks:
            interleaved.append(block[col])
    for col in range(ecc_len):
        for block in ecc_blocks:
            interleaved.append(block[col])
    return interleaved


def _finder(matrix: list[list[int]], row: int, col: int) -> None:
    size = len(matrix)
    for dy in range(-1, 8):
        for dx in range(-1, 8):
            rr, cc = row + dy, col + dx
            if 0 <= rr < size and 0 <= cc < size:
                matrix[rr][cc] = 0
    for dy in range(7):
        for dx in range(7):
            on_ring = dx in (0, 6) or dy in (0, 6)
            in_center = 2 <= dx <= 4 and 2 <= dy <= 4
            matrix[row + dy][col + dx] = int(on_ring or in_center)


def _alignment(matrix: list[list[int]], row: int, col: int) -> None:
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            matrix[row + dy][col + dx] = int(
                dx in (-2, 2) or dy in (-2, 2) or (dx == 0 and dy == 0)
            )


def _reserved(size: int) -> list[list[int]]:
    mark = [[0] * size for _ in range(size)]

    def stamp(r: int, c: int, h: int, w: int) -> None:
        for rr in range(r, r + h):
            for cc in range(c, c + w):
                if 0 <= rr < size and 0 <= cc < size:
                    mark[rr][cc] = 1

    stamp(0, 0, 9, 9)
    stamp(0, size - 8, 9, 8)
    stamp(size - 8, 0, 8, 9)
    stamp(6, 0, 1, size)
    stamp(0, 6, size, 1)
    for pos in _ALIGN.get(size, ()):
        stamp(pos - 2, pos - 2, 5, 5)
    return mark


def _place(size: int, codewords: list[int]) -> list[list[int]]:
    matrix = [[0] * size for _ in range(size)]
    reserved = _reserved(size)
    _finder(matrix, 0, 0)
    _finder(matrix, 0, size - 7)
    _finder(matrix, size - 7, 0)
    for i in range(8, size - 8):
        matrix[6][i] = int(i % 2 == 0)
        matrix[i][6] = int(i % 2 == 0)
    for pos in _ALIGN.get(size, ()):
        _alignment(matrix, pos, pos)

    bits: list[int] = []
    for byte in codewords:
        bits.extend((byte >> shift) & 1 for shift in range(7, -1, -1))

    bit_i = 0
    going_up = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if going_up else range(size)
        for row in rows:
            for dx in (0, -1):
                cc = col + dx
                if reserved[row][cc]:
                    continue
                bit = bits[bit_i] if bit_i < len(bits) else 0
                bit_i += 1
                if (row + cc) % 2 == 0:
                    bit ^= 1
                matrix[row][cc] = bit
        going_up = not going_up
        col -= 2

    _draw_format(matrix)
    return matrix


def _format_bits() -> int:
    """ECC-M + mask 0, BCH-protected and masked (0x5412)."""
    data = 0
    remainder = data << 10
    for i in range(4, -1, -1):
        if (remainder >> (i + 10)) & 1:
            remainder ^= 0x537 << i
    return ((data << 10) | (remainder & 0x3FF)) ^ 0x5412


def _draw_format(matrix: list[list[int]]) -> None:
    """Write format bits in both copies. Bit 0 is the LSB."""
    bits = _format_bits()
    size = len(matrix)
    coords_a = (
        [(8, n) for n in range(6)] + [(8, 7), (8, 8), (7, 8)] + [(n, 8) for n in range(5, -1, -1)]
    )
    coords_b = [(size - 1 - i, 8) for i in range(8)] + [(8, size - 15 + i) for i in range(8, 15)]
    for idx in range(15):
        bit = (bits >> idx) & 1
        row, col = coords_a[idx]
        matrix[row][col] = bit
        row, col = coords_b[idx]
        matrix[row][col] = bit
    matrix[size - 8][8] = 1
