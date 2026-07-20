"""Contacts schemas.

These DTOs match the split schema:
- `contacts` is the person record (auth identity + person fields)
- company membership is via `contact_companies`

Contracts here are intentionally resource-specific (no legacy `clients.*` fields).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from apps.user_service.app.schemas.common import (
    AddressesUpdate,
    AddressInput,
    EducationalHistoryUpdate,
    Email,
    LeadInfo,
    NoteItem,
    Phone,
    PhoneInput,
    SocialPage,
    SocialPagesUpdate,
    Website,
    WorkHistoryUpdate,
)
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    ContactBloodGroup,
    ContactGender,
    ContactType,
)
from apps.user_service.app.schemas.list_filters import DropdownCustomFieldFilter
from apps.user_service.app.utils.common_utils import parse_flexible_date
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

FlexibleOptionalDate = Annotated[date | None, BeforeValidator(parse_flexible_date)]

if TYPE_CHECKING:
    from apps.user_service.app.schemas.companies import CreateCompanyRequest


def _validate_exactly_one_primary_phone(
    phones: list[Phone | PhoneInput],
) -> list[Phone | PhoneInput]:
    """Validate exactly one primary phone when property-management fields are used."""
    primary_count = sum(1 for phone in phones if phone.is_primary)
    if primary_count != 1:
        raise ValidationException(
            message_key="contacts.errors.exactly_one_primary_phone",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
        )
    return phones


class CommunicationPreferences(BaseModel):
    """Notification channel toggles stored on contacts.communication_preferences."""

    model_config = ConfigDict(extra="forbid")

    email: bool = Field(default=True, description="Receive email notifications.")
    sms: bool = Field(default=True, description="Receive SMS notifications.")
    push: bool = Field(default=False, description="Receive push notifications.")


class ContactLeadAssociation(BaseModel):
    """Optional lead creation/linking on contact create."""

    model_config = ConfigDict(extra="forbid")

    stage_id: str = Field(..., description="Lead pipeline stage id (UUID).")
    intake_stage: str | None = Field(
        default=None,
        max_length=255,
        description="Optional intake stage label.",
    )
    lead_score: str | None = Field(
        default=None,
        max_length=255,
        description="Optional lead score label/tier.",
    )


class ContactCompanyAssociationCreateInline(BaseModel):
    """Create a new company inline and link it during contact create."""

    model_config = ConfigDict(extra="forbid")

    company: CreateCompanyRequest = Field(
        ...,
        description="New company payload (same shape as POST /companies).",
    )
    is_primary: bool = Field(
        default=False,
        description="If true, set this contact as the company's primary contact.",
    )


class ContactCompaniesCreate(BaseModel):
    """Company association payload used during contact create.

    Mirrors the update naming, but allows only a single operation:
    - add an existing company membership (optional primary)
    - OR create exactly one new company and associate it (optional primary)
    """

    model_config = ConfigDict(extra="forbid")

    add_association: ContactCompanyAssociationAdd | None = Field(
        default=None,
        description="Associate the contact with an existing company (single).",
    )
    create_and_associate: ContactCompanyAssociationCreateInline | None = Field(
        default=None,
        description="Create exactly one new company and associate it to the contact (single).",
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "ContactCompaniesCreate":
        """Validate the payload."""
        if bool(self.add_association) == bool(self.create_and_associate):
            raise ValidationException(
                message_key="contacts.errors.invalid_company_association",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={
                    "details": "Provide exactly one of add_association or create_and_associate."
                },
            )
        if self.add_association is not None:
            cid = (self.add_association.company_id or "").strip()
            if not cid:
                raise ValidationException(
                    message_key="contacts.errors.invalid_company_association",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"details": "add_association.company_id is required."},
                )
            # Avoid mutating nested model attributes (pylint E0237 false-positive on slots).
            self.add_association = self.add_association.model_copy(update={"company_id": cid})
        return self


class CreateContactRequest(BaseModel):
    """Create a contact.

    Supports the operations from ADR:
    - contact only
    - contact + link to existing company (optionally primary)
    - contact + create new company + link (optionally primary)
    - property-management onboarding (contact_type + emails jsonb)
    """

    model_config = ConfigDict(extra="forbid")

    contact_type: ContactType | None = Field(
        None,
        description="Property contact type (Owner, Tenant, etc.). Required for onboarding flows.",
    )
    # core identity/person fields
    email: str | None = Field(None, description="Contact email address (CRM create).")
    portal_access: bool = Field(
        default=False,
        description="If true, provisions a portal user for this contact and sends an invite email.",
    )
    prefix: str | None = Field(None, max_length=50)
    first_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    title: str | None = Field(None, max_length=100)
    date_of_birth: FlexibleOptionalDate = None
    profile_photo_url: str | None = Field(None, max_length=500)
    gender: ContactGender | None = Field(None, description="Optional gender.")
    blood_group: ContactBloodGroup | None = Field(None, description="Optional blood group.")
    communication_preferences: CommunicationPreferences = Field(
        default_factory=CommunicationPreferences,
        description="Notification channel preferences.",
    )
    external_contact_id: str | None = Field(
        default=None,
        max_length=255,
        description="Optional identifier from an external system (immutable after create).",
    )

    phones: list[Phone] = Field(default_factory=list, max_length=20)
    emails: list[Email] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=50)
    social_pages: list[SocialPage] = Field(default_factory=list, max_length=20)
    websites: list[Website] = Field(
        default_factory=list,
        max_length=10,
        description="Websites for the contact (stored in additional_data.websites).",
    )

    custom_fields: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Custom fields root cells payload (validated and stored as JSONB).",
    )
    additional_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSONB payload stored on the contact.",
    )
    notes: list[NoteItem] = Field(default_factory=list, description="Structured notes")

    lead: ContactLeadAssociation | None = Field(
        default=None,
        description=(
            "Optional lead creation. When provided, creates a lead and associates it with "
            "this contact (and the linked company if provided)."
        ),
    )

    # optional company association at create-time (single op wrapper)
    company_association: ContactCompaniesCreate | None = Field(
        default=None,
        description="Optional company association (add existing OR create+associate).",
    )

    # optional addresses created on contact
    addresses: list[AddressInput] = Field(default_factory=list, max_length=50)

    @field_validator("phones")
    @classmethod
    def validate_property_primary_phone(
        cls, phones: list[PhoneInput | Phone], info
    ) -> list[PhoneInput | Phone]:
        """Require exactly one primary phone for property-management creates."""
        contact_type = info.data.get("contact_type")
        if contact_type is not None and phones:
            return _validate_exactly_one_primary_phone(phones)
        return phones

    @model_validator(mode="after")
    def validate_email_for_create(self) -> "CreateContactRequest":
        """Ensure CRM creates have email; property flows may use emails jsonb only."""
        if self.contact_type is not None:
            if not self.phones:
                raise ValidationException(
                    message_key="contacts.errors.exactly_one_primary_phone",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return self
        if not (self.email or "").strip():
            raise ValidationException(
                message_key="contacts.errors.email_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        return self


class CreateContactRequestStandalone(BaseModel):
    """Create a contact without allowing nested lead creation."""

    model_config = ConfigDict(extra="forbid")

    # core identity/person fields
    email: str = Field(..., description="Contact email address (required).")
    portal_access: bool = Field(
        default=False,
        description="If true, provisions a portal user for this contact and sends an invite email.",
    )
    prefix: str | None = Field(None, max_length=50)
    first_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    title: str | None = Field(None, max_length=100)
    date_of_birth: FlexibleOptionalDate = None
    profile_photo_url: str | None = Field(None, max_length=500)
    external_contact_id: str | None = Field(
        default=None,
        max_length=255,
        description="Optional identifier from an external system (immutable after create).",
    )

    phones: list[PhoneInput] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=50)
    social_pages: list[SocialPage] = Field(default_factory=list, max_length=20)
    websites: list[Website] = Field(
        default_factory=list,
        max_length=10,
        description="Websites for the contact (stored in additional_data.websites).",
    )

    custom_fields: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Custom fields root cells payload (validated and stored as JSONB).",
    )
    additional_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form JSONB payload stored on the contact.",
    )
    notes: list[NoteItem] = Field(default_factory=list, description="Structured notes")

    # optional company association at create-time
    company_association: ContactCompaniesCreate | None = None

    # optional addresses created on contact
    addresses: list[AddressInput] = Field(default_factory=list, max_length=50)


class ListContactsRequest(BaseModel):
    """Request body for listing contacts (DB-backed)."""

    model_config = ConfigDict(extra="forbid")

    search: str | None = Field(default=None, min_length=2)
    status: ClientStatus | None = None
    contact_type: ContactType | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    dropdown_filters: list[DropdownCustomFieldFilter] = Field(default_factory=list)


class GetContactsByIdsRequest(BaseModel):
    """Request body for bulk contact lookup by id."""

    model_config = ConfigDict(extra="forbid")

    contact_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Contact identifiers (UUID strings).",
    )


class ContactBasicInfoResponse(BaseModel):
    """Minimal contact fields for bulk lookup."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    email: str | None = None
    external_contact_id: str | None = None


