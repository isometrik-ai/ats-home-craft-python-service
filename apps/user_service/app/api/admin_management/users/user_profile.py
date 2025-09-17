"""
User Profile API Module
This module provides user profile operations including getting own profile and getting user by ID.
All endpoints include proper authentication, validation, and database operations.
"""

from datetime import datetime, timezone
import uuid
import json
from typing import Tuple, Any
from calendar import month_abbr

from fastapi import APIRouter, HTTPException, status, Depends, Request

from apps.user_service.app.dependencies.logger import get_logger
from apps.user_service.app.dependencies.common_utils import (
    extract_user_context,
    require_permission,
)
from apps.user_service.app.dependencies.user_utils import (
    format_permissions,
    update_user_activity,
    fetch_user_profile,
    fetch_user_permissions,
    create_user_profile_data,
)

# Schema imports
from apps.user_service.app.schemas.users import (
    UserProfileResponse,
    UserProfileData,
    PermissionInfo,
    RoleInfoWithDescription,
)

from apps.user_service.app.app_instance import limiter

# # Audit logging imports
# from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
#     audit_api_call,
# )

# Local imports
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_middleware.jwt_auth import get_user_from_auth

# Create router for user profile endpoints
router = APIRouter(prefix="", tags=["User Profile"])

# Initialize logger for user profile module
logger = get_logger("user-profile-api")
logger.info("User Profile API module loaded")


def get_common_dependencies(
    current_user: dict = Depends(get_user_from_auth),
    db_conn: Any = Depends(get_async_db_conn)
) -> Tuple[dict, Any]:
    """Get common dependencies used across API endpoints.
    
    Returns:
        Tuple containing (current_user, db_conn)
    """
    return current_user, db_conn


