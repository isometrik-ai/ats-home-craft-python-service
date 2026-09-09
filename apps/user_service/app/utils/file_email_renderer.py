"""Load and render file-based transactional email templates (layout + body)."""

from __future__ import annotations

import html
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from libs.shared_config.app_settings import shared_settings
from libs.shared_utils.logger import get_logger

logger = get_logger(__name__)

PLACEHOLDER_RE = re.compile(r"\{\{\.([a-z][a-z0-9_]*)\}\}")
RAW_PLACEHOLDER_RE = re.compile(r"\{\{RAW:([a-z][a-z0-9_]*)\}\}")
BODY_CONTENT_TOKEN = "{{BODY_CONTENT}}"

TEMPLATES_ROOT = Path(__file__).resolve().parent.parent / "templates" / "emails"


class EmailTemplateNotFoundError(FileNotFoundError):
    """Raised when a required email template file is missing."""


@lru_cache(maxsize=64)
def _read_template_file(path: str) -> str:
    """Read and cache a template file by absolute path string."""
    file_path = Path(path)
    if not file_path.is_file():
        raise EmailTemplateNotFoundError(f"Email template not found: {file_path}")
    return file_path.read_text(encoding="utf-8")


def _template_path(*parts: str) -> Path:
    """Resolve a path under the emails template root."""
    return TEMPLATES_ROOT.joinpath(*parts)


def _substitute_placeholders(
    template: str,
    context: dict[str, str],
    *,
    escape_html: bool,
) -> str:
    """Replace {{.variable_key}} and {{RAW:key}} placeholders using the provided context."""

    def _replace_raw(match: re.Match[str]) -> str:
        key = match.group(1)
        raw = context.get(key)
        if raw is None:
            logger.warning("Missing raw email template variable: %s", key)
            return ""
        return str(raw)

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        raw = context.get(key)
        if raw is None:
            logger.warning("Missing email template variable: %s", key)
            return ""
        text = str(raw)
        return html.escape(text, quote=True) if escape_html else text

    if escape_html:
        template = RAW_PLACEHOLDER_RE.sub(_replace_raw, template)
    return PLACEHOLDER_RE.sub(_replace, template)


def default_layout_context() -> dict[str, str]:
    """Build default layout placeholder values from shared settings."""
    return {
        "app_name": shared_settings.app_name,
        "company_name": shared_settings.company_name,
        "support_email": shared_settings.company_support_email,
        "company_website": shared_settings.company_website,
        "privacy_policy_url": shared_settings.company_privacy_policy_url,
        "terms_url": shared_settings.company_terms_url,
        "current_year": str(datetime.now().year),
    }


def render_email(
    *,
    body: str,
    layout: str = "transactional",
    body_context: dict[str, str],
    layout_context: dict[str, str] | None = None,
) -> tuple[str, str, str]:
    """Render multipart email content from layout + body template files.

    Returns:
        tuple[str, str, str]: ``(plain_text, html, subject)``
    """
    merged_layout_context = default_layout_context()
    if layout_context:
        merged_layout_context.update(layout_context)

    subject_template = _read_template_file(str(_template_path("subjects", f"{body}.txt")))
    plain_body_template = _read_template_file(str(_template_path("bodies", f"{body}.txt")))
    html_body_template = _read_template_file(str(_template_path("bodies", f"{body}.html")))
    plain_layout_template = _read_template_file(str(_template_path("layouts", f"{layout}.txt")))
    html_layout_template = _read_template_file(str(_template_path("layouts", f"{layout}.html")))

    rendered_plain_body = _substitute_placeholders(
        plain_body_template,
        body_context,
        escape_html=False,
    )
    rendered_html_body = _substitute_placeholders(
        html_body_template,
        body_context,
        escape_html=True,
    )

    if BODY_CONTENT_TOKEN not in plain_layout_template:
        raise ValueError(f"Plain layout '{layout}' must include {BODY_CONTENT_TOKEN}")
    if BODY_CONTENT_TOKEN not in html_layout_template:
        raise ValueError(f"HTML layout '{layout}' must include {BODY_CONTENT_TOKEN}")

    plain_with_body = plain_layout_template.replace(BODY_CONTENT_TOKEN, rendered_plain_body)
    html_with_body = html_layout_template.replace(BODY_CONTENT_TOKEN, rendered_html_body)

    plain_text = _substitute_placeholders(
        plain_with_body,
        merged_layout_context,
        escape_html=False,
    )
    html = _substitute_placeholders(
        html_with_body,
        merged_layout_context,
        escape_html=True,
    )

    subject_context = {**merged_layout_context, **body_context}
    subject = _substitute_placeholders(
        subject_template,
        subject_context,
        escape_html=False,
    ).strip()

    return plain_text, html, subject
