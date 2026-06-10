"""Email template business logic."""

from __future__ import annotations

import copy
import json
import re
import uuid
from typing import Any

import asyncpg
import httpx
from asyncpg import UniqueViolationError

from apps.user_service.app.db.repositories.email_template_repository import (
    EmailTemplateRepository,
)
from apps.user_service.app.schemas.custom_fields import (
    validate_and_normalize_type_config,
)
from apps.user_service.app.schemas.email_templates import (
    CreateEmailTemplateRequest,
    EmailTemplateDetailResponse,
    EmailTemplateListItem,
    EmailTemplateVariableAddRequest,
    EmailTemplateVariableDefinition,
    EmailTemplateVariableRequest,
    EmailTemplateVariablesMutation,
    EmailTemplateVariableUpdateRequest,
    RenderEmailTemplateRequest,
    RenderEmailTemplateResponse,
    UpdateEmailTemplateRequest,
)
from apps.user_service.app.schemas.enums import EmailTemplateType, FieldType
from apps.user_service.app.services.email_template_variable_validation import (
    EmailTemplateVariableValidator,
    collect_renderable_variables,
)
from apps.user_service.app.utils.common_utils import (
    UserContext,
    format_iso_datetime,
    parse_json_field,
)
from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.http_exceptions import (
    ConflictException,
    NotFoundException,
    ServiceUnavailableException,
    ValidationException,
)
from libs.shared_utils.isometrik_strands_client import call_strands_agent
from libs.shared_utils.logger import get_logger
from libs.shared_utils.status_codes import CustomStatusCode

logger = get_logger("email_template_service")

PLACEHOLDER_RE = re.compile(r"\{\{\.([a-z][a-z0-9_]*)\}\}")
BODY_CONTENT_TOKEN = "{{BODY_CONTENT}}"

_VARIABLE_UPDATABLE_FIELDS = (
    "variable_key",
    "field_name",
    "description",
    "field_type",
    "type_config",
    "is_required",
    "default_value",
    "sort_order",
)


