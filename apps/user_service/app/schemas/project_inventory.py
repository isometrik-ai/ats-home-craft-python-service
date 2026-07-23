"""Project inventory schemas: configs, floor_inventory, facilities, units, site map."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from apps.user_service.app.schemas.enums import (
    CommercialUnitType,
    ConfigMediaKind,
    ContactUnitRelationship,
    FacilityLocationType,
    FacilityStatus,
    Facing,
    ParkingUserType,
    PlotItemStatus,
    PlotType,
    PropertyType,
    UnitConfigKind,
    UnitStatus,
)

# ---------------------------------------------------------------------------
# Unit configs
# ---------------------------------------------------------------------------


class CreateUnitConfigRequest(BaseModel):
    """Create a unit configuration (apartment / commercial / plot)."""

    model_config = ConfigDict(extra="forbid")

    config_kind: UnitConfigKind
    name: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1, max_length=64)
    display_label: str | None = None
    active: bool = True
    sort_order: int = Field(default=0, ge=0)

    # apartment
    bedrooms: float | None = Field(default=None, ge=0)
    bathrooms: float | None = Field(default=None, ge=0)
    area_sqft: float | None = Field(default=None, ge=0)
    parking_entitlement: int = Field(default=0, ge=0)
    balconies: int = Field(default=0, ge=0)
    default_facing: Facing | None = None
    view: str | None = None

    # commercial
    commercial_unit_type: CommercialUnitType | None = None
    carpet_area_sqft: float | None = Field(default=None, ge=0)
    dimensions_ft: str | None = None
    height_ft: float | None = Field(default=None, ge=0)
    power_load_kw: float | None = Field(default=None, ge=0)
    has_mezzanine: bool = False
    mezzanine_area_sqft: float | None = Field(default=None, ge=0)

    # plot
    plot_type: PlotType | None = None
    facing: Facing | None = None
    latitude: float | None = None
    longitude: float | None = None


class UpdateUnitConfigRequest(BaseModel):
    """Patch a unit configuration (config_kind is immutable)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    code: str | None = Field(default=None, min_length=1, max_length=64)
    display_label: str | None = None
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)
    bedrooms: float | None = Field(default=None, ge=0)
    bathrooms: float | None = Field(default=None, ge=0)
    area_sqft: float | None = Field(default=None, ge=0)
    parking_entitlement: int | None = Field(default=None, ge=0)
    balconies: int | None = Field(default=None, ge=0)
    default_facing: Facing | None = None
    view: str | None = None
    commercial_unit_type: CommercialUnitType | None = None
    carpet_area_sqft: float | None = Field(default=None, ge=0)
    dimensions_ft: str | None = None
    height_ft: float | None = Field(default=None, ge=0)
    power_load_kw: float | None = Field(default=None, ge=0)
    has_mezzanine: bool | None = None
    mezzanine_area_sqft: float | None = Field(default=None, ge=0)
    plot_type: PlotType | None = None
    facing: Facing | None = None
    latitude: float | None = None
    longitude: float | None = None


class CreatePlotConfigItemRequest(BaseModel):
    """Create a plot item under a plot configuration."""

    model_config = ConfigDict(extra="forbid")

    plot_no: str = Field(..., min_length=1)
    size_sqft: float = Field(..., ge=0)
    description: str | None = Field(default=None, max_length=500)
    status: PlotItemStatus = PlotItemStatus.EMPTY
    is_corner: bool = False
    sort_order: int = Field(default=0, ge=0)


class ConfigMediaRequest(BaseModel):
    """Store config media metadata as provided in the payload."""

    model_config = ConfigDict(extra="forbid")

    kind: ConfigMediaKind
    path: str = Field(..., min_length=1)
    mime: str = Field(..., min_length=1)
    size_bytes: int = Field(..., ge=0)
    original_name: str | None = None
    sort_order: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Floor inventory
# ---------------------------------------------------------------------------


class FloorInventoryItem(BaseModel):
    """A single floor x config quantity cell."""

    model_config = ConfigDict(extra="forbid")

    tower_id: str
    floor_id: str
    config_id: str
    quantity: int = Field(..., ge=0)


class UpsertFloorInventoryRequest(BaseModel):
    """Upsert the floor inventory matrix for a project."""

    model_config = ConfigDict(extra="forbid")

    items: list[FloorInventoryItem] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Facilities
# ---------------------------------------------------------------------------


