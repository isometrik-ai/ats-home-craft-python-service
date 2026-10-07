"""Distance helpers for marketplace nearby societies and closest sort."""

from __future__ import annotations

import math
from decimal import Decimal

MARKETPLACE_NEARBY_RADIUS_KM = 5

_EARTH_RADIUS_KM = 6371.0


def _to_float(value: float | Decimal | None) -> float | None:
    """Coerce a numeric coordinate to float."""
    if value is None:
        return None
    return float(value)


def haversine_km(
    lat1: float | Decimal | None,
    lng1: float | Decimal | None,
    lat2: float | Decimal | None,
    lng2: float | Decimal | None,
) -> float | None:
    """Great-circle distance in kilometres. None when any coordinate is missing."""
    a_lat = _to_float(lat1)
    a_lng = _to_float(lng1)
    b_lat = _to_float(lat2)
    b_lng = _to_float(lng2)
    if a_lat is None or a_lng is None or b_lat is None or b_lng is None:
        return None
    phi1 = math.radians(a_lat)
    phi2 = math.radians(b_lat)
    d_phi = math.radians(b_lat - a_lat)
    d_lambda = math.radians(b_lng - a_lng)
    heart = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(heart)))
