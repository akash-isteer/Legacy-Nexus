"""Project source storage on disk: streamed uploads and safe, filtered archive extraction.

Uploads are streamed to disk in chunks (never held in memory), and archives are extracted selectively:
only files the analyzers can read are kept, so release bundles full of binaries, images and
installers do not consume disk or count against the extraction limit.
"""
from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.config import get_settings
from app.parsers.detect import EXTENSION_LANGUAGE, is_ignored_path

CHUNK = 1024 * 1024


class UploadRejected(ValueError):
    pass


class UploadTooLarge(UploadRejected):
    pass


@dataclass
class StoreResult:
    stored: list[str] = field(default_factory=list)
    skipped: int = 0
    skipped_bytes: int = 0

    def merge(self, other: "StoreResult") -> None:
        self.stored += other.stored
        self.skipped += other.skipped
        self.skipped_bytes += other.skipped_bytes


def project_dir(project_id: int) -> Path:
    path = get_settings().storage_dir / "projects" / str(project_id) / "src"
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_cache_dir(project_id: int) -> Path:
    """Per-project parse cache (outside src/, so it is never analysed or listed as a source file)."""
    return get_settings().storage_dir / "projects" / str(project_id) / "parse-cache"


def staging_dir(project_id: int) -> Path:
    path = get_settings().storage_dir / "projects" / str(project_id) / "staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def delete_project_files(project_id: int) -> None:
    shutil.rmtree(get_settings().storage_dir / "projects" / str(project_id), ignore_errors=True)


def safe_relative_path(name: str) -> PurePosixPath:
    """Normalise an uploaded file name and refuse anything that would escape the project folder."""
    pure = PurePosixPath(name.replace("\\", "/").lstrip("/"))
    parts = [p for p in pure.parts if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or ":" in parts[0]:
        raise UploadRejected(f"Unsafe file path: {name}")
    return PurePosixPath(*parts)


def is_analysable(name: str, size: int) -> bool:
    """Keep known source extensions and extension-less mainframe members; skip binaries and large blobs."""
    rel = PurePosixPath(name)
    if rel.name.startswith(".") or (rel.parts and rel.parts[0] == "__MACOSX") or is_ignored_path(rel.parts):
        return False
    per_file = get_settings().mb_limit(get_settings().max_source_file_mb)
    if per_file is not None and size > per_file:
        return False
    suffix = rel.suffix.lower()
    return suffix in EXTENSION_LANGUAGE or suffix == ""


async def stream_to_staging(project_id: int, upload, remaining_bytes: int | None) -> tuple[Path, int]:
    """Copy an UploadFile to a staging file in 1 MB chunks. Raises UploadTooLarge past the budget (None = no limit)."""
    fd, tmp = tempfile.mkstemp(dir=staging_dir(project_id), suffix=".part")
    written = 0
    try:
        with open(fd, "wb") as out:
            while chunk := await upload.read(CHUNK):
                written += len(chunk)
                if remaining_bytes is not None and written > remaining_bytes:
                    raise UploadTooLarge(f"Upload exceeds the {get_settings().max_upload_mb} MB limit")
                out.write(chunk)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return Path(tmp), written


def store_staged(project_id: int, filename: str, staged: Path) -> StoreResult:
    """Move one staged upload into the project, expanding .zip archives."""
    settings = get_settings()
    root = project_dir(project_id)
    try:
        if filename.lower().endswith(".zip"):
            return _extract_zip(root, staged, settings.mb_limit(settings.max_extracted_mb),
                                settings.max_files_per_project or None)
        rel = safe_relative_path(filename)
        size = staged.stat().st_size
        if not is_analysable(rel.as_posix(), size):
            return StoreResult(skipped=1, skipped_bytes=size)
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), target)
        return StoreResult(stored=[rel.as_posix()])
    finally:
        staged.unlink(missing_ok=True)


def save_upload(project_id: int, filename: str, data: bytes) -> list[str]:
    """Store in-memory bytes (used by tests and small programmatic uploads)."""
    fd, tmp = tempfile.mkstemp(dir=staging_dir(project_id), suffix=".part")
    with open(fd, "wb") as out:
        out.write(data)
    return store_staged(project_id, filename, Path(tmp)).stored


def _extract_zip(root: Path, archive_path: Path, max_bytes: int | None, max_files: int | None) -> StoreResult:
    result = StoreResult()
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise UploadRejected("The archive is not a valid .zip file") from exc
    with archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        keep = [m for m in members if is_analysable(m.filename, m.file_size)]
        result.skipped = len(members) - len(keep)
        result.skipped_bytes = sum(m.file_size for m in members) - sum(m.file_size for m in keep)
        if max_files is not None and len(keep) > max_files:
            raise UploadRejected(f"Archive has {len(keep)} source files; the limit is {max_files}")
        if max_bytes is not None and sum(m.file_size for m in keep) > max_bytes:
            raise UploadRejected(
                f"Source files in the archive expand beyond the {max_bytes // (1024 * 1024)} MB extraction limit")
        for m in keep:
            rel = safe_relative_path(m.filename)
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(m) as src, open(target, "wb") as dst:
                # Copy with a hard cap so a member that lies about its size cannot exceed it.
                remaining = m.file_size + 1
                while remaining > 0 and (chunk := src.read(min(CHUNK, remaining))):
                    remaining -= len(chunk)
                    dst.write(chunk)
                if remaining <= 0:
                    raise UploadRejected(f"Archive member {m.filename} is larger than declared")
            result.stored.append(rel.as_posix())
    return result


def copy_tree(source: Path, project_id: int) -> None:
    shutil.copytree(source, project_dir(project_id), dirs_exist_ok=True)


def read_source_lines(project_id: int, rel_path: str) -> list[str]:
    root = project_dir(project_id).resolve()
    target = (root / safe_relative_path(rel_path)).resolve()
    if root not in target.parents or not target.is_file():
        raise FileNotFoundError(rel_path)
    from app.engine.model import read_text

    return read_text(target).split("\n")
