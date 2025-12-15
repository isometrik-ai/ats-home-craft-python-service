"""Verification Codes Schemas Module.

This module contains Pydantic schemas for verification code operations.
"""

from enum import Enum

from pydantic import BaseModel, EmailStr, Field, model_validator

from libs.shared_utils.http_exceptions import ValidationException
from libs.shared_utils.status_codes import CustomStatusCode

# Re-export enums for easier imports
__all__ = [
    "VerificationType",
    "VerificationTrigger",
    "SendVerificationCodeRequest",
    "SendVerificationCodeResponse",
    "VerifyVerificationCodeRequest",
    "VerifyVerificationCodeResponse",
]


class VerificationType(str, Enum):
    """Verification type enumeration"""

    EMAIL = "EMAIL"
    PHONE_NUMBER = "PHONE_NUMBER"


class VerificationTrigger(str, Enum):
    """Verification trigger/purpose enumeration"""

    SIGNUP_EMAIL_VERIFICATION = "SIGNUP_EMAIL_VERIFICATION"
    SIGNUP_PHONE_VERIFICATION = "SIGNUP_PHONE_VERIFICATION"
    EMAIL_UPDATE = "EMAIL_UPDATE"
    PHONE_NUMBER_UPDATE = "PHONE_NUMBER_UPDATE"


# ============================================================================
# REQUEST MODELS
# ============================================================================


class SendVerificationCodeRequest(BaseModel):
    """Request model for sending verification code"""

    type: VerificationType = Field(..., description="Type of verification: EMAIL or PHONE_NUMBER")
    email: EmailStr | None = Field(None, description="Email address for verification")
    phoneNumber: str | None = Field(None, description="Phone number for verification")
    verification_method: str | None = Field(
        None,
        description="Optional verification method field (e.g., 'signup_verification')",
    )

    @model_validator(mode="after")
    def validate_email_or_phone(self):
        """Validate that either email or phoneNumber is provided based on type"""
        if self.type == VerificationType.EMAIL:
            if not self.email:
                raise ValidationException(
                    message_key="verification_codes.errors.email_required",
                    custom_code=CustomStatusCode.INVALID_DATA,
                )
            if self.phoneNumber:
                raise ValidationException(
                    message_key="verification_codes.errors.phoneNumber_provided",
                    custom_code=CustomStatusCode.INVALID_DATA,
                )
        elif self.type == VerificationType.PHONE_NUMBER:
            if not self.phoneNumber:
                raise ValidationException(
                    message_key="verification_codes.errors.phoneNumber_required",
                    custom_code=CustomStatusCode.INVALID_DATA,
                )
            if self.email:
                raise ValidationException(
                    message_key="verification_codes.errors.email_provided",
                    custom_code=CustomStatusCode.INVALID_DATA,
                )
        return self


class VerifyVerificationCodeRequest(BaseModel):
    """Request model for verifying verification code"""

    type: VerificationType = Field(..., description="Type of verification: EMAIL or PHONE_NUMBER")
    verification_id: str = Field(..., description="ID of the verification code record")
    verification_code: str = Field(..., description="The verification code to verify")
    email: EmailStr | None = Field(None, description="Email address for verification")
    phoneNumber: str | None = Field(None, description="Phone number for verification")

    @model_validator(mode="after")
    def validate_email_or_phone(self):
        """Validate that either email or phoneNumber is provided based on type"""
        if self.type == VerificationType.EMAIL:
            if not self.email:
                raise ValidationException(
                    message_key="verification_codes.errors.email_required",
                    custom_code=CustomStatusCode.INVALID_DATA,
                )
            if self.phoneNumber:
                raise ValidationException(
                    message_key="verification_codes.errors.phoneNumber_provided",
                    custom_code=CustomStatusCode.INVALID_DATA,
                )
        elif self.type == VerificationType.PHONE_NUMBER:
            if not self.phoneNumber:
                raise ValidationException(
                    message_key="verification_codes.errors.phoneNumber_required",
                    custom_code=CustomStatusCode.INVALID_DATA,
                )
            if self.email:
                raise ValidationException(
                    message_key="verification_codes.errors.email_provided",
                    custom_code=CustomStatusCode.INVALID_DATA,
                )
        return self


# ============================================================================
# RESPONSE MODELS
# ============================================================================


class SendVerificationCodeResponse(BaseModel):
    """Response model for sending verification code"""

    verification_id: str = Field(..., description="ID of the created verification code")
    expiryAt: int = Field(..., description="Expiry timestamp (Unix timestamp in milliseconds)")
    message: str = Field(
        default="Verification code sent successfully", description="Response message"
    )
    attemptsLeft: int = Field(..., description="Number of send OTP attempts remaining for today")


class VerifyVerificationCodeResponse(BaseModel):
    """Response model for verifying verification code"""

    verified: bool = Field(..., description="Whether the verification was successful")
    message: str = Field(..., description="Response message")
