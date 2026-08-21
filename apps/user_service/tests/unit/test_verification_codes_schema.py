"""Unit tests for verification code request schema validators."""

from __future__ import annotations

import pytest

from apps.user_service.app.schemas.enums import VerificationType
from apps.user_service.app.schemas.verification_codes import (
    SendVerificationCodeRequest,
    VerifyVerificationCodeRequest,
)
from libs.shared_utils.http_exceptions import ValidationException


def test_send_email_requires_email():
    with pytest.raises(ValidationException) as exc:
        SendVerificationCodeRequest(type=VerificationType.EMAIL)
    assert exc.value.message_key == "verification_codes.errors.email_required"


def test_send_email_rejects_phone_fields():
    with pytest.raises(ValidationException) as exc:
        SendVerificationCodeRequest(
            type=VerificationType.EMAIL,
            email="user@example.com",
            phone_number="+911234567890",
        )
    assert exc.value.message_key == "verification_codes.errors.phoneNumber_provided"


def test_send_phone_requires_number_and_isd():
    with pytest.raises(ValidationException) as exc:
        SendVerificationCodeRequest(type=VerificationType.PHONE_NUMBER)
    assert exc.value.message_key == "verification_codes.errors.phoneNumber_required"

    with pytest.raises(ValidationException) as exc:
        SendVerificationCodeRequest(
            type=VerificationType.PHONE_NUMBER,
            phone_number="1234567890",
        )
    assert exc.value.message_key == "verification_codes.errors.phone_isd_code_required"


def test_send_phone_rejects_email():
    with pytest.raises(ValidationException) as exc:
        SendVerificationCodeRequest(
            type=VerificationType.PHONE_NUMBER,
            phone_number="1234567890",
            phone_isd_code="+91",
            email="user@example.com",
        )
    assert exc.value.message_key == "verification_codes.errors.email_provided"


def test_send_valid_email_and_phone_requests():
    email_req = SendVerificationCodeRequest(
        type=VerificationType.EMAIL,
        email="user@example.com",
    )
    assert email_req.email == "user@example.com"

    phone_req = SendVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        phone_number="1234567890",
        phone_isd_code="+91",
    )
    assert phone_req.phone_isd_code == "+91"


def test_verify_email_requires_email():
    with pytest.raises(ValidationException) as exc:
        VerifyVerificationCodeRequest(
            type=VerificationType.EMAIL,
            verification_id="v-1",
            verification_code="123456",
        )
    assert exc.value.message_key == "verification_codes.errors.email_required"


def test_verify_phone_requires_number_and_isd():
    with pytest.raises(ValidationException) as exc:
        VerifyVerificationCodeRequest(
            type=VerificationType.PHONE_NUMBER,
            verification_id="v-1",
            verification_code="123456",
        )
    assert exc.value.message_key == "verification_codes.errors.phoneNumber_required"

    with pytest.raises(ValidationException) as exc:
        VerifyVerificationCodeRequest(
            type=VerificationType.PHONE_NUMBER,
            verification_id="v-1",
            verification_code="123456",
            phone_number="1234567890",
        )
    assert exc.value.message_key == "verification_codes.errors.phone_isd_code_required"


def test_verify_phone_rejects_email():
    with pytest.raises(ValidationException) as exc:
        VerifyVerificationCodeRequest(
            type=VerificationType.PHONE_NUMBER,
            verification_id="v-1",
            verification_code="123456",
            phone_number="1234567890",
            phone_isd_code="+91",
            email="user@example.com",
        )
    assert exc.value.message_key == "verification_codes.errors.email_provided"


def test_verify_valid_requests():
    email_req = VerifyVerificationCodeRequest(
        type=VerificationType.EMAIL,
        verification_id="v-1",
        verification_code="123456",
        email="user@example.com",
    )
    assert email_req.verification_id == "v-1"

    phone_req = VerifyVerificationCodeRequest(
        type=VerificationType.PHONE_NUMBER,
        verification_id="v-1",
        verification_code="123456",
        phone_number="1234567890",
        phone_isd_code="+91",
    )
    assert phone_req.phone_number == "1234567890"
