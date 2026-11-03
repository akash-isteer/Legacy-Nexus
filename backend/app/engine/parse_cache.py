"""Content-addressed cache of parse results, so re-analysis only re-parses files that changed.

Keys combine the adapter's language and version with the file's SHA-256. A changed file gets a new hash,
and a parser upgrade gets a new version, so stale results are never reused. The cache lives inside each
project's storage folder: it is never shared between projects or tenants.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("legacynexus.parse_cache")


class FileParseCache:
    def __init__(self, directory: Path):
        self.directory = directory
        self.hits = 0
        self.misses = 0
        self._used: set[str] = set()

    @staticmethod
    def key(tag: str, sha256: str) -> str:
        return f"{tag}-{sha256}"

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> Any | None:
        self._used.add(key)
        path = self._path(key)
        try:
            with path.open("r", encoding="utf-8") as fh:
                value = json.load(fh)
        except FileNotFoundError:
            self.misses += 1
            return None
        except (OSError, ValueError):  # corrupt or unreadable entry: parse again
            log.warning("Ignoring unreadable parse cache entry %s", path.name)
            self.misses += 1
            return None
        self.hits += 1
        return value["fragment"]

    def put(self, key: str, fragment: Any) -> None:
        self._used.add(key)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"fragment": fragment}, fh, separators=(",", ":"))
            os.replace(tmp, self._path(key))  # atomic, so readers never see half a file
        except OSError:
            log.warning("Could not write parse cache entry %s", key, exc_info=True)

    def prune(self) -> int:
        """Delete entries not used in this run (files that were changed or removed). Returns the count."""
        removed = 0
        if not self.directory.is_dir():
            return 0
        for path in self.directory.glob("*.json"):
            if path.stem not in self._used:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def stats(self) -> dict:
        return {"files_reused": self.hits, "files_parsed": self.misses}
