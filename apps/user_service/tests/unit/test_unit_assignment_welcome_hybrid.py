"""End-to-end scenario tests: unit assignment welcome email (DB Jinja + file fallback).

Scenario (happy path):
  1. Org admin publishes trigger template ``unit_assignment_welcome`` in DB with Jinja HTML.
  2. Admin assigns unit Tower A — 1204 to contact John (john@example.com, +91 9876543210).
  3. Backend loads published DB row, renders subject/HTML at runtime, sends via Supabase.
  4. If no published DB row exists, file templates under ``app/templates/emails/`` are used.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jinja2 import TemplateSyntaxError

from apps.user_service.app.services.contact_units_service import ContactUnitsService
from apps.user_service.app.utils import email_utils

ORG_ID = "org-green-valley"
CONTACT_ID = "contact-john"

SAMPLE_DB_SUBJECT = "Welcome {{ greeting_name }} — {{ organization_name }}"
SAMPLE_DB_HTML = """
<p>Hello {{ greeting_name }},</p>
<p>Your unit <strong>{{ unit_display }}</strong> at {{ organization_name }} is ready.</p>
<p>Sign in with phone: {{ phone_display }}</p>
{% if ios_app_store_url %}
<p><a href="{{ ios_app_store_url }}">App Store</a></p>
{% endif %}
"""

FULL_CONTEXT_KWARGS = {
    "email": "john@example.com",
    "first_name": "John",
    "organization_name": "Green Valley Residency",
    "project_name": "Sunrise Towers",
    "property_location": "1 Main Street, Bengaluru, Karnataka 560001, India",
    "unit_display": "Tower A — 1204",
    "login_phone": "+91 9876543210",
    "login_email": "john@example.com",
    "ios_app_store_url": "https://apps.apple.com/app/id123",
    "android_play_store_url": None,
}


def _welcome_context() -> dict:
    return email_utils._build_unit_assignment_welcome_context(
        email=FULL_CONTEXT_KWARGS["email"],
        first_name=FULL_CONTEXT_KWARGS["first_name"],
        organization_name=FULL_CONTEXT_KWARGS["organization_name"],
        project_name=FULL_CONTEXT_KWARGS["project_name"],
        unit_display=FULL_CONTEXT_KWARGS["unit_display"],
        property_location=FULL_CONTEXT_KWARGS.get("property_location"),
        login_phone=FULL_CONTEXT_KWARGS["login_phone"],
        login_email=FULL_CONTEXT_KWARGS["login_email"],
        ios_app_store_url=FULL_CONTEXT_KWARGS["ios_app_store_url"],
        android_play_store_url=FULL_CONTEXT_KWARGS["android_play_store_url"],
        company_name=email_utils.COMMON_COMPANY_NAME,
        company_address=email_utils.COMMON_COMPANY_ADDRESS,
        privacy_policy_url=email_utils.COMMON_PRIVACY_POLICY_URL,
        terms_url=email_utils.COMMON_TERMS_URL,
    )


# --- resolve layer (success / fail) ---


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.EmailTemplateRepository")
async def test_scenario_success_db_template_renders_custom_html(mock_repo_cls) -> None:
    """SUCCESS: Published DB template → custom subject + HTML at runtime."""
    mock_repo_cls.return_value.get_published_trigger_by_name = AsyncMock(
        return_value={
            "name": "unit_assignment_welcome",
            "subject": SAMPLE_DB_SUBJECT,
            "html_content": SAMPLE_DB_HTML,
        }
    )

    subject, message, html = await email_utils.resolve_unit_assignment_welcome_content(
        db_connection=MagicMock(),
        organization_id=ORG_ID,
        context=_welcome_context(),
    )

    assert subject == "Welcome John — Green Valley Residency"
    assert "Tower A — 1204" in html
    assert "https://apps.apple.com/app/id123" in html
    assert "+91 9876543210" in message  # plain text still from file template


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.EmailTemplateRepository")
async def test_scenario_success_file_fallback_when_no_db_template(mock_repo_cls) -> None:
    """SUCCESS: No published DB row → bundled file templates used."""
    mock_repo_cls.return_value.get_published_trigger_by_name = AsyncMock(return_value=None)

    subject, message, html = await email_utils.resolve_unit_assignment_welcome_content(
        db_connection=MagicMock(),
        organization_id=ORG_ID,
        context=_welcome_context(),
    )

    assert "Green Valley Residency" in subject
    assert "Tower A — 1204" in html
    assert "+91 9876543210" in message
    assert "password" not in message.lower()


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.EmailTemplateRepository")
async def test_scenario_success_db_empty_html_falls_back_to_files(mock_repo_cls) -> None:
    """SUCCESS: Published row with blank html_content → file templates."""
    mock_repo_cls.return_value.get_published_trigger_by_name = AsyncMock(
        return_value={"subject": "Ignored", "html_content": "   "}
    )

    subject, _, html = await email_utils.resolve_unit_assignment_welcome_content(
        db_connection=MagicMock(),
        organization_id=ORG_ID,
        context=_welcome_context(),
    )

    assert "Welcome to" in subject
    assert "Tower A — 1204" in html


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.EmailTemplateRepository")
async def test_scenario_fail_invalid_jinja_in_db_template(mock_repo_cls) -> None:
    """FAIL: Broken Jinja in DB template raises during resolve."""
    mock_repo_cls.return_value.get_published_trigger_by_name = AsyncMock(
        return_value={
            "subject": "OK",
            "html_content": "{% if unclosed %}broken",
        }
    )

    with pytest.raises(TemplateSyntaxError):
        await email_utils.resolve_unit_assignment_welcome_content(
            db_connection=MagicMock(),
            organization_id=ORG_ID,
            context=_welcome_context(),
        )


# --- send layer (success / fail) ---


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.send_email", return_value=True)
@patch("apps.user_service.app.utils.email_utils.resolve_unit_assignment_welcome_content")
async def test_scenario_success_send_with_db_content(mock_resolve, mock_send) -> None:
    """SUCCESS: Org sender delivers DB-rendered content to Supabase edge function."""
    mock_resolve.return_value = (
        "Welcome John — Green Valley Residency",
        "Plain body from file",
        "<p>DB hello John at Tower A — 1204</p>",
    )

    ok = await email_utils.send_unit_assignment_welcome_email_for_org(
        db_connection=MagicMock(),
        organization_id=ORG_ID,
        **FULL_CONTEXT_KWARGS,
    )

    assert ok is True
    mock_send.assert_called_once()
    email_to, subject, plain, html, from_name = mock_send.call_args[0]
    assert email_to == "john@example.com"
    assert subject == "Welcome John — Green Valley Residency"
    assert "DB hello John" in html


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.send_email", return_value=False)
@patch("apps.user_service.app.utils.email_utils.resolve_unit_assignment_welcome_content")
async def test_scenario_fail_supabase_edge_function_returns_non_200(
    mock_resolve, mock_send
) -> None:
    """FAIL: Transport returns False → sender reports failure."""
    mock_resolve.return_value = ("Subject", "Plain", "<p>Hi</p>")

    ok = await email_utils.send_unit_assignment_welcome_email_for_org(
        db_connection=MagicMock(),
        organization_id=ORG_ID,
        **FULL_CONTEXT_KWARGS,
    )

    assert ok is False
    mock_send.assert_called_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.utils.email_utils.send_email",
    side_effect=RuntimeError("network down"),
)
@patch("apps.user_service.app.utils.email_utils.resolve_unit_assignment_welcome_content")
async def test_scenario_fail_send_email_raises(mock_resolve, mock_send) -> None:
    """FAIL: send_email exception → sender returns False (logged, not raised)."""
    mock_resolve.return_value = ("Subject", "Plain", "<p>Hi</p>")

    ok = await email_utils.send_unit_assignment_welcome_email_for_org(
        db_connection=MagicMock(),
        organization_id=ORG_ID,
        **FULL_CONTEXT_KWARGS,
    )

    assert ok is False


@pytest.mark.asyncio
@patch("apps.user_service.app.utils.email_utils.send_email", return_value=True)
@patch("apps.user_service.app.utils.email_utils.resolve_unit_assignment_welcome_content")
async def test_scenario_fail_resolve_error_returns_false(mock_resolve, mock_send) -> None:
    """FAIL: Invalid DB Jinja during resolve → sender returns False."""
    mock_resolve.side_effect = Exception("TemplateSyntaxError: unexpected '}'")

    ok = await email_utils.send_unit_assignment_welcome_email_for_org(
        db_connection=MagicMock(),
        organization_id=ORG_ID,
        **FULL_CONTEXT_KWARGS,
    )

    assert ok is False
    mock_send.assert_not_called()


# --- assign hook (success / fail / skip) ---


def _contact_units_service() -> ContactUnitsService:
    from apps.user_service.app.utils.common_utils import UserContext

    return ContactUnitsService(
        db_connection=MagicMock(),
        user_context=UserContext(
            user_id="admin-1",
            email="admin@greenvalley.com",
            organization_id=ORG_ID,
        ),
    )


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.contact_units_service.send_unit_assignment_welcome_email_for_org"
)
@patch("apps.user_service.app.services.contact_units_service.OrganizationRepository")
@patch("apps.user_service.app.services.contact_units_service.ContactsRepository")
async def test_scenario_success_full_assign_flow_uses_org_sender(
    mock_contacts_repo_cls,
    mock_org_repo_cls,
    mock_send_for_org,
) -> None:
    """SUCCESS: Admin assign → contact loaded → org-aware welcome email awaited."""
    mock_contacts_repo_cls.return_value.get_contact_for_update = AsyncMock(
        return_value={
            "first_name": "John",
            "emails": [{"email": "john@example.com", "is_primary": True}],
            "phones": [{"phone_isd_code": "+91", "phone_number": "9876543210", "is_primary": True}],
            "user_id": "auth-john",
        }
    )
    mock_org_repo_cls.return_value.get_organization_by_id = AsyncMock(
        return_value={"name": "Green Valley Residency"}
    )
    mock_send_for_org.return_value = True

    svc = _contact_units_service()
    await svc._maybe_send_unit_assignment_welcome_email(
        contact_id=CONTACT_ID,
        normalized_unit={
            "tower_name": "Tower A",
            "unit_label": "1204",
            "project": {"name": "Sunrise Towers"},
        },
    )

    mock_send_for_org.assert_awaited_once()
    kwargs = mock_send_for_org.call_args.kwargs
    assert kwargs["organization_id"] == ORG_ID
    assert kwargs["email"] == "john@example.com"
    assert kwargs["unit_display"] == "Tower A — 1204"


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.contact_units_service.send_unit_assignment_welcome_email_for_org"
)
@patch("apps.user_service.app.services.contact_units_service.OrganizationRepository")
@patch("apps.user_service.app.services.contact_units_service.ContactsRepository")
async def test_scenario_fail_assign_still_succeeds_when_email_sender_fails(
    mock_contacts_repo_cls,
    mock_org_repo_cls,
    mock_send_for_org,
) -> None:
    """FAIL (email): Sender returns False — assign hook swallows error (no raise)."""
    mock_contacts_repo_cls.return_value.get_contact_for_update = AsyncMock(
        return_value={
            "emails": [{"email": "john@example.com", "is_primary": True}],
            "phones": [],
            "user_id": "auth-john",
        }
    )
    mock_org_repo_cls.return_value.get_organization_by_id = AsyncMock(
        return_value={"name": "Green Valley Residency"}
    )
    mock_send_for_org.return_value = False

    svc = _contact_units_service()
    await svc._maybe_send_unit_assignment_welcome_email(
        contact_id=CONTACT_ID,
        normalized_unit={"code": "A-1204", "project": {"name": "Sunrise Towers"}},
    )

    mock_send_for_org.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.contact_units_service.send_unit_assignment_welcome_email_for_org"
)
@patch("apps.user_service.app.services.contact_units_service.OrganizationRepository")
@patch("apps.user_service.app.services.contact_units_service.ContactsRepository")
async def test_scenario_fail_invalid_db_template_does_not_break_assign(
    mock_contacts_repo_cls,
    mock_org_repo_cls,
    mock_send_for_org,
) -> None:
    """FAIL (template): Broken DB Jinja → sender fails; assign hook does not raise."""
    mock_contacts_repo_cls.return_value.get_contact_for_update = AsyncMock(
        return_value={
            "emails": [{"email": "john@example.com", "is_primary": True}],
            "phones": [],
            "user_id": "auth-john",
        }
    )
    mock_org_repo_cls.return_value.get_organization_by_id = AsyncMock(
        return_value={"name": "Green Valley Residency"}
    )
    mock_send_for_org.side_effect = Exception("TemplateSyntaxError")

    svc = _contact_units_service()
    await svc._maybe_send_unit_assignment_welcome_email(
        contact_id=CONTACT_ID,
        normalized_unit={"code": "A-1204", "project": {"name": "Sunrise Towers"}},
    )

    mock_send_for_org.assert_awaited_once()


@pytest.mark.asyncio
@patch(
    "apps.user_service.app.services.contact_units_service.send_unit_assignment_welcome_email_for_org"
)
@patch("apps.user_service.app.services.contact_units_service.ContactsRepository")
async def test_scenario_skip_contact_without_email(
    mock_contacts_repo_cls,
    mock_send_for_org,
) -> None:
    """SKIP: Contact has phone only — no email sent."""
    mock_contacts_repo_cls.return_value.get_contact_for_update = AsyncMock(
        return_value={
            "first_name": "John",
            "phones": [{"phone_isd_code": "+91", "phone_number": "9876543210", "is_primary": True}],
        }
    )

    svc = _contact_units_service()
    await svc._maybe_send_unit_assignment_welcome_email(
        contact_id=CONTACT_ID,
        normalized_unit={"code": "A-1204", "project": {"name": "Sunrise Towers"}},
    )

    mock_send_for_org.assert_not_called()
