"""Shared request models for list/filter endpoints."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode


class DropdownCustomFieldFilter(BaseModel):
    """Dropdown custom-field filter for list endpoints."""

    model_config = ConfigDict(extra="forbid")

    field_id: str = Field(..., description="Custom field id (UUID string).")
    values: list[str] = Field(..., min_length=1, description="Selected dropdown options.")

    @model_validator(mode="after")
    def _normalize_values(self) -> "DropdownCustomFieldFilter":
        """Normalize the values of the dropdown custom-field filter."""
        fid = (self.field_id or "").strip()
        if not fid:
            raise ValidationException(
                message_key="custom_fields.errors.invalid_filter_payload",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"details": "field_id is required."},
            )

        cleaned = [(value or "").strip() for value in (self.values or [])]
        cleaned = [value for value in cleaned if value]

        deduped: list[str] = []
        seen: set[str] = set()
        for value in cleaned:
            if value in seen:
                continue
            seen.add(value)
            deduped.append(value)

        if not deduped:
            raise ValidationException(
                message_key="custom_fields.errors.invalid_filter_payload",
                custom_code=CustomStatusCode.VALIDATION_ERROR,
                params={"details": "values must include at least one non-empty string."},
            )

        self.field_id = fid
        self.values = deduped
        return self
