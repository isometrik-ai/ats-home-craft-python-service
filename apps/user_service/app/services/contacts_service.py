"""Contacts service aligned with public.contacts."""

from __future__ import annotations

import re
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
from apps.user_service.app.schemas.common import Email, Phone
from apps.user_service.app.schemas.contacts import (
    CreateContactRequest,
    UpdateContactRequest,
)
from apps.user_service.app.schemas.enums import (
    ContactStatus,
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
from libs.shared_db.supabase_db.auth_repository import (
    create_user,
    get_user_by_id,
    update_phone,
)
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


def _normalize_phone_item(phone: Any) -> dict[str, Any]:
    """Normalize phone item."""
    if isinstance(phone, Phone):
        return phone.model_dump()
    if isinstance(phone, dict):
        return phone
    return {}


def _normalize_full_phone(phone_isd_code: str, phone_number: str) -> str:
    """Combine ISD + number and strip formatting characters for E.164 storage."""
    combined = f"{phone_isd_code or ''}{phone_number or ''}".strip()
    digits = re.sub(r"\D", "", combined)
    return digits if digits else ""


def _get_primary_email(emails: list[Email] | None) -> str | None:
    """Return normalized primary email when one is marked is_primary."""
    for item in emails or []:
        if item.is_primary:
            normalized = item.email.strip().lower()
            return normalized or None
    return None


def _get_primary_phone_identity(phones: list[Any] | None) -> tuple[str, str] | None:
    """Return (phone_isd_code, phone_number) for the primary phone, if any."""
    for phone in phones or []:
        item = _normalize_phone_item(phone)
        if item.get("is_primary"):
            return (
                str(item.get("phone_isd_code") or ""),
                str(item.get("phone_number") or ""),
            )
    return None


def _primary_phone_changed(old_phones: Any, new_phones: list[Any]) -> bool:
    """True when the primary phone assignment or number changed."""
    old_primary = _get_primary_phone_identity(parse_json_any(old_phones, default=[]))
    new_primary = _get_primary_phone_identity(new_phones)
    return old_primary != new_primary


def _serialize_contact_update_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """Normalize enum and JSONB list fields in a contact update payload."""
    if "contact_type" in patch and isinstance(patch["contact_type"], ContactType):
        patch["contact_type"] = patch["contact_type"].value
    if "status" in patch and hasattr(patch["status"], "value"):
        patch["status"] = patch["status"].value
    if "gender" in patch:
        patch["gender"] = patch["gender"].value
    if "blood_group" in patch:
        patch["blood_group"] = patch["blood_group"].value
    if "communication_preferences" in patch:
        patch["communication_preferences"] = parse_json_any(
            patch["communication_preferences"],
            default=patch["communication_preferences"],
        )
    for key in ("emails", "notes", "social_pages", "websites"):
        if key in patch:
            patch[key] = _serialize_jsonb_list(patch[key])
    return patch


def _contact_phone_sync_info(
    *,
    current: dict[str, Any],
    phones: list[Phone],
) -> tuple[bool, Phone | None]:
    """Return whether auth phone should sync and the new primary phone."""
    sync_auth_phone = bool(current.get("user_id")) and _primary_phone_changed(
        current.get("phones"), phones
    )
    primary_phone = next(phone for phone in phones if phone.is_primary) if sync_auth_phone else None
    return sync_auth_phone, primary_phone


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
        if details.get("portal_access") is None:
            details["portal_access"] = True
        if details.get("gender") is not None:
            details["gender"] = str(details["gender"])
        if details.get("blood_group") is not None:
            details["blood_group"] = str(details["blood_group"])
        if details.get("communication_preferences") is not None:
            details["communication_preferences"] = parse_json_any(
                details["communication_preferences"],
                default=details["communication_preferences"],
            )
        return details

    def _summary_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Get summary from contact row."""
        return {
            "id": str(row["id"]),
            "organization_id": str(row["organization_id"]),
            "status": row.get("status"),
            "contact_type": row.get("contact_type"),
            "portal_access": bool(row.get("portal_access", True)),
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
        phone: str,
        email: str | None = None,
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
        auth_matches = await user_repo.get_auth_users_by_phone_or_email(
            phone=phone,
            email=email,
        )
        matched_user_ids = {
            str(match["id"]) for match in auth_matches if match.get("id") is not None
        }

        created_password: str | None = None
        if len(matched_user_ids) > 1:
            raise ConflictException(
                message_key="contacts.errors.primary_email_phone_auth_mismatch",
                custom_code=CustomStatusCode.CONFLICT,
            )
        if len(matched_user_ids) == 1:
            user_id = next(iter(matched_user_ids))
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
                email=email,
                phone=phone,
                password=password,
                user_metadata=user_metadata,
                email_confirm=True,
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

    async def _sync_contact_auth_phone(self, *, user_id: str, phone: Phone) -> None:
        """Update linked Supabase auth user when the contact primary phone changes."""
        if not self.supabase_client:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

        full_phone = _normalize_full_phone(phone.phone_isd_code, phone.phone_number)
        user_repo = UserRepository(db_connection=self.db_connection)
        existing_user = await user_repo.get_auth_user_by_phone(full_phone)
        if existing_user and str(existing_user["id"]) != user_id:
            raise ConflictException(
                message_key="clients.errors.phone_number_already_exists",
                custom_code=CustomStatusCode.CONFLICT,
            )

        auth_user = await get_user_by_id(self.supabase_client, user_id)
        if not auth_user:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

        updated = await update_phone(
            self.supabase_client,
            user_id,
            auth_user.get("user_metadata") or {},
            phone.phone_number,
            phone.phone_isd_code,
        )
        if not updated:
            raise ServiceUnavailableException(
                message_key="contacts.errors.auth_user_creation_failed",
                custom_code=CustomStatusCode.EXTERNAL_SERVICE_ERROR,
            )

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

        primary_email = _get_primary_email(body.emails)

        (
            user_id,
            isometrik_user_id,
            _,
        ) = await self._provision_contact_auth_identity(
            contact_id=contact_id,
            phone=_normalize_full_phone(phone.phone_isd_code, phone.phone_number),
            email=primary_email,
            first_name=body.first_name,
            last_name=body.last_name,
            prefix=body.prefix,
        )

        contact_row = {
            "id": contact_id,
            "organization_id": org_id,
            "user_id": user_id,
            "isometrik_user_id": isometrik_user_id,
            "status": ContactStatus.ACTIVE.value,
            "contact_type": body.contact_type.value,
            "portal_access": body.portal_access,
            "prefix": body.prefix,
            "first_name": body.first_name,
            "middle_name": body.middle_name,
            "last_name": body.last_name,
            "title": body.title,
            "date_of_birth": body.date_of_birth,
            "gender": body.gender.value if body.gender else None,
            "blood_group": body.blood_group.value if body.blood_group else None,
            "communication_preferences": body.communication_preferences.model_dump()
            if body.communication_preferences
            else {},
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
        if "portal_access" in body.model_fields_set:
            patch["portal_access"] = body.portal_access

        if not patch:
            return {"old_data": current, "new_data": self._normalize_details(current)}

        patch = _serialize_contact_update_patch(patch)

        sync_auth_phone = False
        primary_phone: Phone | None = None
        if "phones" in patch and body.phones is not None:
            patch["phones"] = _serialize_jsonb_list(body.phones)
            sync_auth_phone, primary_phone = _contact_phone_sync_info(
                current=current,
                phones=body.phones,
            )

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
        if sync_auth_phone and primary_phone is not None:
            await self._sync_contact_auth_phone(
                user_id=str(current["user_id"]),
                phone=primary_phone,
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