@router.get(
    "/profile",
    response_model=UserProfileResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=[
#         "gdpr",  # Accessing user profile data involves personal information
#         "pii",  # User profile contains personally identifiable information
#         "audit_required",  # Profile access must be logged for compliance and security audits
#     ],
#     table_name="organization_members",
#     category="USER_PROFILE",
# )
async def get_user_profile(
    request: Request,
    commons: Tuple[dict, Any] = Depends(get_common_dependencies),
):
    """
    Retrieve the authenticated user's profile (optimized + async).
    """
    current_user, db_conn = commons  # Destructure the tuple
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        ("GET /profile request started - Request ID: %s, ",request_id),
        ("User ID: %s, ",current_user.get('user_id')),
        ("Organization ID: %s",current_user.get('organization_id'))
    )

    user_context = extract_user_context(current_user)
    logger.debug(
        ("User context extracted - Request ID: %s, ",request_id),
        ("Email: %s, Organization ID: %s, ",user_context.email,user_context.organization_id),
        ("User Type: %s",user_context.user_type)
    )

    # Set audit context for profile access based on user type
    # if user_context.user_type == "organization_member":
    #     request.state.audit_table = "organization_members"
    # elif user_context.user_type == "client":
    #     request.state.audit_table = "client_members"
    # elif user_context.user_type == "candidate":
    #     request.state.audit_table = "candidates"

    # request.state.audit_description = (
    #     f"User accessed their own profile: {user_context.email}"
    # )
    # request.state.audit_risk_level = "low"
    # logger.debug(
    #     ("Audit context set for profile access - Request ID: %s, ",request_id),
    #     ("Email: %s, Audit Table: %s",user_context.email,request.state.audit_table)
    # )

    def _get_org_member_query() -> str:
        """Get query for fetching organization member profile."""
        return """
            SELECT
                om.id as member_id,
                om.user_id,
                om.organization_id,
                om.email,
                om.full_name,
                om.first_name,
                om.last_name,
                om.avatar_url,
                om.phone,
                om.timezone,
                om.status,
                om.joined_at,
                om.last_active_at,
                r.id as role_id,
                r.name as role_name,
                r.description as role_description
            FROM public.organization_members om
            INNER JOIN public.roles r
                ON om.role_id = r.id
                AND om.organization_id = r.organization_id
            WHERE om.user_id = $1 AND om.organization_id = $2
            LIMIT 1;
        """

    async def _fetch_org_member_profile() -> dict:
        """Fetch and validate organization member profile."""
        user_profile = await db_conn.fetchrow(
            _get_org_member_query(),
            user_context.user_id,
            user_context.organization_id,
        )
        logger.debug(
            ("User profile retrieved from database - Request ID: %s, ",request_id),
            ("Profile found: %s",user_profile is not None)
        )

        if not user_profile:
            logger.warning(
                ("User profile not found - Request ID: %s, ",request_id),
                (
                    "User ID: %s, Organization ID: %s",
                    user_context.user_id,user_context.organization_id
                )
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found or access denied to organization",
            )

        if user_profile["email"].lower() != user_context.email.lower():
            logger.warning(
                ("Token email does not match user profile - Request ID: %s, ",request_id),
                ("Token email: %s, Profile email: %s",user_context.email,user_profile['email'])
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token email does not match user profile",
            )

        return user_profile

    # Handle different user types
    if user_context.user_type == "organization_member":
        # Original flow for organization members
        user_profile = await _fetch_org_member_profile()

        async def _fetch_permissions() -> list:
            """Fetch user permissions and update activity."""
            permissions_query = """
                SELECT DISTINCT
                    p.id as permission_id,
                    p.code as permission_code,
                    p.name as permission_name,
                    p.description as permission_description,
                    p.category
                FROM public.role_permissions rp
                INNER JOIN public.permissions p
                    ON rp.permission_id = p.id
                WHERE rp.role_id = $1 AND rp.organization_id = $2
                ORDER BY p.category NULLS LAST, p.name;
            """

            permissions_data = await db_conn.fetch(
                permissions_query,
                user_profile["role_id"],
                user_context.organization_id,
            )
            logger.debug(
                ("User permissions retrieved - Request ID: %s, ",request_id),
                ("Permissions count: %s",len(permissions_data))
            )
            await update_user_activity(
                db_conn,
                user_context.user_id,
                user_context.organization_id,
            )
            logger.debug(
                ("User activity updated - Request ID: %s, ",request_id),
                ("User ID: %s",user_context.user_id)
            )
            return permissions_data

        permissions_data = await _fetch_permissions()

        def _format_org_member_data(user_profile: dict, permissions_data: list) -> UserProfileData:
            """Format organization member profile data."""
            role_info = RoleInfoWithDescription(
                role_id=str(user_profile["role_id"]),
                role_name=user_profile["role_name"],
                description=user_profile.get("role_description", ""),
            )
            permissions = format_permissions(permissions_data)
            # Timestamps are now handled by create_user_profile_data
            logger.debug(
                ("Profile data formatted - Request ID: %s, ",request_id),
                ("Role: %s, Permissions: %s",role_info.role_name,len(permissions))
            )

            # Set audit data for profile access
            request.state.raw_audit_new_data = {
                "user_id": str(user_profile["user_id"]),
                "email": user_profile["email"],
                "full_name": user_profile["full_name"],
                "organization_id": str(user_profile["organization_id"]),
                "role_id": str(user_profile["role_id"]),
                "role_name": user_profile["role_name"],
                "status": user_profile["status"],
                "permission_count": len(permissions),
                "access_timestamp": datetime.now().isoformat(),
            }

            return create_user_profile_data(
                user_profile=user_profile,
                user_type=user_context.user_type,
                role_info=role_info,
                permissions=permissions
            )

        profile_data = _format_org_member_data(user_profile, permissions_data)
    elif user_context.user_type == "client":
        # Handle client user type
        # Extract from client_members table using user_id from JWT
        client_query = """
            SELECT
                cm.id,
                cm.organization_id,
                cm.first_name,
                cm.last_name,
                cm.email,
                cm.contact_no as phone,
                cm.designation,
                cm.is_primary,
                cm.created_at as joined_at,
                cm.updated_at as last_active_at
            FROM public.client_members cm
            WHERE cm.id = $1 AND cm.organization_id = $2
            LIMIT 1;
        """
        user_profile = await db_conn.fetchrow(
            client_query,
            user_context.user_id,
            user_context.organization_id,
        )
        logger.debug(
            ("Client member profile retrieved from database - Request ID: %s, ",request_id),
            ("Profile found: %s",user_profile is not None)
        )

        if not user_profile:
            logger.warning(
                ("Client member profile not found - Request ID: %s, ",request_id),
                (
                    "User ID: %s, Organization ID: %s",
                    user_context.user_id,user_context.organization_id
                )
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Client member profile not found or access denied to organization",
            )

        # Set audit data for client member profile access
        request.state.raw_audit_new_data = {
            "user_id": str(user_profile["id"]),
            "email": user_profile["email"] or user_context.email,
            "full_name": f"{user_profile['first_name']} {user_profile['last_name']}".strip(),
            "organization_id": str(user_profile["organization_id"]),
            "user_type": "client",
            "access_timestamp": datetime.now().isoformat(),
        }
        profile_data = create_user_profile_data(
            user_profile={
                "user_id": user_profile["id"],
                "email": user_profile["email"] or user_context.email,
                "full_name": f"{user_profile['first_name']} {user_profile['last_name']}".strip(),
                "first_name": user_profile["first_name"],
                "last_name": user_profile["last_name"],
                "avatar_url": None,  # client_members doesn't have avatar_url
                "phone": user_profile["phone"],
                "timezone": "UTC",
                "status": "active",
                "joined_at": user_profile["joined_at"],
                "last_active_at": user_profile["last_active_at"],
                "organization_id": user_profile["organization_id"],
            },
            user_type=user_context.user_type,
        )
    elif user_context.user_type == "candidate":
        # Handle candidate user type
        candidate_query = """
            SELECT
                id,
                candidate_id,
                organization_id,
                first_name,
                middle_name,
                last_name,
                email,
                phone_number as phone,
                profile_picture_url as avatar_url,
                current_title,
                current_organisation,
                location,
                age_bracket,
                gender,
                linkedin_profile_url,
                current_compensation,
                target_compensation,
                executive_summary,
                confidence_level,
                confidence_score,
                work_experience,
                skills,
                sectors,
                ai_scores,
                projects,
                awards_certifications,
                notes,
                motivating_factors,
                documents,
                similar_profiles,
                created_at as joined_at,
                modified_at as last_active_at
            FROM public.candidates
            WHERE candidate_id = $1 AND organization_id = $2
            LIMIT 1;
        """
        user_profile = await db_conn.fetchrow(
            candidate_query,
            user_context.user_id,
            user_context.organization_id,
        )
        logger.debug(
            ("Candidate profile retrieved from database - Request ID: %s, ",request_id),
            ("Profile found: %s",user_profile is not None)
        )
        if not user_profile:
            logger.warning(
                ("Candidate profile not found - Request ID: %s, ",request_id),
                (
                    "User ID: %s, Organization ID: %s",
                    user_context.user_id,user_context.organization_id
                )
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Candidate profile not found or access denied to organization",
            )

        # Set audit data for candidate profile access
        request.state.raw_audit_new_data = {
            "user_id": str(user_profile["candidate_id"]),
            "email": user_profile["email"],
            "full_name": f"{user_profile['first_name']} {user_profile['last_name']}".strip(),
            "organization_id": str(user_profile["organization_id"]),
            "user_type": "candidate",
            "access_timestamp": datetime.now().isoformat(),
        }
        profile_data = create_user_profile_data(
            user_profile={
                "user_id": user_profile["candidate_id"],
                "email": user_profile["email"],
                "full_name": f"{user_profile['first_name']} {user_profile['last_name']}".strip(),
                "first_name": user_profile["first_name"],
                "last_name": user_profile["last_name"],
                "avatar_url": user_profile["avatar_url"],
                "phone": user_profile["phone"],
                "timezone": "UTC",
                "status": "active",
                "joined_at": user_profile["joined_at"],
                "last_active_at": user_profile["last_active_at"],
                "organization_id": user_profile["organization_id"],
            },
            user_type=user_context.user_type,
        )
        # Transform candidate data similar to get_detailed_candidate_by_id
        # & add it to the profile_data
        profile_data.candidate_data = {
            "id": str(user_profile["id"]),
            "candidate_id": str(user_profile["id"]),  # Include candidate_id
            "profile": {
                "firstName": user_profile["first_name"],
                "lastName": user_profile["last_name"],
                "initials": f"{user_profile['first_name'][0]}{user_profile['last_name'][0]}",
                "profile_picture_url": user_profile.get("avatar_url"),
                "ageRange": user_profile.get("age_bracket", ""),
                "gender": user_profile.get("gender", ""),
                "currentRole": user_profile.get("current_title", ""),
                "currentCompany": user_profile.get("current_organisation", ""),
            },
            "contact": {
                "email": user_profile["email"],
                "phone": user_profile.get("phone", ""),
                "location": user_profile.get("location", ""),
                "linkedin": user_profile.get("linkedin_profile_url", ""),
            },
            "documents": transform_documents(user_profile.get("documents", [])),
            "sectorExpertise": transform_sector_expertise(
                user_profile.get("sectors", {})
            ),
            "skills": transform_skills_detailed(user_profile.get("skills", [])),
            "awards": transform_awards(user_profile.get("awards_certifications", [])),
            "projects": transform_projects(user_profile.get("projects", [])),
            "workHistory": transform_work_history(
                user_profile.get("work_experience", [])
            ),
            "profileCompletionScore": calculate_profile_completion_score(user_profile),
            "lastUpdated": user_profile["last_active_at"]
            .astimezone(timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user type",
        )

    # User type validation is now handled in extract_user_context function
    # This else block should never be reached due to validation in common_utils

    logger.info(
        ("GET /profile request completed successfully - Request ID: %s, ",request_id),
        ("User ID: %s, Email: %s, ",user_context.user_id,user_context.email),
        (" Status Code: %s",status.HTTP_200_OK)
    )
    return UserProfileResponse(
        status_code=status.HTTP_200_OK,
        message="User profile retrieved successfully",
        data=profile_data,
    )

# Transformation functions for candidate data
def transform_documents(documents_data):
    """Transform documents JSONB to Document objects"""
    if isinstance(documents_data, str):
        documents_data = json.loads(documents_data)
    if not documents_data:
        return {}

    documents = {}
    for doc in documents_data:
        if isinstance(doc, dict):
            if doc["file_type"] not in documents:
                documents[doc["file_type"]] = []
            documents[doc["file_type"]].append(
                {
                    "id": doc.get("id", ""),
                    "name": doc.get("name", ""),
                    "url": doc.get("url", ""),
                    "uploadedAt": doc.get("uploaded_at", ""),
                }
            )
    return documents

def transform_compensation(compensation_data):
    """Transform compensation JSONB to Compensation object"""
    if isinstance(compensation_data, str):
        compensation_data = json.loads(compensation_data)

    if compensation_data:
        return {
            "currentCompensation": str(compensation_data.get("amount", "")),
            "description": compensation_data.get("description", ""),
            "lastUpdated": compensation_data.get(
                "last_updated",
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
            "isAIGenerated": compensation_data.get("is_ai_generated", False),
        }

    return {
        "currentCompensation": "",
        "description": "",
        "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isAIGenerated": False,
    }

def transform_sector_expertise(sectors_data):
    """Transform sectors JSONB to SectorExpertise object"""
    if isinstance(sectors_data, str):
        sectors_data = json.loads(sectors_data)

    if not sectors_data:
        return {"primary": [], "secondary": []}

    return {
        "primary": sectors_data.get("primary", []),
        "secondary": sectors_data.get("secondary", []),
    }

def transform_skills_detailed(skills_data):
    """Transform skills for detailed Candidate schema"""
    if isinstance(skills_data, str):
        skills_data = json.loads(skills_data)

    skills = []
    for i, skill in enumerate(skills_data):
        if isinstance(skill, str):
            skills.append({"id": str(i), "name": skill, "category": "General"})
        elif isinstance(skill, dict):
            skills.append(
                {
                    "id": skill.get("id", str(i)),
                    "name": skill.get("name", ""),
                    "category": skill.get("category", "General"),
                }
            )

    return skills

def transform_awards(awards_data):
    """Transform awards JSONB to Awards object"""
    if isinstance(awards_data, str):
        awards_data = json.loads(awards_data)

    if not awards_data:
        return {
            "content": "",
            "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "isAIGenerated": False,
            "summaries": [],
            "items": [],
        }

    award_items = []
    for i, award in enumerate(awards_data):
        if isinstance(award, dict):
            award_items.append(
                {
                    "id": str(i),
                    "title": award.get("title", ""),
                    "description": award.get("description", ""),
                    "date": award.get("date", ""),
                    "type": award.get("type", ""),
                    "imageUrl": award.get("image_url"),
                }
            )

    return {
        "content": "",
        "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isAIGenerated": False,
        "summaries": [],
        "items": award_items,
    }

def transform_work_history(work_experience_data):
    """Transform work experience to WorkHistoryItem objects"""
    if isinstance(work_experience_data, str):
        work_experience_data = json.loads(work_experience_data)
    work_history = []
    for i, exp in enumerate(work_experience_data):
        if isinstance(exp, dict):
            start_date, end_date = "", ""
            if exp.get("startDate"):
                if isinstance(exp.get("startDate"), dict):
                    start_date_obj = exp.get("startDate")
                    start_date = (
                        f"{month_abbr[start_date_obj.get('month')]} {start_date_obj.get('year')}"
                        if start_date_obj
                        else ""
                    )
                else:
                    start_date = exp.get("startDate")
            if exp.get("endDate"):
                if isinstance(exp.get("endDate"), dict):
                    end_date_obj = exp.get("endDate")
                    end_date = (
                        f"{month_abbr[end_date_obj.get('month')]} {end_date_obj.get('year')}"
                        if end_date_obj
                        else ""
                    )
                else:
                    end_date = exp.get("endDate")

            work_history.append(
                {
                    "id": str(i),
                    "title": exp.get("title", ""),
                    "company": exp.get("companyName", ""),
                    "startDate": start_date,
                    "endDate": end_date,
                    "isCurrent": exp.get("endDate") is None or end_date == "Present",
                    "description": exp.get("description", ""),
                }
            )
    return work_history

def transform_motivating_factors(motivating_factors_data):
    """Transform motivating factors JSONB"""
    if isinstance(motivating_factors_data, str):
        motivating_factors_data = json.loads(motivating_factors_data)

    content = ""
    if isinstance(motivating_factors_data, list):
        content = ", ".join([str(factor) for factor in motivating_factors_data])
    elif isinstance(motivating_factors_data, dict):
        content = str(motivating_factors_data.get("content", ""))

    return {
        "content": content,
        "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "isAIGenerated": False,
    }


def transform_projects(projects_data):
    """Transform projects JSONB to Project objects"""
    if isinstance(projects_data, str):
        projects_data = json.loads(projects_data)
    if not projects_data:
        return []

    projects = []
    for i, project in enumerate(projects_data):
        if isinstance(project, dict):
            projects.append(
                {
                    "id": str(i),
                    "title": project.get("title", ""),
                    "description": project.get("description", ""),
                    "startDate": project.get("start_date", ""),
                    "endDate": project.get("end_date", ""),
                    "technologies": project.get("technologies", []),
                    "url": project.get("url", ""),
                    "imageUrl": project.get("image_url", ""),
                }
            )

    return projects


def calculate_profile_completion_score(user_profile):
    """Calculate profile completion score based on UI fields and work history only"""
    total_fields = 0
    completed_fields = 0

    # Basic Profile Information (40% weight) - Based on UI form
    basic_fields = [
        "first_name",
        "last_name",
        "email",
        "phone_number",
        "avatar_url",
        "current_title",
        "current_organisation",
        "location",
        "age_bracket",
        "gender",
        "linkedin_profile_url",
    ]

    for field in basic_fields:
        total_fields += 1
        if user_profile.get(field) and str(user_profile.get(field)).strip():
            completed_fields += 1

    # Professional Information (60% weight) - Work history and skills
    professional_fields = ["work_experience", "skills"]

    for field in professional_fields:
        total_fields += 1
        value = user_profile.get(field)
        if value:
            if isinstance(value, str) and value.strip():
                completed_fields += 1
            elif isinstance(value, (list, dict)) and len(value) > 0:
                completed_fields += 1
            else:
                completed_fields += 1

    # No additional section used for scoring per latest requirement

    # Calculate percentage
    if total_fields == 0:
        return 0

    completion_percentage = (completed_fields / total_fields) * 100

    # Determine completion level using match-case
    match completion_percentage:
        case p if p >= 90:
            completion_level = "EXCELLENT"
        case p if p >= 75:
            completion_level = "GOOD"
        case p if p >= 60:
            completion_level = "FAIR"
        case p if p >= 40:
            completion_level = "BASIC"
        case _:
            completion_level = "MINIMAL"

    return {
        "score": round(completion_percentage, 1),
        "level": completion_level,
        "completedFields": completed_fields,
        "totalFields": total_fields,
        "breakdown": {
            "basicProfile": {
                "completed": sum(
                    1
                    for field in basic_fields
                    if user_profile.get(field) and str(user_profile.get(field)).strip()
                ),
                "total": len(basic_fields),
                "weight": 40,
            },
            "professionalInfo": {
                "completed": sum(
                    1
                    for field in professional_fields
                    if user_profile.get(field)
                    and (
                        (
                            isinstance(user_profile.get(field), str)
                            and user_profile.get(field).strip()
                        )
                        or (
                            isinstance(user_profile.get(field), (list, dict))
                            and len(user_profile.get(field)) > 0
                        )
                    )
                ),
                "total": len(professional_fields),
                "weight": 60,
            },
        },
    }


@router.get(
    "/{user_id}", response_model=UserProfileResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("100/minute")
# @audit_api_call(
#     action_type="READ",
#     data_classification="confidential",
#     compliance_tags=[
#         "gdpr",  # Accessing other user profile data involves personal information
#         "pii",  # User profile contains personally identifiable information
#         "audit_required",  # Profile access must be logged for compliance and security audits
#     ],
#     table_name="organization_members",
#     category="USER_PROFILE",
# )
async def get_user_by_id(
    user_id: str,
    request: Request,
    commons: Tuple[dict, Any] = Depends(get_common_dependencies),
):
    """
    Get a user's profile by user_id (async, sequential)
    """
    current_user, db_conn = commons  # Destructure the tuple
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        ("GET /%s request started - Request ID: %s, ",user_id,request_id),
        ("User ID: %s, ",current_user.get('user_id')),
        ("Organization ID: %s, ",current_user.get('organization_id')),
        ("Target User ID: %s",user_id)
    )

    user_context = extract_user_context(current_user)
    logger.debug(
        ("User context extracted - Request ID: %s, ",request_id),
        ("Email: %s, Organization ID: %s, ",user_context.email,user_context.organization_id),
        ("User Type: %s",user_context.user_type)
    )

    # Only organization members can access other user profiles
    if user_context.user_type != "organization_member":
        logger.warning(
            ("Non-organization member trying to access user profile - Request ID: %s, ",request_id),
            ("User Type: %s, Target User ID: %s",user_context.user_id,user_id)
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organization members can access user profiles",
        )

    await require_permission(
        permission_code="settings.users.manage",
        user_context=user_context,
        db_conn=db_conn,
        action_description="access user profiles",
    )
    logger.debug(
        ("User permissions validated for profile access - Request ID: %s, ",request_id),
        ("Target User ID: %s",user_id)
    )

    # Set audit context for user profile access
    # This endpoint only handles organization members, so audit table is always organization_members
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin accessed user profile: {user_id}"
    request.state.audit_risk_level = "medium"
    logger.debug(
        ("Audit context set for user profile access - Request ID: %s, ",request_id),
        ("Target User ID: %s",user_id)
    )

    user_profile = await fetch_user_profile(
        db_conn, user_id, user_context.organization_id
    )
    logger.debug(
        ("User profile fetched - Request ID: %s, ",request_id),
        ("Target User ID: %s, Profile found: %s",user_id,user_profile is not None)
    )

    if not user_profile:
        logger.warning(
            ("User not found in organization - Request ID: %s, ",request_id),
            ("Target User ID: %s, Organization ID: %s",user_id,user_context.organization_id)
        )
        raise HTTPException(
            status_code=404,
            detail="User not found in organization",
        )

    permissions_data = await fetch_user_permissions(
        db_conn, user_profile["role_id"], user_context.organization_id
    )
    logger.debug(
        ("User permissions fetched - Request ID: %s, ",request_id),
        (
            "Target User ID: %s, Permissions count: %s",
            user_id,len(permissions_data) if permissions_data else 0
        )
    )

    print("permission_id")
    print(permissions_data)
    permissions = [
        PermissionInfo(
            permission_id=str(p.permission_id),
            permission_name=p.permission_name,
            permission_code=p.permission_code,
            category=p.category,
        )
        for p in permissions_data
    ]
    # permissions = [
    #     PermissionInfo(
    #         permission_code=str(p["permission_id"]),
    #         permission_name=p["permission_name"],
    #         permission_code=p["permission_code"],
    #         category=p["category"],
    #     )
    #     for p in permissions_data
    # ]
    print("permission_id")

    role_info = RoleInfoWithDescription(
        role_id=str(user_profile["role_id"]),
        role_name=user_profile["role_name"],
        description=user_profile.get("role_description", ""),
    )
    logger.debug(
        ("Profile data formatted - Request ID: %s, ",request_id),
        (
            "Target User ID: %s, Role: %s, Permissions: %s",
            user_id,role_info.role_name,len(permissions)
        )
    )

    # Set audit data for user profile access
    request.state.raw_audit_new_data = {
        "target_user_id": str(user_profile["user_id"]),
        "target_email": user_profile["email"],
        "target_full_name": user_profile["full_name"],
        "organization_id": str(user_profile["organization_id"]),
        "role_id": str(user_profile["role_id"]),
        "role_name": user_profile["role_name"],
        "status": user_profile["status"],
        "permission_count": len(permissions),
        "accessed_by_user_id": user_context.user_id,
        "accessed_by_email": user_context.email,
        "access_timestamp": datetime.now().isoformat(),
    }

    profile_data = create_user_profile_data(
        user_profile=user_profile,
        user_type="organization_member",  # This endpoint only handles organization members
        role_info=role_info,
        permissions=permissions
    )

    logger.info(
        ("GET /%s request completed successfully - Request ID: %s, ",user_id,request_id),
        ("Target User ID: %s, Email: %s, ",user_id,user_profile['email']),
        ("Permissions: %s, Status Code: %s",len(permissions),status.HTTP_200_OK)
    )

    return UserProfileResponse(
        status_code=200,
        message="User profile retrieved successfully",
        data=profile_data,
    )
