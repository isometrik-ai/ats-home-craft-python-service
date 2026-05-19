"""Companies schemas.

These DTOs match the split schema:
- `companies` is the company record
- primary contact is `companies.primary_contact_id` (nullable FK -> contacts)
- memberships are via `contact_companies`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from apps.user_service.app.schemas.common import (
    AddressesUpdate,
    AddressInput,
    BillingPreferences,
    BillingPreferencesUpdate,
    KeyPeopleUpdate,
    LeadInfo,
    LinkedPagesUpdate,
    NoteItem,
    Phone,
    ProductsUpdate,
    SocialPage,
    SocialPagesUpdate,
    Website,
    WebsitesUpdate,
)
from apps.user_service.app.schemas.enums import ClientStatus
from apps.user_service.app.schemas.list_filters import DropdownCustomFieldFilter
from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

if TYPE_CHECKING:
    from apps.user_service.app.schemas.contacts import CreateContactRequest


class ListCompaniesRequest(BaseModel):
    """Request body for listing companies (DB-backed)."""

    model_config = ConfigDict(extra="forbid")

    search: str | None = Field(default=None, min_length=2)
    status: ClientStatus | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    dropdown_filters: list[DropdownCustomFieldFilter] = Field(default_factory=list)


class CompanyLeadAssociation(BaseModel):
    """Optional lead creation/linking on company create.

    Creates a new lead (v2 `public.leads`) and associates it with the created company,
    and also with the linked/created contact when present on the same request.
    """

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


class CreateCompanyRequest(BaseModel):
    """Create a company.

    Supports ADR operations:
    - company only
    - company + link existing contact (primary/non-primary)
    - company + create new contact + link (primary/non-primary)
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., max_length=200)
    industry: str | None = Field(None, max_length=100)
    profile_photo_url: str | None = Field(None, max_length=500)
    portal_access: bool = False
    email: str | None = Field(None, max_length=320)
    phones: list[Phone] = Field(default_factory=list, max_length=20)

    tags: list[str] = Field(default_factory=list, max_length=50)
    websites: list[Website] = Field(default_factory=list, max_length=10)
    billing_preferences: BillingPreferences | None = Field(
        default_factory=BillingPreferences,
        description="Billing preferences (defaults to empty preferences).",
    )
    social_pages: list[SocialPage] = Field(default_factory=list, max_length=20)

    target_market_segments: list[str] = Field(default_factory=list, max_length=50)
    current_tech_stack: list[str] = Field(default_factory=list, max_length=50)
    preferred_communication_channels: list[str] = Field(default_factory=list, max_length=20)
    industry_specific_terminologies: list[str] = Field(default_factory=list, max_length=100)
    description: str | None = Field(None, max_length=10000)

    custom_fields: list[dict[str, Any]] = Field(default_factory=list)
    additional_data: dict[str, Any] = Field(default_factory=dict)
    notes: list[NoteItem] = Field(default_factory=list, description="Structured notes")

    # optional lead create + association
    lead: CompanyLeadAssociation | None = Field(
        default=None,
        description=(
            "Optional lead creation. When provided, creates a lead and associates it with "
            "this company (and the linked contact if provided)."
        ),
    )

    # Developer-friendly association input (one contact operation on create).
    contact_association: CompanyContactsCreate | None = Field(
        default=None,
        description="Optional contact association (add existing OR create+associate).",
    )

    addresses: list[AddressInput] = Field(default_factory=list, max_length=50)


