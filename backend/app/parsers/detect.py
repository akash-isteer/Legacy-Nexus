"""Source language detection by extension, with content sniffing as a fallback."""
import re
from pathlib import Path

EXTENSION_LANGUAGE = {
    ".cbl": "cobol", ".cob": "cobol", ".cobol": "cobol", ".pco": "cobol", ".sqb": "cobol",
    ".cpy": "copybook", ".copy": "copybook", ".cpb": "copybook",
    ".jcl": "jcl", ".prc": "jcl", ".proc": "jcl",
    ".sql": "sql", ".ddl": "sql", ".db2": "sql", ".pls": "sql", ".pks": "sql", ".pkb": "sql",
    ".yaml": "api", ".yml": "api", ".json": "api",
    ".md": "doc", ".txt": "doc", ".rst": "doc",
    ".java": "java",
    ".cs": "csharp",
    ".py": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".sh": "shell", ".bash": "shell", ".ksh": "shell",
}

# Dependency, build and tool folders: third-party or generated code, never part of the application.
IGNORED_DIRS = {
    "node_modules", "bower_components", "venv", ".venv", "site-packages", "__pycache__", "dist", "build",
    "target", "obj", ".git", ".svn", ".idea", ".vscode", "vendor", ".gradle", ".mvn",
    "coverage", ".next", ".nuxt", ".tox", ".mypy_cache", ".pytest_cache",
}
YAML_SUFFIXES = {".yaml", ".yml"}


def is_ignored_path(rel_parts: tuple[str, ...]) -> bool:
    """True for files inside dependency/build folders or generated bundles (e.g. *.min.js)."""
    if any(part in IGNORED_DIRS for part in rel_parts[:-1]):
        return True
    name = rel_parts[-1].lower() if rel_parts else ""
    return name.endswith((".min.js", ".bundle.js", ".map", ".d.ts", ".designer.cs", ".g.cs"))

_COBOL_HINT = re.compile(r"^\s{0,7}\d*\s*(IDENTIFICATION|ID)\s+DIVISION|PROGRAM-ID\.", re.I | re.M)
_JCL_HINT = re.compile(r"^//\S*\s+(JOB|EXEC|DD)\b", re.M)
_SQL_HINT = re.compile(r"^\s*(CREATE|ALTER|INSERT|UPDATE|SELECT)\s", re.I | re.M)
_API_HINT = re.compile(r"^\s*[\"']?(openapi|swagger)[\"']?\s*:", re.M)
_COPYBOOK_HINT = re.compile(r"^\s{0,7}\d*\s*0[1-9]\s+[A-Z0-9-]+", re.I | re.M)


def detect_language(path: Path, text: str) -> str | None:
    """Return the language for a source file, or None if it should be ignored."""
    lang = EXTENSION_LANGUAGE.get(path.suffix.lower())
    head = text[:20_000]
    if lang == "api":
        if _API_HINT.search(head):
            return "api"
        return "config" if path.suffix.lower() in YAML_SUFFIXES else None
    if lang == "copybook" and _COBOL_HINT.search(head):
        return "cobol"
    if lang:
        return lang
    if head.startswith("#!") and re.match(r"#!.*\b(ba|k|z)?sh\b", head):
        return "shell"
    # Extensionless members (common in mainframe exports)
    if _JCL_HINT.search(head):
        return "jcl"
    if _COBOL_HINT.search(head):
        return "cobol"
    if _SQL_HINT.search(head):
        return "sql"
    if _COPYBOOK_HINT.search(head) and "PIC" in head.upper():
        return "copybook"
    return None
