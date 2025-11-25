# pylint: disable=invalid-name,E0213
"""
Verification Codes Schemas Module

This module contains Pydantic schemas for verification code operations.
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, EmailStr, model_validator

from apps.user_service.app.schemas import ResponseModel

# Re-export enums for easier imports
__all__ = [
    "VerificationType",
    "VerificationTrigger",
    "SendVerificationCodeRequest",
    "SendVerificationCodeResponse",
    "VerifyVerificationCodeRequest",
    "VerifyVerificationCodeResponse",
]


# ============================================================================
# ENUMS
# ============================================================================

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
    email: Optional[EmailStr] = Field(None, description="Email address for verification")
    phoneNumber: Optional[str] = Field(None, description="Phone number for verification")
    verification_type: Optional[str] = Field(None, description="Optional verification type field (e.g., 'signup_verification')")

    @model_validator(mode='after')
    def validate_email_or_phone(self):
        """Validate that either email or phoneNumber is provided based on type"""
        if self.type == VerificationType.EMAIL:
            if not self.email:
                raise ValueError("Email is required when type is EMAIL")
            if self.phoneNumber:
                raise ValueError("phoneNumber should not be provided when type is EMAIL")
        elif self.type == VerificationType.PHONE_NUMBER:
            if not self.phoneNumber:
                raise ValueError("phoneNumber is required when type is PHONE_NUMBER")
            if self.email:
                raise ValueError("email should not be provided when type is PHONE_NUMBER")
        return self


class VerifyVerificationCodeRequest(BaseModel):
    """Request model for verifying verification code"""

    type: VerificationType = Field(..., description="Type of verification: EMAIL or PHONE_NUMBER")
    verificationId: str = Field(..., description="ID of the verification code record")
    verificationCode: str = Field(..., description="The verification code to verify")
    email: Optional[EmailStr] = Field(None, description="Email address for verification")
    phoneNumber: Optional[str] = Field(None, description="Phone number for verification")

    @model_validator(mode='after')
    def validate_email_or_phone(self):
        """Validate that either email or phoneNumber is provided based on type"""
        if self.type == VerificationType.EMAIL:
            if not self.email:
                raise ValueError("Email is required when type is EMAIL")
            if self.phoneNumber:
                raise ValueError("phoneNumber should not be provided when type is EMAIL")
        elif self.type == VerificationType.PHONE_NUMBER:
            if not self.phoneNumber:
                raise ValueError("phoneNumber is required when type is PHONE_NUMBER")
            if self.email:
                raise ValueError("email should not be provided when type is PHONE_NUMBER")
        return self


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class SendVerificationCodeResponse(ResponseModel):
    """Response model for sending verification code"""

    verificationId: str = Field(..., description="ID of the created verification code")
    expiryAt: int = Field(..., description="Expiry timestamp (Unix timestamp in milliseconds)")
    message: str = Field(default="Verification code sent successfully", description="Response message")
    attemptsLeft: int = Field(..., description="Number of send OTP attempts remaining for today")


class VerifyVerificationCodeResponse(ResponseModel):
    """Response model for verifying verification code"""

    verified: bool = Field(..., description="Whether the verification was successful")
    message: str = Field(..., description="Response message")
