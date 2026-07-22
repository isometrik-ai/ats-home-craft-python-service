"""Map project property types to fee configuration tabs."""

from __future__ import annotations

from apps.user_service.app.schemas.enums import PropertyType, UnitConfigKind

PROPERTY_TYPE_TO_UNIT_CONFIG_KIND: dict[str, UnitConfigKind] = {
    PropertyType.RESIDENTIAL.value: UnitConfigKind.APARTMENT,
    PropertyType.COMMERCIAL.value: UnitConfigKind.COMMERCIAL,
    PropertyType.PLOTS.value: UnitConfigKind.PLOT,
}


def applicable_unit_config_kinds(property_types: list[str] | None) -> list[UnitConfigKind]:
    """Return fee tabs required for the project's property types."""
    kinds: list[UnitConfigKind] = []
    for prop in property_types or []:
        kind = PROPERTY_TYPE_TO_UNIT_CONFIG_KIND.get(prop)
        if kind and kind not in kinds:
            kinds.append(kind)
    return kinds
