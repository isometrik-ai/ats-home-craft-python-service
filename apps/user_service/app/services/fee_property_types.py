"""Map project property types to fee configuration tabs."""

from __future__ import annotations

from apps.user_service.app.schemas.enums import PropertyType, UnitConfigKind

PROPERTY_TYPE_TO_UNIT_CONFIG_KIND: dict[str, UnitConfigKind] = {
    PropertyType.RESIDENTIAL.value: UnitConfigKind.APARTMENT,
    PropertyType.COMMERCIAL.value: UnitConfigKind.COMMERCIAL,
    PropertyType.PLOTS.value: UnitConfigKind.PLOT,
}

UNIT_CONFIG_KIND_TO_PROPERTY_TYPE: dict[str, str] = {
    UnitConfigKind.APARTMENT.value: PropertyType.RESIDENTIAL.value,
    UnitConfigKind.COMMERCIAL.value: PropertyType.COMMERCIAL.value,
    UnitConfigKind.PLOT.value: PropertyType.PLOTS.value,
}


def property_type_for_unit_config_kind(config_kind: str | None) -> str | None:
    """Map a unit config kind to its property type label."""
    if config_kind is None:
        return None
    return UNIT_CONFIG_KIND_TO_PROPERTY_TYPE.get(str(config_kind))


def applicable_unit_config_kinds(property_types: list[str] | None) -> list[UnitConfigKind]:
    """Return fee tabs required for the project's property types."""
    kinds: list[UnitConfigKind] = []
    for prop in property_types or []:
        kind = PROPERTY_TYPE_TO_UNIT_CONFIG_KIND.get(prop)
        if kind and kind not in kinds:
            kinds.append(kind)
    return kinds
