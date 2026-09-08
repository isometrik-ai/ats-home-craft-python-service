"""Tests for work order service locale registration."""

from pathlib import Path

from libs.shared_utils.translations import register_translation_path, translator

LOCALE_DIR = Path(__file__).resolve().parents[1] / "app" / "locales"


def test_work_order_locales_resolve_known_keys():
    """WOM locale file supplies strings for keys used by API handlers."""
    register_translation_path(LOCALE_DIR)

    assert translator.get("success.list_retrieved") == "List retrieved successfully."
    assert translator.get("errors.validation_error") == (
        "The request could not be processed because one or more fields are invalid."
    )
    assert translator.get("work_orders.success.list") == "Form templates retrieved successfully."
    assert translator.get("auth.errors.unauthorized") == (
        "Authentication is required to access this resource."
    )
