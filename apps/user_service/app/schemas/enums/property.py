"""Enumeration values for property domain."""

from enum import Enum


class PropertyProjectStatus(str, Enum):
    """Project lifecycle status (Postgres project_status enum)."""

    ACTIVE = "active"
    ONBOARDING = "onboarding"
    SUSPENDED = "suspended"


class ProjectMemberRole(str, Enum):
    """Staff role on a project (Postgres project_member_role enum)."""

    COMMUNITY_ADMIN = "community_admin"
    SECURITY = "security"
    ACCOUNTANT = "accountant"
    FACILITY_MANAGER = "facility_manager"
    VIEWER = "viewer"


class ProjectMemberStatus(str, Enum):
    """Project member assignment status."""

    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"


class PropertyType(str, Enum):
    """Property type for a project (Postgres property_type enum)."""

    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    PLOTS = "plots"


class MeasurementUnit(str, Enum):
    """Primary measurement unit (Postgres measurement_unit enum)."""

    SQ_FT = "sq_ft"
    SQ_M = "sq_m"
    GAJ = "gaj"


class ProjectSetupStep(str, Enum):
    """Project setup wizard step keys (Postgres project_setup_step enum)."""

    PROJECT_BASICS = "project_basics"
    TOWER_BUILDER = "tower_builder"
    APARTMENT_CONFIG = "apartment_config"
    COMMERCIAL_CONFIG = "commercial_config"
    PLOT_CONFIG = "plot_config"
    INVENTORIES = "inventories"
    FACILITIES = "facilities"
    FLOOR_PLANS = "floor_plans"
    SITE_MAP = "site_map"


class ProjectMediaKind(str, Enum):
    """Project media kind (Postgres project_media_kind enum)."""

    COVER_IMAGE = "cover_image"
    LOGO = "logo"
    VIDEO = "video"
    SITE_MAP = "site_map"


class ConfigMediaKind(str, Enum):
    """Config media kind (Postgres config_media_kind enum)."""

    FLOOR_PLAN = "floor_plan"
    LAYOUT_PLAN = "layout_plan"
    UNIT_DRAWING = "unit_drawing"


class TowerType(str, Enum):
    """Tower type (Postgres tower_type enum)."""

    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    CLUBHOUSE = "clubhouse"
    MIXED = "mixed"


class UnitNumberingPattern(str, Enum):
    """Unit numbering pattern (Postgres unit_numbering_pattern enum)."""

    FLOOR_UNIT = "floor_unit"
    SEQUENTIAL = "sequential"
    CUSTOM = "custom"


class GateType(str, Enum):
    """Gate type (Postgres gate_type enum)."""

    ENTRY = "entry"
    EXIT = "exit"
    BOTH = "both"


class GateStatus(str, Enum):
    """Gate status (Postgres gate_status enum)."""

    ACTIVE = "active"
    INACTIVE = "inactive"


class LiftType(str, Enum):
    """Lift type (Postgres lift_type enum)."""

    PASSENGER = "passenger"
    SERVICE = "service"
    FREIGHT = "freight"


class LiftStatus(str, Enum):
    """Lift status (Postgres lift_status enum)."""

    OPERATIONAL = "operational"
    MAINTENANCE = "maintenance"
    INACTIVE = "inactive"


class UnitConfigKind(str, Enum):
    """Unit configuration kind (Postgres unit_config_kind enum)."""

    APARTMENT = "apartment"
    COMMERCIAL = "commercial"
    PLOT = "plot"


class Facing(str, Enum):
    """Compass facing (Postgres facing enum)."""

    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"
    NORTH_EAST = "north_east"
    NORTH_WEST = "north_west"
    SOUTH_EAST = "south_east"
    SOUTH_WEST = "south_west"


class CommercialUnitType(str, Enum):
    """Commercial unit type (Postgres commercial_unit_type enum)."""

    RETAIL_SHOP = "retail_shop"
    OFFICE = "office"
    FOOD_COURT = "food_court"
    ANCHOR_STORE = "anchor_store"
    CLINIC = "clinic"
    KIOSK = "kiosk"
    WAREHOUSE = "warehouse"
    OTHER = "other"


class PlotType(str, Enum):
    """Plot type (Postgres plot_type enum)."""

    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    VILLA = "villa"


class PlotItemStatus(str, Enum):
    """Plot item status (Postgres plot_item_status enum)."""

    EMPTY = "empty"
    UNDER_CONSTRUCTION = "under_construction"
    CONSTRUCTED = "constructed"


class UnitStatus(str, Enum):
    """Unit status (Postgres unit_status enum)."""

    VACANT = "vacant"
    OCCUPIED = "occupied"
    UNDER_MAINTENANCE = "under_maintenance"
    BLOCKED = "blocked"


