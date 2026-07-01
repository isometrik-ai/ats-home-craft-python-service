"""Contacts service aligned with public.contacts."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import asyncpg
from asyncpg import UniqueViolationError
from supabase import AsyncClient

from apps.user_service.app.db.repositories.contacts_repository import ContactsRepository
from apps.user_service.app.db.repositories.organization_repository import (
    OrganizationRepository,
)
from apps.user_service.app.db.repositories.user_repository import UserRepository
from apps.user_service.app.schemas.common import Phone
from apps.user_service.app.schemas.contacts import (
    CreateContactRequest,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import (
    ClientStatus,
    ContactType,
    EntityType,
    IsometrikRole,
)
from apps.user_service.app.services.custom_field_service import CustomFieldService
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    generate_random_password,
    parse_json_any,
    parse_json_field,
)
from libs.shared_db.supabase_db.auth_repository import create_user
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
    ValidationException,
)
from libs.shared_utils.isometrik_service import (
    create_isometrik_user,
    get_isometrik_data_from_settings,
)
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("contacts_service")


def _serialize_jsonb_list(items: list[Any] | None) -> list[dict[str, Any]]:
    """Serialize pydantic models or dicts for JSONB list columns."""
    out: list[dict[str, Any]] = []
    for item in items or []:
        if hasattr(item, "model_dump"):
            out.append(item.model_dump(exclude_none=True))
        elif isinstance(item, dict):
            out.append(item)
    return out


class ContactsService:
    """Business logic for contacts."""

    def __init__(
        self,
        *,
        db_connection: asyncpg.Connection,
        user_context: UserContext,
        supabase_client: AsyncClient | None = None,
    ) -> None:
        self.db_connection = db_connection
        self.user_context = user_context
        self.supabase_client = supabase_client
        self.contacts_repo = ContactsRepository(db_connection)
        self.org_repo = OrganizationRepository(db_connection)

    def _normalize_details(self, row: dict[str, Any]) -> dict[str, Any]:
        """Normalize contact details."""
        details = dict(row)
        for key in ("id", "organization_id", "user_id", "isometrik_user_id"):
            if details.get(key) is not None:
                details[key] = str(details[key])
        if isinstance(details.get("date_of_birth"), date):
            details["date_of_birth"] = details["date_of_birth"].isoformat()
        for ts_key in ("created_at", "updated_at"):
            details[ts_key] = format_iso_datetime(details.get(ts_key))
        if details.get("tags") is None:
            details["tags"] = []
        return details

    def _summary_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Get summary from contact row."""
        return {
            "id": str(row["id"]),
            "organization_id": str(row["organization_id"]),
            "status": row.get("status"),
            "contact_type": row.get("contact_type"),
            "first_name": row.get("first_name"),
            "last_name": row.get("last_name"),
            "title": row.get("title"),
            "profile_photo_url": row.get("profile_photo_url"),
            "phones": parse_json_any(row.get("phones"), default=[]),
            "emails": parse_json_any(row.get("emails"), default=[]),
            "tags": list(row.get("tags") or []),
            "created_at": format_iso_datetime(row.get("created_at")),
            "updated_at": format_iso_datetime(row.get("updated_at")),
        }

    async def _validate_custom_fields(
        self,
        payload: list[dict[str, Any]] | None,
        *,
        stored: Any = None,
    ) -> list[dict[str, Any]]:
        """Validate custom fields."""
        if not payload:
            return []
        cfs = CustomFieldService(
            db_connection=self.db_connection,
            user_context=self.user_context,
        )
        if stored is not None:
            return await cfs.merge_for_update(payload, stored, EntityType.CONTACT)
        return await cfs.validate_for_create(payload, EntityType.CONTACT)

    async def _provision_contact_auth_identity(
        self,
        *,
        contact_id: str,
        phone: Phone,
        first_name: str | None,
        last_name: str | None,
        prefix: str | None,
    ) -> tuple[str, str | None, str | None]:
        """Provision contact auth identity."""
        if not self.supabase_client:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

        org_id = self.user_context.organization_id
        organization = await self.org_repo.get_organization_by_id(org_id)
        if not organization:
            raise NotFoundException(
                message_key="organizations.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        user_repo = UserRepository(db_connection=self.db_connection)
        phone_number = phone.phone_isd_code + phone.phone_number
        existing_user = await user_repo.get_auth_user_by_phone(phone_number)
        created_password: str | None = None
        if existing_user and existing_user.get("id"):
            user_id = str(existing_user["id"])
        else:
            password = generate_random_password()
            created_password = password
            user_metadata: dict[str, Any] = {
                "timezone": "UTC",
                "first_name": first_name,
                "last_name": last_name,
            }
            if prefix:
                user_metadata["salutation"] = prefix
            auth_user = await create_user(
                sb_client=self.supabase_client,
                phone=phone_number.strip(),
                password=password,
                user_metadata=user_metadata,
            )
            if not auth_user or not auth_user.get("id"):
                raise ServiceUnavailableException(
                    message_key="contacts.errors.auth_user_creation_failed",
                    custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
                )
            user_id = str(auth_user["id"])

        org_settings = parse_json_field(organization.get("settings"))
        isometrik_credentials = get_isometrik_data_from_settings(org_settings)
        isometrik_response = await create_isometrik_user(
            user={
                "user_id": contact_id,
                "organization_id": org_id,
                "role": IsometrikRole.CLIENT.value,
                "first_name": first_name,
                "last_name": last_name,
            },
            isometrik_credentials=isometrik_credentials,
        )
        isometrik_user_id = (
            str(isometrik_response["userId"])
            if isometrik_response and isometrik_response.get("userId")
            else None
        )
        if isometrik_credentials and not isometrik_user_id:
            raise ServiceUnavailableException(
                message_key="contacts.errors.isometrik_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )
        return user_id, isometrik_user_id, created_password

    async def create_contact(self, body: CreateContactRequest) -> dict[str, Any]:
        """Create a contact."""
        org_id = self.user_context.organization_id
        validated_custom_fields = await self._validate_custom_fields(body.custom_fields)

        contact_id = str(uuid.uuid4())
        user_id: str | None = None
        isometrik_user_id: str | None = None

        phone = next((phone for phone in body.phones if phone.is_primary), None)

        if not phone:
            raise ValidationException(
                message_key="contacts.errors.exactly_one_primary_phone",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        if body.portal_access:
            (
                user_id,
                isometrik_user_id,
                _,
            ) = await self._provision_contact_auth_identity(
                contact_id=contact_id,
                phone=phone,
                first_name=body.first_name,
                last_name=body.last_name,
                prefix=body.prefix,
            )

        contact_row = {
            "id": contact_id,
            "organization_id": org_id,
            "user_id": user_id,
            "isometrik_user_id": isometrik_user_id,
            "status": ClientStatus.ACTIVE.value,
            "contact_type": body.contact_type.value,
            "prefix": body.prefix,
            "first_name": body.first_name,
            "middle_name": body.middle_name,
            "last_name": body.last_name,
            "title": body.title,
            "date_of_birth": body.date_of_birth,
            "profile_photo_url": body.profile_photo_url,
            "phones": _serialize_jsonb_list(body.phones),
            "emails": _serialize_jsonb_list(body.emails),
            "tags": body.tags,
            "custom_fields": validated_custom_fields,
            "additional_data": body.additional_data,
            "social_pages": _serialize_jsonb_list(body.social_pages),
            "documents": body.documents,
            "description": body.description,
            "websites": _serialize_jsonb_list(body.websites),
            "notes": _serialize_jsonb_list(body.notes),
        }

        try:
            inserted = await self.contacts_repo.insert_contact(contact_row)
        except UniqueViolationError as exc:
            if getattr(exc, "constraint_name", None) == "uq_contacts_user_org":
                raise ConflictException(
                    message_key="contacts.errors.contact_user_already_exists",
                    custom_code=CustomStatusCode.CONFLICT,
                ) from exc
            raise

        await self.org_repo.get_organization_by_id(org_id)
        # if body.portal_access and organization:
        #     try:
        #         send_client_creation_email(
        #             email=email_norm,
        #             organization_name=str(organization.get("name") or ""),
        #             password=created_password,
        #         )
        #     except Exception as send_error:
        #         logger.error("Failed to send contact creation email: %s", send_error)

        return {
            "contact_id": contact_id,
            "old_data": None,
            "new_data": self._normalize_details(inserted),
        }

    async def update_contact(
        self, *, contact_id: str, body: UpdateContactRequest
    ) -> dict[str, Any]:
        """Update a contact."""
        # pylint: disable=too-complex
        org_id = self.user_context.organization_id
        current = await self.contacts_repo.get_contact_for_update(
            contact_id=contact_id,
            organization_id=org_id,
        )
        if not current:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        patch = body.model_dump(exclude_unset=True, exclude_none=True)
        if not patch:
            return {"old_data": current, "new_data": self._normalize_details(current)}

        if "emails" in patch:
            patch["emails"] = _serialize_jsonb_list(body.emails)

        if "contact_type" in patch and isinstance(patch["contact_type"], ContactType):
            patch["contact_type"] = patch["contact_type"].value
        if "status" in patch and hasattr(patch["status"], "value"):
            patch["status"] = patch["status"].value
        if "phones" in patch:
            patch["phones"] = _serialize_jsonb_list(patch["phones"])
        if "notes" in patch:
            patch["notes"] = _serialize_jsonb_list(patch["notes"])
        if "social_pages" in patch:
            patch["social_pages"] = _serialize_jsonb_list(patch["social_pages"])
        if "websites" in patch:
            patch["websites"] = _serialize_jsonb_list(patch["websites"])
        if "custom_fields" in patch:
            patch["custom_fields"] = await self._validate_custom_fields(
                patch["custom_fields"],
                stored=current.get("custom_fields"),
            )

        updated = await self.contacts_repo.update_contact(
            contact_id=contact_id,
            organization_id=org_id,
            update_data=patch,
        )
        if not updated:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return {
            "old_data": current,
            "new_data": self._normalize_details(updated),
        }

    async def get_contact_details(self, *, contact_id: str) -> dict[str, Any]:
        """Get contact details."""
        org_id = self.user_context.organization_id
        row = await self.contacts_repo.get_contact_details(
            contact_id=contact_id,
            organization_id=org_id,
        )
        if not row:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._normalize_details(row)

    async def list_contacts(
        self,
        *,
        search: str | None,
        status: str | None,
        contact_type: str | None,
        dropdown_filters: list[dict[str, str]] | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """List contacts."""
        rows, total = await self.contacts_repo.list_contacts(
            organization_id=self.user_context.organization_id,
            search=search,
            status=status,
            contact_type=contact_type,
            dropdown_filters=dropdown_filters,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._summary_from_row(row) for row in rows],
            "total": total,
        }

    async def soft_delete_contact(self, *, contact_id: str) -> dict[str, Any]:
        """Soft delete a contact."""
        org_id = self.user_context.organization_id
        current = await self.contacts_repo.get_contact_for_update(
            contact_id=contact_id,
            organization_id=org_id,
        )
        if not current:
            raise NotFoundException(
                message_key="contacts.errors.contact_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        updated = await self.contacts_repo.soft_delete_contact(
            contact_id=contact_id,
            organization_id=org_id,
        )
        return {"old_data": current, "new_data": updated}