class CreateFacilityRequest(BaseModel):
    """Create a facility/amenity."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    status: FacilityStatus = FacilityStatus.ACTIVE
    facility_type: str = Field(..., min_length=1)
    facility_subtype: str | None = None
    location_type: FacilityLocationType
    tower_id: str | None = None
    floor_level: str | None = None
    wing: str | None = Field(default=None, max_length=100)
    area_sqft: float | None = Field(default=None, ge=0)
    capacity_persons: int | None = Field(default=None, ge=0)
    parking_slots: int | None = Field(default=None, ge=0)
    parking_user_type: ParkingUserType | None = None
    extra_attributes: dict[str, Any] = Field(default_factory=dict)
    location_notes: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    active: bool = True
    sort_order: int = Field(default=0, ge=0)


class UpdateFacilityRequest(BaseModel):
    """Patch a facility."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1)
    status: FacilityStatus | None = None
    facility_type: str | None = Field(default=None, min_length=1)
    facility_subtype: str | None = None
    location_type: FacilityLocationType | None = None
    tower_id: str | None = None
    floor_level: str | None = None
    wing: str | None = Field(default=None, max_length=100)
    area_sqft: float | None = Field(default=None, ge=0)
    capacity_persons: int | None = Field(default=None, ge=0)
    parking_slots: int | None = Field(default=None, ge=0)
    parking_user_type: ParkingUserType | None = None
    extra_attributes: dict[str, Any] | None = None
    location_notes: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Units + parking zones
# ---------------------------------------------------------------------------


class CreateUnitRequest(BaseModel):
    """Create a unit."""

    model_config = ConfigDict(extra="forbid")

    tower_id: str | None = None
    wing_id: str | None = None
    floor_id: str | None = None
    config_id: str | None = None
    code: str = Field(..., min_length=1, max_length=64)
    unit_label: str | None = None
    status: UnitStatus = UnitStatus.VACANT
    sort_order: int = Field(default=0, ge=0)
    is_parking: bool = False
    plot_item_id: str | None = None


class UpdateUnitRequest(BaseModel):
    """Patch a unit."""

    model_config = ConfigDict(extra="forbid")

    tower_id: str | None = None
    wing_id: str | None = None
    floor_id: str | None = None
    config_id: str | None = None
    code: str | None = Field(default=None, min_length=1, max_length=64)
    unit_label: str | None = None
    status: UnitStatus | None = None
    sort_order: int | None = Field(default=None, ge=0)
    is_parking: bool | None = None
    plot_item_id: str | None = None


class UnitListOwner(BaseModel):
    """Owner summary on a unit list row."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str | None = None
    display_name: str | None = None
    phone: str | None = None
    email: str | None = None


class ReassignUnitOwnerRequest(BaseModel):
    """Replace the Owner on a unit with another contact."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str
    is_primary: bool = True
    relationship: ContactUnitRelationship = ContactUnitRelationship.SELF


class UnitOwnerChangeResponse(BaseModel):
    """Result of unassigning or reassigning a unit owner."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    status: str | None = None
    contact_id: str | None = None
    previous_contact_id: str | None = None
    released_contact_unit_ids: list[str] = Field(default_factory=list)
    unit_status: str


class UnitListItemResponse(BaseModel):
    """Unit row for the project unit registry table."""

    model_config = ConfigDict(extra="forbid")

    id: str
    code: str
    unit_label: str | None = None
    location_label: str | None = None
    property_type: str | None = None
    config_kind: str | None = None
    floor_level_number: int | None = None
    floor_display_name: str | None = None
    config_display_label: str | None = None
    tower_id: str | None = None
    config_id: str | None = None
    owner: UnitListOwner | None = None
    status: UnitStatus
    sort_order: int = Field(default=0, ge=0)


class UnitListSummary(BaseModel):
    """Aggregate counts for the unit registry header cards."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(..., ge=0)
    sold_count: int = Field(..., ge=0)
    unsold_count: int = Field(..., ge=0)


class ListProjectUnitsFilterQuery(BaseModel):
    """Shared filter params for unit registry list and summary."""

    model_config = ConfigDict(extra="forbid")

    search: str | None = Field(default=None, min_length=1)
    property_type: PropertyType | None = None
    tower_id: str | None = None
    config_id: str | None = None
    status: UnitStatus | None = None


class ListProjectUnitsQuery(ListProjectUnitsFilterQuery):
    """Query params for the project unit registry list."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class CreateParkingZoneRequest(BaseModel):
    """Create a parking zone."""

    model_config = ConfigDict(extra="forbid")

    tower_id: str
    floor_id: str
    name: str = Field(..., min_length=1)
    slot_from: int | None = None
    slot_to: int | None = None
    sort_order: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Site map
# ---------------------------------------------------------------------------


class CreateSiteMapOverlayRequest(BaseModel):
    """Create a site map overlay marker at a geo coordinate."""

    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(..., min_length=1)
    entity_id: str
    latitude: float
    longitude: float
    label: str | None = None


class CreateSiteMapOverlaysRequest(BaseModel):
    """Create one or more site map overlay markers in a single request."""

    model_config = ConfigDict(extra="forbid")

    items: list[CreateSiteMapOverlayRequest] = Field(..., min_length=1, max_length=200)


class UpdateProjectLocationRequest(BaseModel):
    """Patch project lat/lng (site map step)."""

    model_config = ConfigDict(extra="forbid")

    latitude: float
    longitude: float


# ---------------------------------------------------------------------------
# Inventory summary (post-setup inventory menu)
# ---------------------------------------------------------------------------


class InventorySummaryHeader(BaseModel):
    """Aggregated counts for the inventory page header."""

    model_config = ConfigDict(extra="forbid")

    buildings: int = Field(..., ge=0)
    apartments: int = Field(..., ge=0)
    commercial: int = Field(..., ge=0)
    plots: int = Field(..., ge=0)
    sold_count: int = Field(..., ge=0)
    unsold_count: int = Field(..., ge=0)
    sold_percent: int = Field(..., ge=0, le=100)


class InventorySummaryBuilding(BaseModel):
    """Tower row for the buildings sidebar."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    code: str
    tower_type: str
    upper_floor_count: int = Field(..., ge=0)
    basement_count: int = Field(..., ge=0)
    units_per_floor_default: int | None = Field(default=None, ge=0)
    unit_count: int = Field(..., ge=0)
    sold_count: int = Field(..., ge=0)
    unsold_count: int = Field(..., ge=0)
    active: bool = True


