"""Render transactional email templates from files under app/templates/emails/."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "emails"


@lru_cache(maxsize=2)
def _get_environment(*, autoescape: bool) -> Environment:
    """Return a cached Jinja2 environment for email templates."""
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "xml"]) if autoescape else False,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_email_template(template_key: str, template_name: str, context: dict[str, Any]) -> str:
    """Render a template file for the given email key.

    Args:
        template_key: Folder name, e.g. ``unit_assignment_welcome``.
        template_name: File within the folder, e.g. ``subject.txt`` or ``body.html``.
        context: Variables passed to Jinja2.

    Returns:
        Rendered template string.
    """
    autoescape = template_name.endswith(".html")
    env = _get_environment(autoescape=autoescape)
    template = env.get_template(f"{template_key}/{template_name}")
    return template.render(**context).strip()


def render_string_template(
    template_source: str,
    context: dict[str, Any],
    *,
    autoescape: bool = True,
) -> str:
    """Compile and render a Jinja template from a string (Pug-style runtime HTML).

    Uses the same loader as file templates so ``{% extends %}`` and ``{% include %}``
    still resolve under ``app/templates/emails/``.
    """
    env = _get_environment(autoescape=autoescape)
    return env.from_string(template_source).render(**context).strip()


def render_transactional_email(
    template_key: str,
    context: dict[str, Any],
) -> tuple[str, str, str]:
    """Render subject, plain text, and HTML for a transactional email key."""
    subject = render_email_template(template_key, "subject.txt", context)
    message = render_email_template(template_key, "body.txt", context)
    html = render_email_template(template_key, "body.html", context)
    return subject, message, html


def render_transactional_email_from_db_row(
    template_key: str,
    context: dict[str, Any],
    db_row: dict[str, Any],
) -> tuple[str, str, str]:
    """Render subject/HTML from a DB row; plain text falls back to the file template."""
    subject_source = (db_row.get("subject") or "").strip()
    html_source = (db_row.get("html_content") or "").strip()
    if not html_source:
        return render_transactional_email(template_key, context)

    subject = (
        render_string_template(subject_source, context, autoescape=False)
        if subject_source
        else render_email_template(template_key, "subject.txt", context)
    )
    message = render_email_template(template_key, "body.txt", context)
    html = render_string_template(html_source, context, autoescape=True)
    return subject, message, html