class FacilityStatus(str, Enum):
    """Facility status (Postgres facility_status enum)."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    UNDER_MAINTENANCE = "under_maintenance"


class FacilityLocationType(str, Enum):
    """Facility location type (Postgres facility_location_type enum)."""

    OUTDOOR_STANDALONE = "outdoor_standalone"
    INDOOR_CLUBHOUSE = "indoor_clubhouse"
    IN_TOWER = "in_tower"
    OTHER = "other"


class FacilityType(str, Enum):
    """Facility category (stored as lowercase text on public.facilities.facility_type)."""

    SPORTS = "sports"
    RECREATION = "recreation"
    EVENTS = "events"
    SERVICES = "services"
    UTILITY = "utility"
    PARKING = "parking"


class ParkingUserType(str, Enum):
    """Parking facility audience (Postgres parking_user_type enum)."""

    RESIDENT = "resident"
    VISITORS = "visitors"


class ParkingVehicleCategory(str, Enum):
    """Parking facility vehicle category (Postgres parking_vehicle_category enum)."""

    TWO_WHEELER = "two_wheeler"
    FOUR_WHEELER = "four_wheeler"
    BOTH = "both"


class ParkingSlotStatus(str, Enum):
    """Individual parking slot status (Postgres parking_slot_status enum)."""

    AVAILABLE = "available"
    ASSIGNED = "assigned"
    BLOCKED = "blocked"


class ParkingAllotmentBasis(str, Enum):
    """Why a parking slot was allotted to a unit."""

    INCLUDED_WITH_UNIT = "included_with_unit"
    ADDITIONAL_CHARGEABLE = "additional_chargeable"
    TEMPORARY = "temporary"


class ParkingAllotmentStatus(str, Enum):
    """Lifecycle status of a unit parking allotment."""

    ACTIVE = "active"
    RELEASED = "released"


class ParkingSlotEventType(str, Enum):
    """Audit events on parking slots."""

    ALLOTTED = "allotted"
    RELEASED = "released"
    REASSIGNED = "reassigned"
    BLOCKED = "blocked"
    UNBLOCKED = "unblocked"


class ParkingSlotDisplayStatus(str, Enum):
    """Derived parking slot status for admin allotment UI."""

    ALLOTTED = "allotted"
    FREE = "free"
    VISITOR_POOL = "visitor_pool"
    BLOCKED = "blocked"


class ParkingFacilitySubtype(str, Enum):
    """Parking facility physical subtype (stored on facilities.facility_subtype)."""

    COVERED = "covered"
    OPEN = "open"
    BASEMENT = "basement"
    STILT = "stilt"
    PODIUM = "podium"
    EV_CHARGING = "ev_charging"


# ============================================================================
# VISITOR PASSES ENUMS — mirror Postgres visitor_passes enums
# ============================================================================


class PassType(str, Enum):
    """Visitor pass type (Postgres pass_type enum)."""

    GUEST = "guest"
    DELIVERY = "delivery"
    CAB = "cab"
    SERVICE = "service"
    DAILY_HELP = "daily_help"
    OTHER = "other"
    WALK_IN = "walk_in"  # visitor logs filter only; not stored on passes.pass_type


class PassValidityType(str, Enum):
    """Pass validity model (Postgres pass_validity_type enum)."""

    ONE_TIME = "one_time"
    RECURRING = "recurring"


class PassStatus(str, Enum):
    """Persisted pass status (Postgres pass_status enum)."""

    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PassEventType(str, Enum):
    """Pass timeline event (Postgres pass_event_type enum)."""

    CREATED = "created"
    CHECKED_IN = "checked_in"
    CHECKED_OUT = "checked_out"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    EXTENDED = "extended"


class PassActorType(str, Enum):
    """Who logged a pass event (Postgres pass_actor_type enum)."""

    RESIDENT = "resident"
    STAFF = "staff"
    SYSTEM = "system"


class PassDisplayStatus(str, Enum):
    """Derived UI bucket for a pass (not stored in DB)."""

    UPCOMING = "upcoming"
    ACTIVE = "active"
    EXPIRED = "expired"
    USED = "used"
    CANCELLED = "cancelled"


class PassListBucket(str, Enum):
    """List filter buckets for GET /passes."""

    UPCOMING = "upcoming"
    ACTIVE = "active"
    EXPIRED = "expired"


class PassEntryMethod(str, Enum):
    """How a guest was admitted at the gate (Postgres pass_entry_method enum)."""

    QR = "qr"
    CODE = "code"
    MANUAL = "manual"


class PassAccessStatus(str, Enum):
    """Gate decision at entry (Postgres pass_access_status enum)."""

    APPROVED = "approved"
    GRANTED = "granted"
    EXPIRED = "expired"
    DENIED = "denied"


class VisitorLogVisitStatus(str, Enum):
    """Unified visit row status for the admin visitor logs table."""

    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    INSIDE = "inside"
    EXITED = "exited"
    EXPIRED = "expired"
    DENIED = "denied"


class VisitorLogBucket(str, Enum):
    """Tab filter buckets for GET /visitor-logs."""

    ALL = "all"
    AWAITING_APPROVAL = "awaiting_approval"
    INSIDE_NOW = "inside_now"
    COMPLETED = "completed"
    DENIED_EXPIRED = "denied_expired"


class VisitorType(str, Enum):
    """High-level visitor category for admin filters (UI visitor type column)."""

    GUEST = "guest"
    VISITOR = "visitor"


# ============================================================================