class EmailTemplateService:
    """Service for email template CRUD and validation."""

    def __init__(
        self,
        db_connection: asyncpg.Connection,
        user_context: UserContext | None = None,
        repository: EmailTemplateRepository | None = None,
    ) -> None:
        """Initialize the service with request-scoped DB access and variable validation."""
        self.user_context = user_context
        self.db_connection = db_connection
        self.repository = repository or EmailTemplateRepository(db_connection)
        self.variable_validator = EmailTemplateVariableValidator(
            db_connection,
            user_context,
        )

    @staticmethod
    def _flatten_variable_keys(variables: list[EmailTemplateVariableDefinition]) -> set[str]:
        """Collect all variable_key values from a variable tree (including nested nodes)."""
        keys: set[str] = set()
        queue = list(variables)
        while queue:
            node = queue.pop(0)
            keys.add(node.variable_key)
            queue.extend(node.sub_fields)
        return keys

    @staticmethod
    def _renderable_variable_keys(variables: list[EmailTemplateVariableDefinition]) -> set[str]:
        """Collect keys that accept runtime values (not object/list containers)."""
        return {node.variable_key for node in collect_renderable_variables(variables)}

    @staticmethod
    def _validate_unique_variable_keys(variables: list[EmailTemplateVariableDefinition]) -> None:
        """Reject duplicate variable_key values anywhere in the tree."""
        seen: set[str] = set()
        queue = list(variables)
        while queue:
            node = queue.pop(0)
            if node.variable_key in seen:
                raise ValidationException(
                    message_key="email_templates.errors.duplicate_variable_key",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                    params={"variable_key": node.variable_key},
                )
            seen.add(node.variable_key)
            queue.extend(node.sub_fields)

    def _validate_variable_tree_structure(
        self,
        variables: list[EmailTemplateVariableDefinition],
    ) -> None:
        """Validate nesting depth, unique keys, and default values for the variable tree."""
        for variable in variables:
            EmailTemplateVariableDefinition.validate_nesting_depth_iterative(variable)
        EmailTemplateService._validate_unique_variable_keys(variables)
        self.variable_validator.validate_variable_tree_defaults(variables)

    @staticmethod
    def _validate_html_placeholders(
        html_content: str,
        variables: list[EmailTemplateVariableDefinition],
    ) -> None:
        """Ensure {{.variable_key}} placeholders match renderable variables bidirectionally."""
        html_keys = set(PLACEHOLDER_RE.findall(html_content))
        defined_keys = EmailTemplateService._renderable_variable_keys(variables)

        for key in sorted(defined_keys - html_keys):
            raise ValidationException(
                message_key="email_templates.errors.variable_not_in_html",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"variable_key": key},
            )
        for key in sorted(html_keys - defined_keys):
            raise ValidationException(
                message_key="email_templates.errors.placeholder_undefined_variable",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"variable_key": key},
            )

    @staticmethod
    def _validate_template_html_rules(
        template_type: EmailTemplateType,
        html_content: str,
    ) -> None:
        """Apply LAYOUT vs TRIGGER HTML constraints (BODY_CONTENT, body-inject, etc.)."""
        if template_type == EmailTemplateType.LAYOUT:
            if BODY_CONTENT_TOKEN not in html_content:
                raise ValidationException(
                    message_key="email_templates.errors.body_content_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            if 'id="body-inject"' not in html_content and "id='body-inject'" not in html_content:
                raise ValidationException(
                    message_key="email_templates.errors.body_inject_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return

        if BODY_CONTENT_TOKEN in html_content:
            raise ValidationException(
                message_key="email_templates.errors.body_content_not_allowed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

    @staticmethod
    def _format_address_for_html(value: dict[str, Any]) -> str:
        """Format a normalized address object for inline HTML display."""
        line1 = value.get("address_line1") or value.get("line1") or ""
        line2 = value.get("address_line2") or value.get("line2")
        city = value.get("city")
        state = value.get("state")
        postal_code = value.get("postal_code")
        country = value.get("country")

        city_state_postal = ", ".join(
            part for part in [city, " ".join(p for p in [state, postal_code] if p)] if part
        )
        parts = [part for part in [line1, line2, city_state_postal, country] if part]
        return ", ".join(str(part) for part in parts)

    @staticmethod
    def _format_value_for_html(value: Any) -> str:
        """Serialize a resolved variable value for HTML substitution."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, dict):
            amount = value.get("amount")
            currency_code = value.get("currency_code")
            if amount is not None and currency_code is not None:
                return f"{amount} {currency_code}"
            if any(
                key in value
                for key in ("address_line1", "line1", "city", "state", "postal_code", "country")
            ):
                return EmailTemplateService._format_address_for_html(value)
        if isinstance(value, list):
            return ", ".join(EmailTemplateService._format_value_for_html(item) for item in value)
        return str(value)

    @classmethod
    def substitute_variable_placeholders(
        cls,
        html_content: str,
        resolved_variables: dict[str, Any],
    ) -> str:
        """Replace {{.variable_key}} tokens with resolved runtime values."""
        rendered = html_content
        for key, value in resolved_variables.items():
            rendered = rendered.replace(f"{{{{.{key}}}}}", cls._format_value_for_html(value))
        rendered = PLACEHOLDER_RE.sub("", rendered)
        return rendered

    @staticmethod
    def _variables_from_row(row: dict[str, Any]) -> list[EmailTemplateVariableRequest]:
        """Load persisted variables from a DB row as validated request models."""
        stored = EmailTemplateService._parse_variables_from_row(row)
        return [EmailTemplateVariableRequest.model_validate(item) for item in stored]

    def validate_template_payload(
        self,
        *,
        template_type: EmailTemplateType,
        html_content: str,
        variables: list[EmailTemplateVariableDefinition],
    ) -> list[dict[str, Any]]:
        """Validate template body and variables; return stored JSON with backend-assigned ids."""
        self._validate_template_html_rules(template_type, html_content)
        self._validate_variable_tree_structure(variables)
        self._validate_html_placeholders(html_content, variables)
        return EmailTemplateService._definitions_to_stored_variables(variables)

    @staticmethod
    def _assign_backend_variable_ids(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Assign a new server-generated id on every node (ignores any client-supplied id)."""
        result: list[dict[str, Any]] = []
        for node in nodes:
            stored = {key: value for key, value in node.items() if key != "id"}
            stored["id"] = str(uuid.uuid4())
            sub_fields = stored.get("sub_fields") or []
            stored["sub_fields"] = EmailTemplateService._assign_backend_variable_ids(
                sub_fields if isinstance(sub_fields, list) else []
            )
            result.append(stored)
        return result

    @staticmethod
    def _ensure_storage_variable_ids(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Preserve ids from storage; generate ids only for legacy rows missing id."""
        result: list[dict[str, Any]] = []
        for node in nodes:
            stored = dict(node)
            if not stored.get("id"):
                stored["id"] = str(uuid.uuid4())
            sub_fields = stored.get("sub_fields") or []
            stored["sub_fields"] = EmailTemplateService._ensure_storage_variable_ids(
                sub_fields if isinstance(sub_fields, list) else []
            )
            result.append(stored)
        return result

    @staticmethod
    def _stored_variables_to_requests(
        stored: list[dict[str, Any]],
    ) -> list[EmailTemplateVariableRequest]:
        """Parse stored JSON nodes into models (ids must already exist in storage)."""
        with_ids = EmailTemplateService._ensure_storage_variable_ids(stored)
        return [EmailTemplateVariableRequest.model_validate(node) for node in with_ids]

    @staticmethod
    def _definitions_to_stored_variables(
        variables: list[EmailTemplateVariableDefinition],
    ) -> list[dict[str, Any]]:
        """Serialize client variable definitions and assign backend ids for persistence."""
        dumped = [variable.model_dump(mode="json") for variable in variables]
        return EmailTemplateService._assign_backend_variable_ids(dumped)

    @staticmethod
    def _collect_variable_descendant_ids(node: dict[str, Any]) -> set[str]:
        """Return the id of a node and all descendant variable ids."""
        ids = {str(node["id"])}
        for child in node.get("sub_fields") or []:
            ids |= EmailTemplateService._collect_variable_descendant_ids(child)
        return ids

    @staticmethod
    def _index_variable_nodes(
        roots: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Map variable id -> node reference (mutable dict in tree)."""
        index: dict[str, dict[str, Any]] = {}

        def walk(nodes: list[dict[str, Any]]) -> None:
            for node in nodes:
                index[str(node["id"])] = node
                walk(node.get("sub_fields") or [])

        walk(roots)
        return index

    @staticmethod
    def _raise_variable_id_not_found(variable_id: str) -> None:
        """Raise when a mutation references a missing variable id."""
        raise ValidationException(
            message_key="email_templates.errors.variable_id_not_found",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"variable_id": variable_id},
        )

    @staticmethod
    def _raise_variable_parent_not_found(variable_id: str) -> None:
        """Raise when an add operation references a missing parent id."""
        raise ValidationException(
            message_key="email_templates.errors.variable_parent_not_found",
            custom_code=CustomStatusCode.VALIDATION_ERROR,
            params={"variable_id": variable_id},
        )

    @staticmethod
    def _collect_mutation_remove_ids(
        index: dict[str, dict[str, Any]],
        remove: list[str],
    ) -> set[str]:
        """Collect ids slated for removal, including descendants."""
        remove_ids: set[str] = set()
        for remove_id in remove:
            if remove_id not in index:
                EmailTemplateService._raise_variable_id_not_found(remove_id)
            remove_ids |= EmailTemplateService._collect_variable_descendant_ids(index[remove_id])
        return remove_ids

    @staticmethod
    def _validate_mutation_add_parents(
        index: dict[str, dict[str, Any]],
        add: list[EmailTemplateVariableAddRequest],
        remove_ids: set[str],
    ) -> None:
        """Ensure add operations reference valid, surviving parent ids."""
        for add_item in add:
            parent_id = add_item.parent_id
            if parent_id is None:
                continue
            if parent_id not in index or parent_id in remove_ids:
                EmailTemplateService._raise_variable_parent_not_found(parent_id)

    @staticmethod
    def _validate_variable_mutation_ids(
        index: dict[str, dict[str, Any]],
        mutation: EmailTemplateVariablesMutation,
    ) -> None:
        """Validate update/remove/add reference existing variable ids on the template."""
        if mutation.update:
            for item in mutation.update:
                if item.id not in index:
                    EmailTemplateService._raise_variable_id_not_found(item.id)

        remove_ids = (
            EmailTemplateService._collect_mutation_remove_ids(index, mutation.remove)
            if mutation.remove
            else set()
        )

        if mutation.add:
            EmailTemplateService._validate_mutation_add_parents(index, mutation.add, remove_ids)

    @staticmethod
    def _remove_variable_nodes(
        roots: list[dict[str, Any]],
        remove_ids: set[str],
    ) -> list[dict[str, Any]]:
        """Return a copy of the tree with the given ids (and their descendants) removed."""

        def filter_children(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
            kept: list[dict[str, Any]] = []
            for node in nodes:
                if str(node["id"]) in remove_ids:
                    continue
                node["sub_fields"] = filter_children(node.get("sub_fields") or [])
                kept.append(node)
            return kept

        return filter_children(roots)

    @staticmethod
    def _variable_add_node_dict(add_item: EmailTemplateVariableAddRequest) -> dict[str, Any]:
        """Build a stored variable node from an add payload with backend-assigned ids."""
        node = add_item.model_dump(mode="json", exclude={"parent_id"})
        return EmailTemplateService._assign_backend_variable_ids([node])[0]

    @staticmethod
    def _attach_variable_child(
        roots: list[dict[str, Any]],
        parent_id: str | None,
        child: dict[str, Any],
    ) -> None:
        """Insert a new variable under a root or under an object/list parent."""
        if parent_id is None:
            roots.append(child)
            return

        parent = EmailTemplateService._index_variable_nodes(roots)[parent_id]
        parent_type = parent.get("field_type")
        if parent_type not in (FieldType.OBJECT.value, FieldType.LIST.value):
            raise ValidationException(
                message_key="email_templates.errors.variable_parent_cannot_have_children",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"variable_id": parent_id},
            )
        sub_fields = parent.setdefault("sub_fields", [])
        if parent_type == FieldType.LIST.value and len(sub_fields) >= 1:
            raise ValidationException(
                message_key="custom_fields.errors.list_must_have_exactly_one_child",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        sub_fields.append(child)

    @staticmethod
    def _apply_variable_field_type_change(
        node: dict[str, Any],
        new_type: FieldType | None,
    ) -> None:
        """Apply a field_type change and clear sub_fields when leaving object/list."""
        if new_type is None:
            return
        current = node.get("field_type")
        if current in (FieldType.OBJECT.value, FieldType.LIST.value) and new_type not in (
            FieldType.OBJECT,
            FieldType.LIST,
        ):
            node["sub_fields"] = []
        node["field_type"] = new_type.value

    @staticmethod
    def _apply_variable_updates(
        index: dict[str, dict[str, Any]],
        updates: list[EmailTemplateVariableUpdateRequest],
    ) -> None:
        """Patch stored variable nodes in place from flat update requests."""
        for item in updates:
            node = index[item.id]
            payload = item.model_dump(exclude_unset=True, exclude={"id"})
            new_field_type = payload.pop("field_type", None)
            type_config = payload.pop("type_config", None)

            if new_field_type is not None:
                EmailTemplateService._apply_variable_field_type_change(node, new_field_type)

            if type_config is not None:
                field_type = FieldType(node["field_type"])
                node["type_config"] = validate_and_normalize_type_config(field_type, type_config)
            elif new_field_type is not None:
                field_type = FieldType(node["field_type"])
                node["type_config"] = validate_and_normalize_type_config(
                    field_type,
                    node.get("type_config") or {},
                )

            for key, value in payload.items():
                if key in _VARIABLE_UPDATABLE_FIELDS:
                    node[key] = value

    @staticmethod
    def _apply_variable_mutations(
        stored_variables: list[dict[str, Any]],
        mutation: EmailTemplateVariablesMutation,
    ) -> list[dict[str, Any]]:
        """Apply remove → update → add and return the new stored variable tree."""
        roots = EmailTemplateService._ensure_storage_variable_ids(copy.deepcopy(stored_variables))
        index = EmailTemplateService._index_variable_nodes(roots)
        EmailTemplateService._validate_variable_mutation_ids(index, mutation)

        if mutation.remove:
            remove_ids: set[str] = set()
            for remove_id in mutation.remove:
                remove_ids |= EmailTemplateService._collect_variable_descendant_ids(
                    index[remove_id]
                )
            roots = EmailTemplateService._remove_variable_nodes(roots, remove_ids)
            index = EmailTemplateService._index_variable_nodes(roots)

        if mutation.update:
            EmailTemplateService._apply_variable_updates(index, mutation.update)

        if mutation.add:
            for add_item in mutation.add:
                child = EmailTemplateService._variable_add_node_dict(add_item)
                EmailTemplateService._attach_variable_child(roots, add_item.parent_id, child)

        return roots

    @staticmethod
    def _parse_variables_from_row(row: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse variables JSON from a row and ensure each node has a storage id."""
        raw = parse_json_field(row.get("variables", []))
        if not isinstance(raw, list):
            return []
        return EmailTemplateService._ensure_storage_variable_ids(raw)

    def _row_to_list_item(self, row: dict[str, Any]) -> EmailTemplateListItem:
        """Map a repository row to a list summary schema."""
        return EmailTemplateListItem(
            id=str(row["id"]),
            name=row["name"],
            template_type=row["template_type"],
            status=row["status"],
            is_default=row["is_default"],
            created_at=format_iso_datetime(row.get("created_at")),
            updated_at=format_iso_datetime(row.get("updated_at")),
        )

    @staticmethod
    def _row_to_audit_snapshot(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize a DB row for audit logging (stable keys for diffs)."""
        snapshot: dict[str, Any] = {
            "id": str(row["id"]),
            "name": row["name"],
            "template_type": row["template_type"],
            "status": row["status"],
            "is_default": row["is_default"],
            "subject": row.get("subject"),
            "html_content": row["html_content"],
            "variables": EmailTemplateService._parse_variables_from_row(row),
            "created_at": format_iso_datetime(row.get("created_at")),
            "updated_at": format_iso_datetime(row.get("updated_at")),
        }
        if row.get("organization_id") is not None:
            snapshot["organization_id"] = str(row["organization_id"])
        return snapshot

    def _row_to_detail(self, row: dict[str, Any]) -> EmailTemplateDetailResponse:
        """Map a repository row to a full detail response including variables."""
        return EmailTemplateDetailResponse(
            id=str(row["id"]),
            name=row["name"],
            template_type=row["template_type"],
            status=row["status"],
            is_default=row["is_default"],
            subject=row.get("subject"),
            html_content=row["html_content"],
            variables=self._parse_variables_from_row(row),
            created_at=format_iso_datetime(row.get("created_at")),
            updated_at=format_iso_datetime(row.get("updated_at")),
        )

    @staticmethod
    def _raise_unique_violation(exc: UniqueViolationError) -> None:
        """Translate known unique constraint violations into API conflict errors."""
        constraint = getattr(exc, "constraint_name", "") or ""
        if constraint == "uq_et_org_name":
            raise ConflictException(
                message_key="email_templates.errors.name_exists",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        if constraint == "uq_et_org_default_layout":
            raise ConflictException(
                message_key="email_templates.errors.default_layout_exists",
                custom_code=CustomStatusCode.CONFLICT,
            ) from exc
        raise exc

    def _resolve_variables_json_for_update(
        self,
        existing: dict[str, Any],
        body: UpdateEmailTemplateRequest,
        template_type: EmailTemplateType,
        html_content: str,
    ) -> list[dict[str, Any]] | None:
        """Build validated variables JSON when variables or HTML change on update."""
        if body.variables is not None:
            stored = self._parse_variables_from_row(existing)
            merged = EmailTemplateService._apply_variable_mutations(stored, body.variables)
            variables = EmailTemplateService._stored_variables_to_requests(merged)
            return self.validate_template_payload(
                template_type=template_type,
                html_content=html_content,
                variables=variables,
            )
        if body.html_content is not None:
            variables = EmailTemplateService._stored_variables_to_requests(
                self._parse_variables_from_row(existing)
            )
            return self.validate_template_payload(
                template_type=template_type,
                html_content=html_content,
                variables=variables,
            )
        return None

    @staticmethod
    def _build_template_update_data(
        body: UpdateEmailTemplateRequest,
        variables_json: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Map a PATCH body to repository update fields."""
        update_data: dict[str, Any] = {}
        if body.name is not None:
            update_data["name"] = body.name.strip()
        if body.subject is not None:
            update_data["subject"] = body.subject
        if body.html_content is not None:
            update_data["html_content"] = body.html_content
        if body.status is not None:
            update_data["status"] = body.status.value
        if variables_json is not None:
            update_data["variables"] = variables_json
        return update_data

    async def create_email_template(self, body: CreateEmailTemplateRequest) -> dict[str, Any]:
        """Create a TRIGGER or LAYOUT template."""
        organization_id = self.user_context.organization_id
        variables_json = self.validate_template_payload(
            template_type=body.template_type,
            html_content=body.html_content,
            variables=body.variables,
        )

        row = {
            "organization_id": organization_id,
            "name": body.name.strip(),
            "template_type": body.template_type.value,
            "status": body.status.value,
            "subject": body.subject,
            "html_content": body.html_content,
            "variables": variables_json,
            "is_default": body.is_default and body.template_type == EmailTemplateType.LAYOUT,
        }

        try:
            created = await self.repository.create_template(row)
        except UniqueViolationError as exc:
            self._raise_unique_violation(exc)

        return self._row_to_audit_snapshot(created)

    async def list_email_templates(
        self,
        *,
        template_type: EmailTemplateType | None = None,
        status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List template summaries for the organization."""
        organization_id = self.user_context.organization_id
        rows = await self.repository.list_templates(
            organization_id,
            template_type=template_type.value if template_type else None,
            status=status,
        )
        items = [self._row_to_list_item(row).model_dump(mode="json") for row in rows]
        return items, len(items)

    async def get_email_template(self, template_id: str) -> dict[str, Any]:
        """Get full template detail."""
        organization_id = self.user_context.organization_id
        row = await self.repository.get_template_by_id(organization_id, template_id)
        if not row:
            raise NotFoundException(
                message_key="email_templates.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._row_to_detail(row).model_dump(mode="json")

    async def update_email_template(
        self,
        template_id: str,
        body: UpdateEmailTemplateRequest,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Partially update a template."""
        organization_id = self.user_context.organization_id
        existing = await self.repository.get_template_by_id(organization_id, template_id)
        if not existing:
            raise NotFoundException(
                message_key="email_templates.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        template_type = EmailTemplateType(existing["template_type"])
        html_content = (
            body.html_content if body.html_content is not None else existing["html_content"]
        )
        variables_json = self._resolve_variables_json_for_update(
            existing, body, template_type, html_content
        )
        update_data = self._build_template_update_data(body, variables_json)

        try:
            updated = await self.repository.update_template(
                organization_id,
                template_id,
                update_data,
            )
        except UniqueViolationError as exc:
            self._raise_unique_violation(exc)

        if not updated:
            raise NotFoundException(
                message_key="email_templates.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return (
            self._row_to_audit_snapshot(existing),
            self._row_to_audit_snapshot(updated),
        )

    async def delete_email_template(self, template_id: str) -> dict[str, Any]:
        """Delete a template; default layout cannot be removed."""
        organization_id = self.user_context.organization_id
        existing = await self.repository.get_template_by_id(organization_id, template_id)
        if not existing:
            raise NotFoundException(
                message_key="email_templates.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        if existing.get("is_default"):
            raise ConflictException(
                message_key="email_templates.errors.cannot_delete_default_layout",
                custom_code=CustomStatusCode.CONFLICT,
            )

        deleted = await self.repository.delete_template(organization_id, template_id)
        if not deleted:
            raise NotFoundException(
                message_key="email_templates.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return self._row_to_audit_snapshot(deleted)

    async def _get_layout_for_render(
        self,
        organization_id: str,
        layout_id: str | None,
    ) -> dict[str, Any]:
        """Resolve the layout row to use when rendering a TRIGGER template."""
        if layout_id:
            layout = await self.repository.get_template_by_id(organization_id, layout_id)
            if not layout:
                raise NotFoundException(
                    message_key="email_templates.errors.layout_not_found",
                    custom_code=CustomStatusCode.NOT_FOUND,
                )
            if layout["template_type"] != EmailTemplateType.LAYOUT.value:
                raise ValidationException(
                    message_key="email_templates.errors.layout_id_must_be_layout",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            return layout

        layout = await self.repository.get_default_layout(organization_id)
        if not layout:
            raise NotFoundException(
                message_key="email_templates.errors.default_layout_not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )
        return layout

    async def render_email_template(
        self,
        template_id: str,
        body: RenderEmailTemplateRequest,
    ) -> dict[str, Any]:
        """Merge layout + trigger (if applicable) and substitute variable values into HTML."""
        organization_id = self.user_context.organization_id
        template = await self.repository.get_template_by_id(organization_id, template_id)
        if not template:
            raise NotFoundException(
                message_key="email_templates.errors.not_found",
                custom_code=CustomStatusCode.NOT_FOUND,
            )

        template_type = EmailTemplateType(template["template_type"])
        variable_defs = self._variables_from_row(template)

        if template_type == EmailTemplateType.TRIGGER:
            layout = await self._get_layout_for_render(organization_id, body.layout_id)
            body_html = template["html_content"]
            resolved = self.variable_validator.resolve_runtime_variable_values(
                variable_defs,
                body.variable_values,
            )
            body_html = self.substitute_variable_placeholders(body_html, resolved)

            if BODY_CONTENT_TOKEN not in layout["html_content"]:
                raise ValidationException(
                    message_key="email_templates.errors.body_content_required",
                    custom_code=CustomStatusCode.VALIDATION_ERROR,
                )
            html_content = layout["html_content"].replace(BODY_CONTENT_TOKEN, body_html)
            layout_id = str(layout["id"])

            response = RenderEmailTemplateResponse(
                template_id=str(template["id"]),
                template_type=template_type.value,
                layout_id=layout_id,
                subject=template.get("subject"),
                html_content=html_content,
                resolved_variables=resolved,
            )
            return response.model_dump(mode="json")

        body_slot = body.body_content if body.body_content is not None else ""
        layout_variables = variable_defs
        resolved = self.variable_validator.resolve_runtime_variable_values(
            layout_variables,
            body.variable_values,
        )

        if BODY_CONTENT_TOKEN not in template["html_content"]:
            raise ValidationException(
                message_key="email_templates.errors.body_content_required",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )
        html_content = template["html_content"].replace(BODY_CONTENT_TOKEN, body_slot)
        html_content = self.substitute_variable_placeholders(html_content, resolved)

        response = RenderEmailTemplateResponse(
            template_id=str(template["id"]),
            template_type=template_type.value,
            layout_id=str(template["id"]),
            subject=None,
            html_content=html_content,
            resolved_variables=resolved,
        )
        return response.model_dump(mode="json")

    @staticmethod
    def email_template_ai_generation_enabled() -> bool:
        """Return True when strands email-template agent credentials are configured."""
        iso = shared_settings.isometrik
        return bool(iso.strands_auth_token.strip() and iso.email_template_agent_id.strip())

    @staticmethod
    def _build_email_template_agent_message(*, query: str, organization_id: str) -> str:
        """Append organization scope suffix expected by the external template agent."""
        normalized_query = query.strip()
        return f"{normalized_query} ::organization_id : {organization_id.strip()}"

    @staticmethod
    def _parse_template_id_from_agent_text(raw_text: str) -> str | None:
        """Extract template_id from agent text (JSON object or bare UUID)."""
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        try:
            payload = json.loads(cleaned)
            if isinstance(payload, dict):
                template_id = payload.get("template_id")
                if isinstance(template_id, str) and template_id.strip():
                    return template_id.strip()
        except json.JSONDecodeError:
            pass

        try:
            return str(uuid.UUID(cleaned))
        except ValueError:
            return None

    async def generate_email_template_with_ai(self, *, query: str) -> str:
        """Invoke the configured strands agent and return the created template id."""
        if not self.email_template_ai_generation_enabled():
            raise ServiceUnavailableException(
                message_key="email_templates.errors.ai_generation_not_configured",
            )

        organization_id = self.user_context.organization_id
        if not organization_id:
            raise ValidationException(
                message_key="email_templates.errors.ai_generation_failed",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
            )

        agent_id = shared_settings.isometrik.email_template_agent_id
        message = self._build_email_template_agent_message(
            query=query,
            organization_id=organization_id,
        )

        try:
            body = await call_strands_agent(agent_id=agent_id, message=message, stream=False)
        except httpx.HTTPError as exc:
            logger.error(
                "email_template_agent_request_failed: %s | agent_id=%s organization_id=%s",
                exc,
                agent_id,
                organization_id,
                exc_info=True,
            )
            raise ServiceUnavailableException(
                message_key="email_templates.errors.ai_generation_failed",
            ) from exc

        raw_text = body.get("text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            logger.error(
                "email_template_agent_empty_text | agent_id=%s organization_id=%s body_keys=%s",
                agent_id,
                organization_id,
                list(body.keys()),
            )
            raise ServiceUnavailableException(
                message_key="email_templates.errors.ai_generation_invalid_response",
            )

        template_id = self._parse_template_id_from_agent_text(raw_text)
        if not template_id:
            logger.error(
                "email_template_agent_invalid_template_id | agent_id=%s organization_id=%s "
                "raw_text_preview=%s",
                agent_id,
                organization_id,
                raw_text[:500],
            )
            raise ServiceUnavailableException(
                message_key="email_templates.errors.ai_generation_invalid_response",
            )

        return template_id
