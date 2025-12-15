"""Translations for custom responses"""

import json
from pathlib import Path
from typing import Any


class Translator:
    """Handles translation of messages based on language code."""

    def __init__(self, default_language: str = "en", extra_paths: list[Path] | None = None):
        self.default_language = default_language
        self.translations: dict[str, dict[str, Any]] = {}
        self.translation_paths: list[Path] = []

        # Load default shared locales first
        self.add_translation_path(Path(__file__).parent / "locales", reload=False)

        for custom_path in extra_paths or []:
            self.add_translation_path(custom_path, reload=False)

        self._reload_translations()

    def _reload_translations(self) -> None:
        """Reload translations from all registered directories."""
        self.translations.clear()
        for path in self.translation_paths:
            self._load_from_path(path)

    @staticmethod
    def _deep_update(target: dict[str, Any], incoming: dict[str, Any]) -> None:
        """Recursively merge dictionaries so shared keys aren't clobbered."""
        for key, value in incoming.items():
            if key in target and isinstance(target[key], dict) and isinstance(value, dict):
                Translator._deep_update(target[key], value)
            else:
                target[key] = value

    def _load_from_path(self, path: Path) -> None:
        """Load translation files from a specific directory."""
        try:
            for file_path in path.glob("*.json"):
                language_code = file_path.stem
                with open(file_path, encoding="utf-8") as f:
                    data = json.load(f)
                    language_dict = self.translations.setdefault(language_code, {})
                    Translator._deep_update(language_dict, data)
        except FileNotFoundError:
            # Locale directory is optional
            pass
        except (json.JSONDecodeError, OSError):
            # Ignore malformed files but keep others loading
            pass

    def add_translation_path(self, path: Path | str, reload: bool = True) -> None:
        """Register an additional directory for translations."""
        resolved = Path(path)
        if resolved in self.translation_paths:
            return
        self.translation_paths.append(resolved)
        if reload:
            self._load_from_path(resolved)

    def get(self, key: str, language: str | None = None, **params) -> str:
        """Get a translated string for the given key and language.

        Args:
        ----
            key: The translation key (can be dot-separated for nested access)
            language: The language code
            **params: Parameters to format into the translated string

        Returns:
        -------
            The translated string or the key itself if not found
        """
        language = language or self.default_language

        # Fall back to default language if requested language not available
        if language not in self.translations:
            language = self.default_language

        # Navigate nested keys (e.g., "errors.not_found")
        value = self.translations.get(language, {})
        parts = key.split(".")

        for part in parts:
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                # Key not found, return the original key
                return key

        # If we got a string, format it with the provided parameters
        if isinstance(value, str):
            try:
                return value.format(**params)
            except KeyError:
                # If formatting fails, return the unformatted string
                return value

        # If the value is not a string, return the key
        return key


def register_translation_path(path: Path | str) -> None:
    """Expose ability for services to register their locale directories."""
    translator.add_translation_path(Path(path))


# Create a global translator instance
translator = Translator()