class CompanyContactAssociationAdd(BaseModel):
    """Add an existing contact membership for a company."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str = Field(..., description="Existing contact id to link")
    is_primary: bool = Field(
        default=False,
        description="If true, set this contact as the company's primary contact.",
    )


class CompanyContactAssociationCreate(BaseModel):
    """Create exactly one new contact and link it to the company."""

    model_config = ConfigDict(extra="forbid")

    contact: "CreateContactRequest" = Field(
        ...,
        description="New contact payload (same shape as POST /contacts).",
    )
    is_primary: bool = Field(
        default=False,
        description="If true, set the new contact as the company's primary contact.",
    )


class CompanyContactAssociationUpdate(BaseModel):
    """Update primary status for a contact on this company (membership unchanged)."""

    model_config = ConfigDict(extra="forbid")

    contact_id: str = Field(..., description="Contact id to update relationship for")
    is_primary: bool = Field(
        ...,
        description=(
            "If true, set this contact as the company's primary contact. "
            "If false, clear primary when this contact is currently primary (keeps membership)."
        ),
    )


class CompanyContactUpdate(BaseModel):
    """Batch contact association changes for a company.

    Mirrors ``ContactCompaniesUpdate`` on PATCH ``/contacts``.

    In one request:
    - remove membership for N contacts
    - add membership for N existing contacts (optional primary per contact; only one may be primary)
    - update primary flag per contact without unlinking
    - create exactly one new contact and link (optional primary)
    """

    model_config = ConfigDict(extra="forbid")

    remove_associations: list[str] = Field(
        default_factory=list,
        description="Contact ids to unlink from the company",
    )
    add_associations: list[CompanyContactAssociationAdd] = Field(
        default_factory=list,
        description="Associate existing contacts with this company (by id)",
    )
    update_associations: list[CompanyContactAssociationUpdate] = Field(
        default_factory=list,
        description="Update primary status per contact without unlinking",
    )
    create_and_associate: CompanyContactAssociationCreate | None = Field(
        default=None,
        description="Create exactly one new contact and associate it to the company",
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "CompanyContactUpdate":
        """Normalize ids and ensure at least one batch operation is present."""
        remove_ids = [
            entry.strip() for entry in (self.remove_associations or []) if (entry or "").strip()
        ]
        self.remove_associations = remove_ids

        normalized_add: list[CompanyContactAssociationAdd] = []
        for item in self.add_associations or []:
            contact_identifier = (item.contact_id or "").strip()
            if not contact_identifier:
                raise ValueError("add_associations.contact_id is required.")
            normalized_add.append(
                CompanyContactAssociationAdd(
                    contact_id=contact_identifier,
                    is_primary=bool(item.is_primary),
                )
            )
        self.add_associations = normalized_add

        normalized_update: list[CompanyContactAssociationUpdate] = []
        for item in self.update_associations or []:
            contact_identifier = (item.contact_id or "").strip()
            if not contact_identifier:
                raise ValueError("update_associations.contact_id is required.")
            normalized_update.append(
                CompanyContactAssociationUpdate(
                    contact_id=contact_identifier,
                    is_primary=bool(item.is_primary),
                )
            )
        self.update_associations = normalized_update

        if (
            not self.remove_associations
            and not self.add_associations
            and not self.update_associations
            and self.create_and_associate is None
        ):
            raise ValueError("Provide at least one operation in contact_association.")

        return self


class CompanyContactsCreate(BaseModel):
    """Contact association payload used during company create.

    Mirrors the update naming, but allows only a single operation:
    - add an existing contact membership (optional primary)
    - OR create exactly one new contact and associate it (optional primary)
    """

    model_config = ConfigDict(extra="forbid")

    add_association: CompanyContactAssociationAdd | None = Field(
        default=None,
        description="Associate an existing contact to this company (single).",
    )
    create_and_associate: CompanyContactAssociationCreate | None = Field(
        default=None,
        description="Create exactly one new contact and associate it to the company (single).",
    )

    @model_validator(mode="after")
    def validate_payload(self) -> "CompanyContactsCreate":
        """Validate mutual exclusivity and normalize nested ids."""
        if bool(self.add_association) == bool(self.create_and_associate):
            raise ValidationException(
                message_key="companies.errors.invalid_contact_association",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={
                    "details": "Provide exactly one of add_association or create_and_associate."
                },
            )
        if self.add_association is not None:
            contact_id = (self.add_association.contact_id or "").strip()
            if not contact_id:
                raise ValidationException(
                    message_key="companies.errors.invalid_contact_association",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"details": "add_association.contact_id is required."},
                )
            # Avoid mutating nested model attributes (pylint E0237 false-positive on slots).
            self.add_association = self.add_association.model_copy(
                update={"contact_id": contact_id}
            )
        return self


class UpdateCompanyRequest(BaseModel):
    """Patch a company (companies table) and/or manage contact associations."""

    model_config = ConfigDict(extra="forbid")

    status: ClientStatus | None = None
    name: str | None = Field(None, max_length=200)
    industry: str | None = Field(None, max_length=100)
    profile_photo_url: str | None = Field(None, max_length=500)
    portal_access: bool | None = None
    email: str | None = Field(None, max_length=320)
    phones: list[Phone] | None = Field(None, max_length=20)
    tags: list[str] | None = Field(None, max_length=50)

    websites: WebsitesUpdate | None = None
    billing_preferences: BillingPreferencesUpdate | None = None
    social_pages: SocialPagesUpdate | None = None
    addresses: AddressesUpdate | None = None

    target_market_segments: list[str] | None = None
    current_tech_stack: list[str] | None = None
    preferred_communication_channels: list[str] | None = None
    industry_specific_terminologies: list[str] | None = None
    description: str | None = None

    custom_fields: list[dict[str, Any]] | None = None
    additional_data: dict[str, Any] | None = None
    sales_intelligence: dict[str, Any] | None = None
    notes: list[NoteItem] | None = None

    linked_pages: LinkedPagesUpdate | None = None
    products: ProductsUpdate | None = None
    key_people: KeyPeopleUpdate | None = None

    contact_association: CompanyContactUpdate | None = None


class CompanySummaryContactItem(BaseModel):
    """Basic contact row on company list (members via contact_companies)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    first_name: str | None = None
    last_name: str | None = None
    title: str | None = None
    email: str | None = None
    phones: list[dict[str, Any]] = Field(default_factory=list)
    is_primary: bool = False


