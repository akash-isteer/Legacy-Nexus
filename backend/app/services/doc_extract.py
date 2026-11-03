"""Text extraction for the document Q&A feature.

Turns an uploaded PDF, DOCX, TXT or Markdown file into a list of pages/blocks, each carrying the
text and its source location (a page number for PDFs, a heading for DOCX/Markdown). Parsing errors
and empty documents raise ExtractionError with a clear, user-facing message.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Accepted upload formats: extension -> internal format id.
SUPPORTED_FORMATS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".rst": "txt",
}


class ExtractionError(ValueError):
    """Raised when a document cannot be parsed or contains no usable text."""


@dataclass
class Block:
    """A unit of extracted text with its source location."""

    text: str
    page: int | None = None  # 1-based page number (PDF) or None
    section: str | None = None  # nearest heading (DOCX / Markdown) or None


@dataclass
class Extraction:
    blocks: list[Block]
    page_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(b.text) for b in self.blocks)


def format_for(filename: str) -> str | None:
    """Return the internal format id for a filename, or None if the extension is unsupported."""
    lower = filename.lower()
    for ext, fmt in SUPPORTED_FORMATS.items():
        if lower.endswith(ext):
            return fmt
    return None


def extract(filename: str, raw: bytes) -> Extraction:
    fmt = format_for(filename)
    if fmt is None:
        raise ExtractionError(
            "Unsupported file type. Upload a PDF, Word document (.docx), text (.txt) or Markdown (.md) file."
        )
    if not raw:
        raise ExtractionError("The uploaded file is empty.")
    if fmt == "pdf":
        extraction = _extract_pdf(raw)
    elif fmt == "docx":
        extraction = _extract_docx(raw)
    else:
        extraction = _extract_text(raw)

    if extraction.char_count < 20:
        raise ExtractionError(
            "No readable text could be extracted from this document. If it is a scanned PDF (images only), "
            "it has no selectable text to analyse."
        )
    return extraction


def _extract_pdf(raw: bytes) -> Extraction:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ExtractionError(
            "PDF support is not installed on the server. Install 'pypdf' to upload PDF files."
        ) from exc
    import io

    try:
        reader = PdfReader(io.BytesIO(raw))
    except (PdfReadError, Exception) as exc:  # pypdf raises assorted errors on malformed files
        raise ExtractionError(f"The PDF could not be read; it may be corrupted or password-protected. ({exc})") from exc

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")  # many PDFs are encrypted with an empty owner password
        except Exception:
            raise ExtractionError("The PDF is password-protected. Remove the protection and upload it again.")

    blocks: list[Block] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        text = _clean(text)
        if text:
            blocks.append(Block(text=text, page=i))
    notes = []
    if not blocks:
        raise ExtractionError(
            "This PDF has no selectable text. It is likely a scanned image; OCR is not supported."
        )
    return Extraction(blocks=blocks, page_count=len(reader.pages), notes=notes)


def _extract_docx(raw: bytes) -> Extraction:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ExtractionError(
            "Word (.docx) support is not installed on the server. Install 'python-docx' to upload .docx files."
        ) from exc
    import io

    try:
        document = docx.Document(io.BytesIO(raw))
    except Exception as exc:
        raise ExtractionError(
            f"The Word document could not be read; it may be corrupted or in the older .doc format. ({exc})"
        ) from exc

    blocks: list[Block] = []
    section: str | None = None
    for para in document.paragraphs:
        text = _clean(para.text)
        if not text:
            continue
        style = (para.style.name or "").lower() if para.style else ""
        if style.startswith("heading") or style == "title":
            section = text
            blocks.append(Block(text=text, section=section))
        else:
            blocks.append(Block(text=text, section=section))

    # Tables carry a lot of real content in specs; flatten each row into a line.
    for table in document.tables:
        for row in table.rows:
            cells = [_clean(c.text) for c in row.cells]
            line = " | ".join(c for c in cells if c)
            if line:
                blocks.append(Block(text=line, section=section))

    if not blocks:
        raise ExtractionError("The Word document contains no readable text.")
    return Extraction(blocks=blocks, page_count=0)


def _extract_text(raw: bytes) -> Extraction:
    text = None
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")

    blocks: list[Block] = []
    section: str | None = None
    for line in text.split("\n"):
        stripped = line.rstrip()
        heading = _markdown_heading(stripped)
        if heading is not None:
            section = heading
        cleaned = _clean(stripped)
        if cleaned:
            blocks.append(Block(text=cleaned, section=section))
    if not blocks:
        raise ExtractionError("The text file contains no readable text.")
    return Extraction(blocks=blocks, page_count=0)


def _markdown_heading(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("#"):
        title = stripped.lstrip("#").strip()
        return title or None
    return None


def _clean(text: str) -> str:
    """Normalise whitespace while keeping the text on one logical line per block."""
    return " ".join(text.replace(" ", " ").split()).strip()
