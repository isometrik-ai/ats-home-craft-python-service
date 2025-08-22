# pylint: disable=logging-fstring-interpolation
"""
User Profile API Module
This module provides user profile operations including getting own profile and getting user by ID.
All endpoints include proper authentication, validation, and database operations.
"""

from datetime import datetime, timezone
import uuid
import json

from fastapi import APIRouter, HTTPException, status, Depends, Request

# Logger import
from apps.user_service.app.dependencies.logger import get_logger

from apps.user_service.app.dependencies.common_utils import (
    extract_user_context,
    require_permission,
)
from apps.user_service.app.dependencies.user_utils import (
    format_permissions,
    format_timestamps,
    update_user_activity,
    fetch_user_profile,
    fetch_user_permissions,
)

# Schema imports
from apps.user_service.app.schemas.users import (
    UserProfileResponse,
    UserProfileData,
    PermissionInfo,
    RoleInfoWithDescription,
)

from apps.user_service.app.app_instance import limiter

# Audit logging imports
from apps.user_service.app.dependencies.audit_logs.audit_decorator import (
    audit_api_call,
)

# Local imports
from libs.shared_db.postgres_db.db import get_async_db_conn
from libs.shared_middleware.jwt_auth import get_user_from_auth

# Create router for user profile endpoints
router = APIRouter(prefix="", tags=["User Profile"])

