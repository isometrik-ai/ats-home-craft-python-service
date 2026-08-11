"""Branch-coverage tests for Pydantic schema validators."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from apps.user_service.app.schemas.auth import (
    ChangePasswordRequest,
    CompanyData,
    SignupRequest,
)
from apps.user_service.app.schemas.common import NoteItem
from apps.user_service.app.schemas.contact_onboarding import (
    CompleteProfileRequest,
    CreateHouseholdMemberRequest,
    CreateVehicleRequest,
    ReviewVehicleRequest,
    UpdateHouseholdMemberRequest,
    UpdateVehicleRequest,
)
from apps.user_service.app.schemas.email_templates import (
    CreateEmailTemplateRequest,
    EmailTemplateVariableDefinition,
    EmailTemplateVariablesMutation,
    EmailTemplateVariableUpdateRequest,
    UpdateEmailTemplateRequest,
)
from apps.user_service.app.schemas.enums import (
    ContactUnitRelationship,
    EmailTemplateType,
    FieldType,
    MoveEventType,
    VehicleStatus,
    VehicleType,
)
from apps.user_service.app.schemas.move_events import (
    CreateMoveEventRequest,
    UpdateMoveEventRequest,
)
from apps.user_service.app.schemas.visitor_logs import VisitorLogDateRangeQuery
from libs.shared_utils.http_exceptions import ValidationException


def _phone(*, primary: bool = True) -> dict:
    return {
        "phone_number": "9876543210",
        "phone_isd_code": "+91",
        "is_primary": primary,
    }


class TestAuthSchemaValidators:
    """Auth schema password and company signup validators."""

    def test_signup_password_field_length(self) -> None:
        with pytest.raises(ValidationError):
            SignupRequest.model_validate(
                {
                    "email": "user@example.com",
                    "password": "12345",
                    "first_name": "Ada",
                    "verification_id": "vid",
                    "verification_code": "123456",
                }
            )

    def test_change_password_field_length(self) -> None:
        with pytest.raises(ValidationError):
            ChangePasswordRequest.model_validate(
                {"current_password": "oldpass", "new_password": "123"}
            )

    def test_company_practice_area_overlap_rejected(self) -> None:
        with pytest.raises(ValidationException, match="practice_areas_overlap"):
            CompanyData(
                company_name="Acme Legal",
                primary_practice_areas=["Litigation"],
                secondary_practice_areas=["Litigation"],
            )


class TestEmailTemplateSchemaValidators:
    """Email template builder schema branches."""

    def test_invalid_variable_key(self) -> None:
        with pytest.raises(ValidationException):
            EmailTemplateVariableDefinition(
                variable_key="Bad-Key",
                field_name="Bad",
                field_type=FieldType.TEXT,
            )

    def test_sub_fields_only_for_object_or_list(self) -> None:
        with pytest.raises(ValidationException):
            EmailTemplateVariableDefinition(
                variable_key="title",
                field_name="Title",
                field_type=FieldType.TEXT,
                sub_fields=[
                    EmailTemplateVariableDefinition(
                        variable_key="child",
                        field_name="Child",
                        field_type=FieldType.TEXT,
                    )
                ],
            )

    def test_list_requires_exactly_one_child(self) -> None:
        with pytest.raises(ValidationException):
            EmailTemplateVariableDefinition(
                variable_key="items",
                field_name="Items",
                field_type=FieldType.LIST,
                sub_fields=[],
            )

    def test_nesting_depth_exceeded(self) -> None:
        from apps.user_service.app.schemas.custom_fields import MAX_NESTING_DEPTH

        child = EmailTemplateVariableDefinition(
            variable_key="leaf",
            field_name="Leaf",
            field_type=FieldType.TEXT,
        )
        current = EmailTemplateVariableDefinition(
            variable_key="list",
            field_name="List",
            field_type=FieldType.LIST,
            sub_fields=[child],
        )
        for idx in range(MAX_NESTING_DEPTH):
            current = EmailTemplateVariableDefinition(
                variable_key=f"obj_{idx}",
                field_name=f"Obj {idx}",
                field_type=FieldType.OBJECT,
                sub_fields=[current],
            )
        with pytest.raises(ValidationException):
            EmailTemplateVariableDefinition.validate_nesting_depth_iterative(current)

    def test_update_requires_field_type_for_type_config(self) -> None:
        with pytest.raises(ValidationException):
            EmailTemplateVariableUpdateRequest(id="var-1", type_config={"max_length": 10})

    def test_variables_mutation_requires_operation(self) -> None:
        with pytest.raises(ValidationException):
            EmailTemplateVariablesMutation()

    def test_create_template_is_default_layout_only(self) -> None:
        with pytest.raises(ValidationException):
            CreateEmailTemplateRequest(
                name="Trigger",
                template_type=EmailTemplateType.TRIGGER,
                html_content="<p>Hi</p>",
                is_default=True,
            )

    def test_create_template_blank_name(self) -> None:
        with pytest.raises(ValidationException):
            CreateEmailTemplateRequest(
                name="   ",
                template_type=EmailTemplateType.LAYOUT,
                html_content="<p>Hi</p>",
            )

    def test_update_template_empty_payload(self) -> None:
        with pytest.raises(ValidationException):
            UpdateEmailTemplateRequest()


class TestContactOnboardingSchemaValidators:
    """Contact onboarding request validation branches."""

    def test_complete_profile_primary_phone_required(self) -> None:
        with pytest.raises(ValidationException):
            CompleteProfileRequest(
                first_name="Jane",
                phones=[_phone(primary=False), _phone(primary=False)],
            )

    def test_create_vehicle_invalid_photo_path(self) -> None:
        with pytest.raises(ValidationException):
            CreateVehicleRequest(
                unit_id="unit-1",
                vehicle_type=VehicleType.FOUR_WHEELER,
                registration_number="MH12AB1234",
                photo_paths=[""],
            )

    def test_update_vehicle_invalid_photo_path(self) -> None:
        with pytest.raises(ValidationException):
            UpdateVehicleRequest(photo_paths=["x" * 501])

    def test_review_vehicle_requires_slot_on_approve(self) -> None:
        with pytest.raises(ValidationException):
            ReviewVehicleRequest(status=VehicleStatus.APPROVED)

    def test_review_vehicle_requires_reason_on_reject(self) -> None:
        with pytest.raises(ValidationException):
            ReviewVehicleRequest(status=VehicleStatus.REJECTED)

    def test_review_vehicle_rejects_pending_status(self) -> None:
        with pytest.raises(ValidationException):
            ReviewVehicleRequest(
                status=VehicleStatus.PENDING,
                parking_slot_id="slot-1",
            )

    def test_household_member_requires_primary_phone(self) -> None:
        with pytest.raises(ValidationException):
            CreateHouseholdMemberRequest(
                unit_id="unit-1",
                first_name="Kid",
                phones=[_phone(primary=False)],
                relationship=ContactUnitRelationship.CHILD,
            )

    def test_update_household_member_empty_patch(self) -> None:
        with pytest.raises(ValidationException):
            UpdateHouseholdMemberRequest()


class TestMoveEventsSchemaValidators:
    """Move event schema validation."""

    def test_create_move_event_document_path_limit(self) -> None:
        with pytest.raises(ValidationError):
            CreateMoveEventRequest(
                unit_id="unit-1",
                contact_id="contact-1",
                move_type=MoveEventType.MOVE_IN,
                event_date=date(2026, 1, 1),
                document_paths=[f"path-{idx}" for idx in range(21)],
            )

    def test_update_move_event_document_path_limit(self) -> None:
        with pytest.raises(ValidationError):
            UpdateMoveEventRequest(
                document_paths=[f"path-{idx}" for idx in range(21)],
            )


class TestVisitorLogsSchemaValidators:
    """Visitor log query schema validation."""

    def test_date_range_allows_both_none(self) -> None:
        query = VisitorLogDateRangeQuery()
        assert query.start_at is None
        assert query.end_at is None

    def test_date_range_requires_both_bounds(self) -> None:
        with pytest.raises(ValidationError):
            VisitorLogDateRangeQuery(start_at=datetime(2026, 6, 1, tzinfo=timezone.utc))

    def test_date_range_end_must_be_after_start(self) -> None:
        with pytest.raises(ValidationError):
            VisitorLogDateRangeQuery(
                start_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
                end_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            )

    def test_date_range_normalizes_naive_datetimes(self) -> None:
        query = VisitorLogDateRangeQuery(
            start_at=datetime(2026, 6, 1),
            end_at=datetime(2026, 6, 30),
        )
        assert query.start_at.tzinfo == timezone.utc
        assert query.end_at.tzinfo == timezone.utc


class TestCommonSchemaValidators:
    """Shared common schema validators."""

    def test_note_item_strips_and_rejects_blank(self) -> None:
        note = NoteItem(title="  Title  ", content="  Body  ")
        assert note.title == "Title"

        with pytest.raises(ValidationError):
            NoteItem(title="   ", content="Body")