class UpdateContactRequest(BaseModel):
    """Patch a contact (contacts table) and/or manage associations."""

    model_config = ConfigDict(extra="forbid")

    status: ClientStatus | None = None
    contact_type: ContactType | None = None
    portal_access: bool | None = None
    prefix: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    date_of_birth: FlexibleOptionalDate = None
    profile_photo_url: str | None = None
    gender: ContactGender | None = None
    blood_group: ContactBloodGroup | None = None
    communication_preferences: CommunicationPreferences | None = None
    phones: list[Phone] | None = None
    emails: list[Email] | None = None
    tags: list[str] | None = None
    social_pages: SocialPagesUpdate | None = None
    custom_fields: list[dict[str, Any]] | None = None
    additional_data: dict[str, Any] | None = None
    sales_intelligence: dict[str, Any] | None = None
    description: str | None = None
    notes: list[NoteItem] | None = None

    # person enrichment/profile fields (same storage columns as ContactDetailsResponse)
    work_history: WorkHistoryUpdate | None = None
    educational_history: EducationalHistoryUpdate | None = None
    skills: list[str] | None = None

    # contact address delta
    addresses: AddressesUpdate | None = None

    # preferred company association delta (batch-friendly)
    company_association: ContactCompanyUpdate | None = None


class ContactCompanyAssociationAdd(BaseModel):
    """Add an existing company membership for a contact."""

    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(..., description="Existing company id to link")
    is_primary: bool = Field(
        default=False,
        description="If true, set this contact as the company's primary contact.",
    )