class CompanySummaryResponse(BaseModel):
    """Company list/search item."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    status: ClientStatus
    name: str
    industry: str | None = None
    profile_photo_url: str | None = None
    email: str | None = None
    phones: list[dict[str, Any]] = Field(default_factory=list)
    contacts: list[CompanySummaryContactItem] = Field(
        default_factory=list,
        description="Contacts linked to this company (basic fields)",
    )
    created_at: str
    updated_at: str


class CompanyDetailsResponse(BaseModel):
    """Company detail response."""

    model_config = ConfigDict(extra="ignore")

    id: str
    organization_id: str
    status: ClientStatus
    name: str
    industry: str | None = None
    profile_photo_url: str | None = None
    portal_access: bool = False
    email: str | None = None
    phones: list[dict[str, Any]] = Field(default_factory=list)

    primary_contact_id: str | None = None
    contacts: list[CompanySummaryContactItem] = Field(
        default_factory=list,
        description="Contacts linked to this company (same shape as list endpoint)",
    )

    tags: list[str] = Field(default_factory=list)
    websites: list[dict[str, Any]] = Field(default_factory=list)
    billing_preferences: dict[str, Any] | None = None
    social_pages: list[dict[str, Any]] = Field(default_factory=list)
    linked_pages: list[dict[str, Any]] = Field(default_factory=list)
    products: list[dict[str, Any]] = Field(default_factory=list)
    key_people: list[dict[str, Any]] = Field(default_factory=list)

    custom_fields: list[dict[str, Any]] = Field(default_factory=list)
    additional_data: dict[str, Any] = Field(default_factory=dict)
    sales_intelligence: dict[str, Any] | None = None
    notes: list[NoteItem] = Field(default_factory=list)

    target_market_segments: list[str] = Field(default_factory=list)
    current_tech_stack: list[str] = Field(default_factory=list)
    preferred_communication_channels: list[str] = Field(default_factory=list)
    industry_specific_terminologies: list[str] = Field(default_factory=list)
    description: str | None = None

    enrichment_done: bool = False
    enrichment_status: str | None = None
    enrichment_request_id: str | None = None
    last_enriched_at: str | None = None

    addresses: list[dict[str, Any]] = Field(default_factory=list)

    leads: list[LeadInfo] = Field(
        default_factory=list,
        description="Leads linked to this company via lead_companies",
    )
    created_at: str
    updated_at: str
