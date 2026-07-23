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


def test_translator_handles_missing_locale_dir(tmp_path: Path) -> None:
    """Missing locale directories are ignored during load."""
    missing = tmp_path / "does-not-exist"
    custom = Translator(default_language="en", extra_paths=[missing])
    assert custom.get("any.key") == "any.key"


def test_translator_ignores_malformed_json(tmp_path: Path) -> None:
    """Malformed JSON in a locale directory is ignored without raising."""
    locale_dir = tmp_path / "bad-locales"
    locale_dir.mkdir()
    (locale_dir / "en.json").write_text("{not-json", encoding="utf-8")
    custom = Translator(default_language="en")
    custom._load_from_path(locale_dir)
    assert custom.get("any.key") == "any.key"


def test_translator_loads_valid_locale_after_bad_file(tmp_path: Path) -> None:
    """Valid locale files load when placed in their own directory."""
    locale_dir = tmp_path / "fr-locales"
    locale_dir.mkdir()
    (locale_dir / "fr.json").write_text('{"hello": "Bonjour"}', encoding="utf-8")
    custom = Translator(default_language="fr", extra_paths=[locale_dir])
    assert custom.get("hello", language="fr") == "Bonjour"


def test_translator_falls_back_to_default_language() -> None:
    """Unknown language codes fall back to default_language."""
    custom = Translator(default_language="en")
    custom.translations = {"en": {"only_en": "English"}}
    assert custom.get("only_en", language="xx") == "English"


def test_translator_format_keyerror_returns_unformatted() -> None:
    """Formatting failures return the raw translated string."""
    custom = Translator(default_language="en")
    custom.translations = {"en": {"greet": "Hello {name}"}}
    assert custom.get("greet", language="en", wrong="x") == "Hello {name}"


def test_translator_non_string_leaf_returns_key() -> None:
    """Non-string translation values return the original key."""
    custom = Translator(default_language="en")
    custom.translations = {"en": {"meta": {"nested": 1}}}
    assert custom.get("meta", language="en") == "meta"


def test_deep_update_merges_nested_dicts() -> None:
    """_deep_update merges nested dictionaries without clobbering siblings."""
    target = {"errors": {"not_found": "A"}}
    incoming = {"errors": {"conflict": "B"}}
    Translator._deep_update(target, incoming)
    assert target["errors"]["not_found"] == "A"
    assert target["errors"]["conflict"] == "B"