class ContactCompanyAssociationCreate(BaseModel):
    """Create exactly one new company and link it to the contact."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="New company name to create")
    is_primary: bool = Field(
        default=False,
        description="If true, set this contact as the new company's primary contact.",
    )


class ContactCompanyAssociationUpdate(BaseModel):
    """Update per-company attributes for this contact relationship."""

    model_config = ConfigDict(extra="forbid")

    company_id: str = Field(..., description="Company id to update relationship for")
    is_primary: bool = Field(
        ...,
        description=(
            "If true, set this contact as the company's primary contact. "
            "If false, unset this contact as primary for that company (keeps membership)."
        ),
    )


class ContactCompanyUpdate(BaseModel):
    """Batch company association changes for a contact (developer-friendly, low round trips).

    Supports in one request:
    - remove association with N companies
    - add association with N existing companies (optionally setting primary per company)
    - create exactly 1 new company and link it (optional primary)
    """

    model_config = ConfigDict(extra="forbid")

    remove_associations: list[str] = Field(
        default_factory=list,
        description="Company ids to unlink from the contact",
    )
    add_associations: list[ContactCompanyAssociationAdd] = Field(
        default_factory=list,
        description="Associate the contact with existing companies (by id)",
    )
    update_associations: list[ContactCompanyAssociationUpdate] = Field(
        default_factory=list,
        description="Update primary status per company without unlinking",
    )
    create_and_associate: ContactCompanyAssociationCreate | None = Field(
        default=None,
        description="Create exactly one new company and associate it to the contact",
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "ContactCompanyUpdate":
        """Validate the payload."""
        remove_ids = [c.strip() for c in (self.remove_associations or []) if (c or "").strip()]
        self.remove_associations = remove_ids

        # validate create.name
        if self.create_and_associate is not None:
            name = (self.create_and_associate.name or "").strip()
            if not name:
                raise ValueError("create_and_associate.name is required.")

        # normalize connect company_id strings
        normalized_add: list[ContactCompanyAssociationAdd] = []
        for item in self.add_associations or []:
            cid = (item.company_id or "").strip()
            if not cid:
                raise ValueError("add_associations.company_id is required.")
            normalized_add.append(
                ContactCompanyAssociationAdd(company_id=cid, is_primary=bool(item.is_primary))
            )
        self.add_associations = normalized_add

        normalized_update: list[ContactCompanyAssociationUpdate] = []
        for item in self.update_associations or []:
            cid = (item.company_id or "").strip()
            if not cid:
                raise ValueError("update_associations.company_id is required.")
            normalized_update.append(
                ContactCompanyAssociationUpdate(company_id=cid, is_primary=bool(item.is_primary))
            )
        self.update_associations = normalized_update

        # require at least one operation
        if (
            not self.remove_associations
            and not self.add_associations
            and not self.update_associations
            and self.create_and_associate is None
        ):
            raise ValueError("Provide at least one operation in company_association.")

        return self


class ContactSummaryResponse(BaseModel):
    """Contact list/search item."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    status: ClientStatus
    contact_type: str | None = None
    portal_access: bool = True
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    email: str | None = None
    profile_photo_url: str | None = None
    external_contact_id: str | None = None
    phones: list[dict[str, Any]] = Field(default_factory=list)
    company_names: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ContactCountsResponse(BaseModel):
    """Overview card counts for the Contacts registry dashboard."""

    model_config = ConfigDict(extra="ignore")

    total: int = 0
    owners: int = 0
    tenants: int = 0
    vendors: int = 0