class InventorySummaryUnit(BaseModel):
    """Slim unit row for occupancy grid rendering."""

    model_config = ConfigDict(extra="forbid")

    id: str
    code: str
    tower_id: str | None = None
    floor_id: str | None = None
    config_id: str | None = None
    config_kind: str | None = None
    status: str
    sort_order: int = Field(..., ge=0)
    is_parking: bool = False
    plot_item_id: str | None = None


class InventorySummaryFloor(BaseModel):
    """Floor row grouped under a tower."""

    model_config = ConfigDict(extra="forbid")

    id: str
    level_number: int
    display_name: str
    sort_order: int = Field(..., ge=0)
    is_parking: bool = False


class InventorySummaryPlotItem(BaseModel):
    """Plot item with optional linked unit status."""

    model_config = ConfigDict(extra="forbid")

    id: str
    plot_no: str
    size_sqft: float = Field(..., ge=0)
    description: str | None = None
    status: str
    is_corner: bool = False
    sort_order: int = Field(..., ge=0)
    unit_id: str | None = None
    unit_status: str | None = None


class InventorySummaryPlotConfig(BaseModel):
    """Plot configuration with nested plot items."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    code: str
    items: list[InventorySummaryPlotItem] = Field(default_factory=list)


class InventorySummaryResponse(BaseModel):
    """Full inventory menu payload for a project."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    header: InventorySummaryHeader
    buildings: list[InventorySummaryBuilding] = Field(default_factory=list)
    units: list[InventorySummaryUnit] = Field(default_factory=list)
    floors: dict[str, list[InventorySummaryFloor]] = Field(default_factory=dict)
    plot_configs: list[InventorySummaryPlotConfig] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Unit detail (inventory slide-out / unit registry)
# ---------------------------------------------------------------------------


class UnitDetailTower(BaseModel):
    """Tower context for a unit."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    code: str
    tower_type: str


class UnitDetailFloor(BaseModel):
    """Floor context for a unit."""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    level_number: int


class UnitDetailConfig(BaseModel):
    """Unit configuration snapshot."""

    model_config = ConfigDict(extra="forbid")

    id: str
    config_kind: str
    name: str
    code: str
    display_label: str | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    area_sqft: float | None = None
    carpet_area_sqft: float | None = None
    parking_entitlement: int = Field(default=0, ge=0)
    default_facing: str | None = None
    facing: str | None = None
    commercial_unit_type: str | None = None


class UnitDetailPlotItem(BaseModel):
    """Linked plot item when the unit represents a plot."""

    model_config = ConfigDict(extra="forbid")

    id: str
    plot_no: str
    size_sqft: float = Field(..., ge=0)
    status: str
    description: str | None = None


class UnitDetailPerson(BaseModel):
    """Active contact linked to a unit."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str
    contact_unit_id: str
    display_name: str
    contact_type: str
    relationship: str
    is_primary: bool = False
    phone: str | None = None
    email: str | None = None


class UnitDetailFinancials(BaseModel):
    """Fee and balance placeholders until billing is implemented."""

    model_config = ConfigDict(extra="forbid")

    base_fee_monthly: float | None = None
    outstanding_amount: float | None = None
    currency: str = "INR"


class UnitDetailResponse(BaseModel):
    """Full unit detail for inventory slide-out and unit registry screens."""

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    code: str
    unit_label: str | None = None
    status: str
    occupancy_label: str
    is_sold: bool
    is_parking: bool = False
    sort_order: int = Field(default=0, ge=0)
    location_label: str | None = None
    carpet_area_sqft: float | None = None
    facing: str | None = None
    parking_entitlement: int = Field(default=0, ge=0)
    parking_slots_assigned: int = Field(default=0, ge=0)
    tower: UnitDetailTower | None = None
    floor: UnitDetailFloor | None = None
    config: UnitDetailConfig | None = None
    plot_item: UnitDetailPlotItem | None = None
    owner: UnitDetailPerson | None = None
    residents: list[UnitDetailPerson] = Field(default_factory=list)
    vehicles_count: int = Field(default=0, ge=0)
    financials: UnitDetailFinancials = Field(default_factory=UnitDetailFinancials)
    created_at: str
    updated_at: str
