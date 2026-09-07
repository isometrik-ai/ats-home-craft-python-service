"""Work order domain enums."""

from enum import Enum


class RecordStatus(str, Enum):
    """Soft-delete status for work order entities."""

    ACTIVE = "active"
    DELETED = "deleted"


class AssetStatus(str, Enum):
    """Operational status of a project asset."""

    OPERATIONAL = "operational"
    UNDER_REPAIR = "under_repair"
    FAULTY = "faulty"
    DECOMMISSIONED = "decommissioned"


class ContractStatus(str, Enum):
    """Lifecycle status of a maintenance contract."""

    ACTIVE = "active"
    EXPIRED = "expired"
    TERMINATED = "terminated"
    PAUSED = "paused"


class VisitFrequency(str, Enum):
    """How often contract visits recur."""

    DAILY = "daily"
    WEEKLY = "weekly"
    FORTNIGHTLY = "fortnightly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    YEARLY = "yearly"


class PaymentFrequency(str, Enum):
    """How often contract payments recur."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    HALF_YEARLY = "half_yearly"
    YEARLY = "yearly"
    ONE_TIME = "one_time"


class WorkOrderState(str, Enum):
    """Lifecycle state of a work order."""

    UPCOMING = "upcoming"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class WorkOrderPriority(str, Enum):
    """Priority level assigned to a work order."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class WorkOrderSource(str, Enum):
    """Origin of a work order record."""

    CONTRACT = "contract"
    AD_HOC = "ad_hoc"
    RECURRING = "recurring"


class InvoiceStatus(str, Enum):
    """Approval and payment status of an invoice."""

    SUBMITTED = "submitted"
    REVISION_REQUESTED = "revision_requested"
    RESUBMITTED = "resubmitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAID = "paid"


class PaymentMethod(str, Enum):
    """Method used to settle an invoice payment."""

    BANK_TRANSFER = "bank_transfer"
    CHEQUE = "cheque"
    CASH = "cash"
    UPI = "upi"
    OTHER = "other"


class PaymentStatus(str, Enum):
    """Processing status of a payment record."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    VOIDED = "voided"


class TriggerEntity(str, Enum):
    """Entity type observed by integration triggers."""

    WORK_ORDER = "work_order"
    CONTRACT = "contract"
    INVOICE = "invoice"
    PAYMENT = "payment"


class TriggerEvent(str, Enum):
    """Event type emitted to integration webhooks."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    STATUS_CHANGED = "status_changed"