class ContactDetailsResponse(BaseModel):
    """Contact detail response."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    status: ClientStatus
    contact_type: str | None = None
    portal_access: bool = True
    user_id: str | None = None
    isometrik_user_id: str | None = None

    prefix: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    date_of_birth: date | None = None
    profile_photo_url: str | None = None
    gender: str | None = None
    blood_group: str | None = None
    communication_preferences: dict[str, Any] = Field(default_factory=dict)
    email: str | None = None
    external_contact_id: str | None = None
    phones: list[dict[str, Any]] = Field(default_factory=list)
    emails: list[Any] = Field(default_factory=list)

    tags: list[str] = Field(default_factory=list)
    custom_fields: list[dict[str, Any]] = Field(default_factory=list)
    additional_data: dict[str, Any] = Field(default_factory=dict)
    sales_intelligence: dict[str, Any] | None = None
    social_pages: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[NoteItem] = Field(default_factory=list)

    work_history: list[dict[str, Any]] = Field(default_factory=list)
    educational_history: list[dict[str, Any]] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)

    enrichment_done: bool = False
    enrichment_status: str | None = None
    last_enriched_at: str | None = None

    companies: list[dict[str, Any]] = Field(
        default_factory=list,
        description="All linked companies with is_primary flag",
    )
    leads: list[LeadInfo] = Field(
        default_factory=list,
        description="Leads linked to this contact via lead_contacts",
    )
    addresses: list[dict[str, Any]] = Field(default_factory=list)

    created_at: str
    updated_at: str


def _rebuild_cross_schema_models() -> None:
    """Resolve forward references across contacts <-> companies without import cycles."""
    # Local import to keep module import order flexible.
    import importlib

    companies_module = importlib.import_module("apps.user_service.app.schemas.companies")

    ContactCompanyAssociationCreateInline.model_rebuild(
        _types_namespace={
            # `CreateCompanyRequest` transitively references `CreateContactRequest` via
            # `CompanyContactLink`, so we must provide both names here.
            "CreateCompanyRequest": companies_module.CreateCompanyRequest,
            "CreateContactRequest": CreateContactRequest,
        }
    )
    companies_module.CompanyContactAssociationCreate.model_rebuild(
        _types_namespace={"CreateContactRequest": CreateContactRequest}
    )
    companies_module.CompanyContactsCreate.model_rebuild(
        _types_namespace={"CreateContactRequest": CreateContactRequest}
    )


_rebuild_cross_schema_models()
