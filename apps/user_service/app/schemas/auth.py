"""
Auth Schemas Module

"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, EmailStr, validator

# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================


class AccountType(str, Enum):
    """Account type enumeration"""

    PERSONAL = "personal"
    BUSINESS = "business"


class PlanType(str, Enum):
    """Plan type enumeration"""

    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


# ============================================================================
# REQUEST MODELS
# ============================================================================


class SessionFilter(BaseModel):
    """Request model for Session Filter"""

    search: Optional[str] = None
    session_status: Optional[str] = None
    login_method: Optional[str] = None
    limit: int = 20
    offset: int = 0


class AuthLogin(BaseModel):
    """Request model for user login"""

    email: EmailStr
    password: str


class MemberBody(BaseModel):
    """Request model"""

    email: EmailStr
    full_name: str
    phone: Optional[str] = None
    timezone: str = "UTC"


class UserSignupData(BaseModel):
    """User signup data model"""

    first_name: str
    last_name: str
    email: EmailStr
    password: str
    job_title: Optional[str] = None
    phone: Optional[str] = None
    timezone: str = "UTC"

    @validator("first_name", "last_name")
    def validate_name_fields(cls, v):  # pylint: disable=no-self-argument
        """Validate name fields are not empty and meet minimum length requirements"""
        if not v or not v.strip():
            raise ValueError("Name fields cannot be empty")
        if len(v.strip()) < 2:
            raise ValueError("Name must be at least 2 characters long")
        return v.strip()

    @validator("password")
    def validate_password(cls, v):  # pylint: disable=no-self-argument
        """Validate password meets minimum length requirements"""
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters long")
        return v


class CompanySignupData(BaseModel):
    """Company signup data model"""

    company_name: str
    company_website: Optional[str] = None
    industry: Optional[str] = None
    company_size: Optional[str] = None
    description: Optional[str] = None
    referral_source: Optional[str] = None

    @validator("company_name")
    def validate_company_name(cls, v):  # pylint: disable=no-self-argument
        """Validate non-empty company name with minimum 2 characters."""
        if not v or not v.strip():
            raise ValueError("Company name cannot be empty")
        if len(v.strip()) < 2:
            raise ValueError("Company name must be at least 2 characters long")
        return v.strip()

    @validator("company_website")
    def validate_website(cls, v):  # pylint: disable=no-self-argument
        """Ensure company website starts with http:// or https://."""
        if v and not v.startswith(("http://", "https://")):
            return f"https://{v}"
        return v


# pylint: disable=R0903
class VerifyEmailRequest(BaseModel):
    """Request model for Verify Email operations"""

    email: EmailStr


class VerifyEmailResponse(BaseModel):
    """Response model for Verify Email operations"""

    status_code: int
    message: str
    email_found: bool
    status: Optional[str]  # 'active', 'suspended', or None
    can_login: bool


class SignupRequest(BaseModel):
    """Main signup request model"""

    account_type: AccountType
    user_data: UserSignupData
    company_data: Optional[CompanySignupData] = None
    plan_type: PlanType = PlanType.STARTER

    @validator("company_data")
    def validate_company_data(cls, v, values):  # pylint: disable=no-self-argument
        """Validate company data for business account type."""
        if values.get("account_type") == AccountType.BUSINESS and not v:
            raise ValueError("Company data is required for business accounts")
        return v


# ============================================================================
# RESPONSE MODELS
# ============================================================================


class UserInfo(BaseModel):
    """User information model"""

    id: str
    email: str
    full_name: str


class OrganizationInfo(BaseModel):
    """Organization information model"""

    id: str
    name: str
    slug: str
    account_type: str
    plan_type: str
    status: str


class AuthResponse(BaseModel):
    """Response model for authentication operations"""

    access_token: str
    user: UserInfo


class SignupResponse(BaseModel):
    """Response model for signup operations"""

    status_code: int
    message: str
    data: dict
