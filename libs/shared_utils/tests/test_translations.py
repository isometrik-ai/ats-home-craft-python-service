"""Unit tests for translation helper utilities."""

from __future__ import annotations

from pathlib import Path

from libs.shared_utils.translations import (
    Translator,
    register_translation_path,
    translator,
)


def test_translator_loads_extra_paths(tmp_path: Path) -> None:
    """Custom locale directories should merge into the translator."""
    locale_dir = tmp_path / "locales"
    locale_dir.mkdir()
    (locale_dir / "en.json").write_text('{"greeting": "Hello {name}"}', encoding="utf-8")
    custom = Translator(default_language="en", extra_paths=[locale_dir])
    assert custom.get("greeting", name="World") == "Hello World"


def test_register_translation_path_adds_directory(tmp_path: Path) -> None:
    """Global register helper should append service locale paths."""
    before = len(translator.translation_paths)
    register_translation_path(tmp_path)
    assert len(translator.translation_paths) == before + 1


def test_translator_returns_key_when_missing() -> None:
    """Unknown keys should fall back to the key string."""
    assert translator.get("missing.translation.key") == "missing.translation.key"


def test_add_translation_path_ignores_duplicates(tmp_path: Path) -> None:
    """Registering the same locale path twice should be a no-op."""
    custom = Translator(default_language="en")
    custom.add_translation_path(tmp_path, reload=False)
    count = len(custom.translation_paths)
    custom.add_translation_path(tmp_path, reload=False)
    assert len(custom.translation_paths) == count