# Initialize logger for user profile module
logger = get_logger("user-profile-api")
logger.info("User Profile API module loaded")


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
    request: Request,  # pylint: disable=unused-argument
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
):
    """
    Retrieve the authenticated user's profile (optimized + async).
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        f"GET /profile request started - Request ID: {request_id}, "
        f"User ID: {current_user.get('user_id')}, "
        f"Organization ID: {current_user.get('organization_id')}"
    )

    user_context = extract_user_context(current_user)
    logger.debug(
        f"User context extracted - Request ID: {request_id}, "
        f"Email: {user_context.email}, Organization ID: {user_context.organization_id}, "
        f"User Type: {user_context.user_type}"
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
    #     f"Audit context set for profile access - Request ID: {request_id}, "
    #     f"Email: {user_context.email}, Audit Table: {request.state.audit_table}"
    # )

    # Handle different user types
    if user_context.user_type == "organization_member":
        # Original flow for organization members
        profile_query = """
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

        user_profile = await db_conn.fetchrow(
            profile_query,
            user_context.user_id,
            user_context.organization_id,
        )
        logger.debug(
            f"User profile retrieved from database - Request ID: {request_id}, "
            f"Profile found: {user_profile is not None}"
        )

        if not user_profile:
            logger.warning(
                f"User profile not found - Request ID: {request_id}, "
                f"User ID: {user_context.user_id}, Organization ID: {user_context.organization_id}"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User profile not found or access denied to organization",
            )

        if user_profile["email"].lower() != user_context.email.lower():
            logger.warning(
                f"Token email does not match user profile - Request ID: {request_id}, "
                f"Token email: {user_context.email}, Profile email: {user_profile['email']}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token email does not match user profile",
            )

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
            f"User permissions retrieved - Request ID: {request_id}, "
            f"Permissions count: {len(permissions_data)}"
        )

        await update_user_activity(
            db_conn,
            user_context.user_id,
            user_context.organization_id,
        )
        logger.debug(
            f"User activity updated - Request ID: {request_id}, "
            f"User ID: {user_context.user_id}"
        )

        role_info = RoleInfoWithDescription(
            role_id=str(user_profile["role_id"]),
            role_name=user_profile["role_name"],
            description=user_profile.get("role_description", ""),
        )
        permissions = format_permissions(permissions_data)
        joined_at, last_active_at = format_timestamps(user_profile)
        logger.debug(
            f"Profile data formatted - Request ID: {request_id}, "
            f"Role: {role_info.role_name}, Permissions: {len(permissions)}"
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

        profile_data = UserProfileData(
            user_id=str(user_profile["user_id"]),
            email=user_profile["email"],
            full_name=user_profile["full_name"],
            first_name=user_profile["first_name"],
            last_name=user_profile["last_name"],
            avatar_url=user_profile["avatar_url"],
            phone=user_profile["phone"],
            timezone=user_profile["timezone"] or "UTC",
            status=user_profile["status"],
            joined_at=joined_at,
            last_active_at=last_active_at,
            organization_id=str(user_profile["organization_id"]),
            user_type=user_context.user_type,
            role=role_info,
            permissions=permissions,
        )

    elif user_context.user_type == "client":
        # Handle client user type
        # Extract from client_members table using user_id from JWT
        permissions = []
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
            f"Client member profile retrieved from database - Request ID: {request_id}, "
            f"Profile found: {user_profile is not None}"
        )

        if not user_profile:
            logger.warning(
                f"Client member profile not found - Request ID: {request_id}, "
                f"User ID: {user_context.user_id}, Organization ID: {user_context.organization_id}"
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

        profile_data = UserProfileData(
            user_id=str(user_profile["id"]),
            email=user_profile["email"]
            or user_context.email,  # Use profile email or fallback to JWT email
            full_name=f"{user_profile['first_name']} {user_profile['last_name']}".strip(),
            first_name=user_profile["first_name"],
            last_name=user_profile["last_name"],
            avatar_url=None,  # client_members doesn't have avatar_url
            phone=user_profile["phone"],
            timezone="UTC",
            status="active",
            joined_at=(
                user_profile["joined_at"].isoformat()
                if user_profile["joined_at"]
                else datetime.now().isoformat()
            ),
            last_active_at=(
                user_profile["last_active_at"].isoformat()
                if user_profile["last_active_at"]
                else None
            ),
            organization_id=str(user_profile["organization_id"]),
            user_type=user_context.user_type,
            role=None,
            permissions=[],
        )

    elif user_context.user_type == "candidate":
        # Handle candidate user type
        permissions = []
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
            f"Candidate profile retrieved from database - Request ID: {request_id}, "
            f"Profile found: {user_profile is not None}"
        )

        if not user_profile:
            logger.warning(
                f"Candidate profile not found - Request ID: {request_id}, "
                f"User ID: {user_context.user_id}, Organization ID: {user_context.organization_id}"
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

        # Transform candidate data similar to get_detailed_candidate_by_id
        candidate_data = {
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

        profile_data = UserProfileData(
            user_id=str(user_profile["candidate_id"]),
            email=user_profile["email"],
            full_name=f"{user_profile['first_name']} {user_profile['last_name']}".strip(),
            first_name=user_profile["first_name"],
            last_name=user_profile["last_name"],
            avatar_url=user_profile["avatar_url"],
            phone=user_profile["phone"],
            timezone="UTC",
            status="active",
            joined_at=(
                user_profile["joined_at"].isoformat()
                if user_profile["joined_at"]
                else datetime.now().isoformat()
            ),
            last_active_at=(
                user_profile["last_active_at"].isoformat()
                if user_profile["last_active_at"]
                else None
            ),
            organization_id=str(user_profile["organization_id"]),
            user_type=user_context.user_type,
            role=None,
            permissions=[],
            candidate_data=candidate_data,  # Add candidate data to profile
        )

    # User type validation is now handled in extract_user_context function
    # This else block should never be reached due to validation in common_utils

    logger.info(
        f"GET /profile request completed successfully - Request ID: {request_id}, "
        f"User ID: {user_context.user_id}, Email: {user_context.email}, "
        f" Status Code: 200"
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

    months_abs = {
        1: "Jan",
        2: "Feb",
        3: "Mar",
        4: "Apr",
        5: "May",
        6: "Jun",
        7: "Jul",
        8: "Aug",
        9: "Sep",
        10: "Oct",
        11: "Nov",
        12: "Dec",
    }

    work_history = []
    for i, exp in enumerate(work_experience_data):
        if isinstance(exp, dict):
            temp = exp.get("startDate")
            startDate = ""
            endDate = ""
            if temp:
                if isinstance(temp, dict):
                    startDate = (
                        f"{months_abs.get(exp.get('startDate').get('month'))} {exp.get('startDate').get('year')}"
                        if exp.get("startDate")
                        else ""
                    )
                else:
                    startDate = temp

            endTemp = exp.get("endDate")

            if endTemp:
                if isinstance(endTemp, dict):
                    endDate = (
                        f"{months_abs.get(exp.get('endDate').get('month'))} {exp.get('endDate').get('year')}"
                        if exp.get("endDate")
                        else ""
                    )
                else:
                    endDate = endTemp

            work_history.append(
                {
                    "id": str(i),
                    "title": exp.get("title", ""),
                    "company": exp.get("companyName", ""),
                    "startDate": startDate,
                    "endDate": endDate,
                    "isCurrent": exp.get("endDate") == None or endDate == "Present",
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

    # Determine completion level
    if completion_percentage >= 90:
        completion_level = "EXCELLENT"
    elif completion_percentage >= 75:
        completion_level = "GOOD"
    elif completion_percentage >= 60:
        completion_level = "FAIR"
    elif completion_percentage >= 40:
        completion_level = "BASIC"
    else:
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
    request: Request,  # pylint: disable=unused-argument
    current_user: dict = Depends(get_user_from_auth),
    db_conn=Depends(get_async_db_conn),
):
    """
    Get a user's profile by user_id (async, sequential)
    """
    # Generate request ID for tracking
    request_id = str(uuid.uuid4())
    logger.info(
        f"GET /{user_id} request started - Request ID: {request_id}, "
        f"User ID: {current_user.get('user_id')}, "
        f"Organization ID: {current_user.get('organization_id')}, "
        f"Target User ID: {user_id}"
    )

    user_context = extract_user_context(current_user)
    logger.debug(
        f"User context extracted - Request ID: {request_id}, "
        f"Email: {user_context.email}, Organization ID: {user_context.organization_id}, "
        f"User Type: {user_context.user_type}"
    )

    # Only organization members can access other user profiles
    if user_context.user_type != "organization_member":
        logger.warning(
            f"Non-organization member trying to access user profile - Request ID: {request_id}, "
            f"User Type: {user_context.user_type}, Target User ID: {user_id}"
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
        f"User permissions validated for profile access - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    # Set audit context for user profile access
    # This endpoint only handles organization members, so audit table is always organization_members
    request.state.audit_table = "organization_members"
    request.state.audit_requested_id = user_id
    request.state.audit_description = f"Admin accessed user profile: {user_id}"
    request.state.audit_risk_level = "medium"
    logger.debug(
        f"Audit context set for user profile access - Request ID: {request_id}, "
        f"Target User ID: {user_id}"
    )

    user_profile = await fetch_user_profile(
        db_conn, user_id, user_context.organization_id
    )
    logger.debug(
        f"User profile fetched - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Profile found: {user_profile is not None}"
    )

    if not user_profile:
        logger.warning(
            f"User not found in organization - Request ID: {request_id}, "
            f"Target User ID: {user_id}, Organization ID: {user_context.organization_id}"
        )
        raise HTTPException(
            status_code=404,
            detail="User not found in organization",
        )

    permissions_data = await fetch_user_permissions(
        db_conn, user_profile["role_id"], user_context.organization_id
    )
    logger.debug(
        f"User permissions fetched - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Permissions count: "
        f"{len(permissions_data) if permissions_data else 0}"
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
        f"Profile data formatted - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Role: {role_info.role_name}, Permissions: {len(permissions)}"
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

    profile_data = UserProfileData(
        user_id=str(user_profile["user_id"]),
        email=user_profile["email"],
        full_name=user_profile["full_name"],
        first_name=user_profile["first_name"],
        last_name=user_profile["last_name"],
        avatar_url=user_profile["avatar_url"],
        phone=user_profile["phone"],
        timezone=user_profile["timezone"] or "UTC",
        status=user_profile["status"],
        joined_at=(
            user_profile["joined_at"].isoformat()
            if user_profile["joined_at"]
            else datetime.now().isoformat()
        ),
        last_active_at=(
            user_profile["last_active_at"].isoformat()
            if user_profile["last_active_at"]
            else None
        ),
        organization_id=str(user_profile["organization_id"]),
        user_type="organization_member",  # This endpoint only handles organization members
        role=role_info,
        permissions=permissions,
    )

    logger.info(
        f"GET /{user_id} request completed successfully - Request ID: {request_id}, "
        f"Target User ID: {user_id}, Email: {user_profile['email']}, "
        f"Permissions: {len(permissions)}, Status Code: 200"
    )

    return UserProfileResponse(
        status_code=200,
        message="User profile retrieved successfully",
        data=profile_data,
    )
