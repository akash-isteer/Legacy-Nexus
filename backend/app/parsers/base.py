"""Language adapter contract.

Every source language plugs into the analysis through one adapter. An adapter does two separate things:

* ``parse(path, text)`` turns one file into a JSON-serialisable *fragment*. It is a pure function of the
  file content, so its result can be cached by content hash and reused when the file has not changed.
* ``merge(model, path, fragment)`` folds that fragment into the shared application model.

Adding a language (Java, C#, PL/I, ...) means writing one adapter and registering it in ``registry.py``;
the graph, lineage, rules and modernization stages then work on the merged model.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LanguageAdapter(ABC):
    #: Language id stored on each inventory entry (e.g. "cobol", "jcl").
    language: str = ""
    #: File extensions (lower case, with dot) this adapter claims when no built-in mapping exists.
    extensions: tuple[str, ...] = ()
    #: Bump when parse output changes shape or content, so cached fragments are not reused.
    version: str = "1"

    @abstractmethod
    def parse(self, path: str, text: str) -> Any:
        """Return a JSON-serialisable fragment for one file (or None when the file holds nothing useful)."""

    @abstractmethod
    def merge(self, model: dict, path: str, fragment: Any) -> None:
        """Add a fragment produced by ``parse`` to the application model."""

    @property
    def cache_tag(self) -> str:
        return f"{self.language}-v{self.version}"
