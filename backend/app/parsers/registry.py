"""Registry of language adapters. New languages register here and nowhere else."""
from __future__ import annotations

from app.parsers.adapters import BUILTIN_ADAPTERS
from app.parsers.base import LanguageAdapter
from app.parsers.detect import EXTENSION_LANGUAGE

_ADAPTERS: dict[str, LanguageAdapter] = {}


def register(adapter: LanguageAdapter) -> None:
    """Register an adapter; its extensions become uploadable and detectable unless already claimed."""
    if not adapter.language:
        raise ValueError("Adapter must declare a language id")
    _ADAPTERS[adapter.language] = adapter
    for ext in adapter.extensions:
        EXTENSION_LANGUAGE.setdefault(ext.lower(), adapter.language)


def get(language: str) -> LanguageAdapter | None:
    return _ADAPTERS.get(language)


def languages() -> list[str]:
    return sorted(_ADAPTERS)


for _adapter in BUILTIN_ADAPTERS:
    register(_adapter)
