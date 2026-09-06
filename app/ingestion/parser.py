"""SOW document parsing: file -> sections -> chunks with traceable identifiers.

Every chunk carries a stable key of the form ``SOW-001-S02-C03`` so that any
downstream requirement, task, or claim can cite the exact text it came from
(brief sections 5 and 19). Losing that link breaks grounding, so the parsers
preserve section and page information rather than flattening to raw text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import pdfplumber
from docx import Document
from docx.table import Table as DocxTable
from docx.text.paragraph import Paragraph as DocxParagraph

# A heading looks like "4. Commercial Requirements", "2.1 Offers", "FWD: RE: training",
# or an ALL-CAPS line. Deliberately permissive: SOW C's messy headings must still match.
_NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(\S.*)$")
_ALLCAPS_HEADING = re.compile(r"^\s*([A-Z][A-Z\s&/,'-]{3,})\s*$")
_EMAILISH_HEADING = re.compile(r"^\s*((?:FWD|RE|FW)\s*:.*)$", re.IGNORECASE)

MAX_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 40


@dataclass
class ParsedChunk:
    chunk_index: int
    text: str
    page: int | None = None
    is_table: bool = False


@dataclass
class ParsedSection:
    section_index: int
    title: str
    chunks: list[ParsedChunk] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None


@dataclass
class ParsedDocument:
    doc_key: str
    filename: str
    file_type: str
    sections: list[ParsedSection] = field(default_factory=list)
    page_count: int | None = None
    table_count: int = 0

    def chunk_key(self, section: ParsedSection, chunk: ParsedChunk) -> str:
        return f"{self.doc_key}-S{section.section_index:02d}-C{chunk.chunk_index:02d}"

    def iter_chunks(self):
        for section in self.sections:
            for chunk in section.chunks:
                yield section, chunk

    @property
    def full_text(self) -> str:
        return "\n".join(c.text for _, c in self.iter_chunks())


def looks_like_heading(line: str) -> str | None:
    """Return the normalized heading text, or None if the line is body text."""
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return None
    for pattern in (_EMAILISH_HEADING, _NUMBERED_HEADING, _ALLCAPS_HEADING):
        if pattern.match(stripped):
            # A numbered line ending in a period is usually a sentence, not a heading.
            if pattern is _NUMBERED_HEADING and stripped.endswith("."):
                return None
            return stripped
    return None


def _split_paragraph(text: str) -> list[str]:
    """Split overlong paragraphs on sentence boundaries to keep chunks citable."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(current) + len(sentence) + 1 > MAX_CHUNK_CHARS and current:
            pieces.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current.strip())
    return pieces


def _blocks_to_document(
    blocks: list[tuple[str, int | None, bool]],
    doc_key: str,
    filename: str,
    file_type: str,
    page_count: int | None,
) -> ParsedDocument:
    """Fold a flat list of (text, page, is_table) blocks into sections and chunks."""
    doc = ParsedDocument(doc_key=doc_key, filename=filename, file_type=file_type,
                         page_count=page_count)
    current = ParsedSection(section_index=0, title="(preamble)")
    chunk_index = 0

    def close(section: ParsedSection) -> None:
        if section.chunks:
            doc.sections.append(section)

    for text, page, is_table in blocks:
        text = text.strip()
        if not text:
            continue
        if not is_table and (heading := looks_like_heading(text)):
            close(current)
            current = ParsedSection(
                section_index=len(doc.sections) + 1, title=heading, page_start=page
            )
            chunk_index = 0
            continue
        for piece in _split_paragraph(text):
            if len(piece) < MIN_CHUNK_CHARS and not is_table:
                continue
            chunk_index += 1
            current.chunks.append(
                ParsedChunk(chunk_index=chunk_index, text=piece, page=page, is_table=is_table)
            )
            if page is not None:
                current.page_end = page
                if current.page_start is None:
                    current.page_start = page
        if is_table:
            doc.table_count += 1
    close(current)
    return doc


def parse_txt(path: Path, doc_key: str) -> ParsedDocument:
    raw = path.read_text(encoding="utf-8", errors="replace")
    blocks: list[tuple[str, int | None, bool]] = []
    for block in re.split(r"\n\s*\n", raw):
        block = block.strip()
        if not block or set(block) <= {"-", "=", " "}:
            continue  # underline rows from the TXT renderer
        # Strip a trailing underline row that belongs to a heading above it.
        lines = [ln for ln in block.splitlines() if set(ln.strip()) - {"-", "="}]
        if not lines:
            continue
        blocks.append(("\n".join(lines), None, False))
    return _blocks_to_document(blocks, doc_key, path.name, "txt", page_count=None)


def parse_docx(path: Path, doc_key: str) -> ParsedDocument:
    document = Document(str(path))
    blocks: list[tuple[str, int | None, bool]] = []

    # Walk body elements in document order so tables stay next to their section.
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            para = DocxParagraph(child, document)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "").lower() if para.style else ""
            if style.startswith("heading") or style == "title":
                blocks.append((text, None, False))
            else:
                blocks.append((text, None, False))
        elif tag == "tbl":
            table = DocxTable(child, document)
            rows = [
                " | ".join(_flatten_cell(cell.text) for cell in row.cells)
                for row in table.rows
            ]
            if rows:
                blocks.append(("\n".join(rows), None, True))
    return _blocks_to_document(blocks, doc_key, path.name, "docx", page_count=None)


def _flatten_cell(text: str | None) -> str:
    """One cell, one line.

    Rows are joined with newlines, so a cell that wraps inside the document
    carries a newline of its own and splits its row in two. A row broken across
    two lines reads as two rows, and the quantity in the second half then
    belongs to nothing.
    """
    return " ".join((text or "").split())


def _paragraph_gap_threshold(gaps: list[float]) -> float:
    """Vertical gap above which a line starts a new paragraph.

    PDFs carry no blank lines between paragraphs, so splitting on whitespace
    collapses a whole page into one unciteable block. Vertical spacing is the
    only reliable paragraph signal.

    The baseline is the *smallest* gap in the document, because line pitch is a
    document-wide font constant while paragraph gaps vary. Percentiles fail here:
    in a document of one-line paragraphs the breaks outnumber the wrapped lines,
    so any percentile lands on a paragraph gap and hides every break behind it.
    Erring small over-splits, which keeps chunks citable; erring large silently
    merges distinct statements into one chunk.
    """
    if not gaps:
        return float("inf")
    return min(gaps) * 1.25


def _page_blocks(
    page_no: int,
    lines: list[dict],
    tables: list[tuple[float, list[str]]],
    break_threshold: float,
) -> list[tuple[str, int | None, bool]]:
    """Fold one page's lines and tables into ordered (text, page, is_table) blocks."""
    blocks: list[tuple[str, int | None, bool]] = []
    buffer: list[str] = []
    previous_top: float | None = None
    pending = sorted(tables, key=lambda t: t[0])

    def flush() -> None:
        nonlocal buffer
        if buffer:
            blocks.append((" ".join(buffer).strip(), page_no, False))
            buffer = []

    for line in lines:
        text = line["text"].strip()
        if not text:
            continue
        # Emit any table that sits above this line before the line itself.
        while pending and pending[0][0] <= line["top"]:
            flush()
            blocks.append(("\n".join(pending.pop(0)[1]), page_no, True))
        gap = (line["top"] - previous_top) if previous_top is not None else 0.0
        previous_top = line["top"]
        if looks_like_heading(text):
            flush()
            blocks.append((text, page_no, False))
            continue
        if buffer and gap > break_threshold:
            flush()
        buffer.append(text)
    flush()
    for _, rows in pending:
        blocks.append(("\n".join(rows), page_no, True))
    return blocks


def parse_pdf(path: Path, doc_key: str) -> ParsedDocument:
    blocks: list[tuple[str, int | None, bool]] = []
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)

        # First pass: text lines and tables per page, ignoring lines that fall
        # inside a table since extract_tables already captured that content.
        # Tables carry their vertical position so they can be interleaved with
        # the surrounding prose — a table emitted out of order gets attributed
        # to the wrong section and its citations point at the wrong place.
        pages: list[tuple[int, list[dict], list[tuple[float, list[str]]]]] = []
        all_gaps: list[float] = []
        for page_no, page in enumerate(pdf.pages, start=1):
            found = page.find_tables()
            table_regions = [t.bbox for t in found]
            tables: list[tuple[float, list[str]]] = []
            for table_obj, table in zip(found, page.extract_tables() or []):
                rows = [
                    " | ".join(_flatten_cell(cell) for cell in row)
                    for row in table
                    if any(cell for cell in row)
                ]
                if rows:
                    tables.append((table_obj.bbox[1], rows))
            lines = [
                ln
                for ln in (page.extract_text_lines() or [])
                if not any(
                    y0 - 2 <= ln["top"] <= y1 + 2 and x0 - 2 <= ln["x0"] <= x1 + 2
                    for x0, y0, x1, y1 in table_regions
                )
            ]
            all_gaps += [
                b["top"] - a["top"] for a, b in pairwise(lines) if b["top"] > a["top"]
            ]
            pages.append((page_no, lines, tables))

        # Line pitch is document-wide, so the threshold is computed once.
        break_threshold = _paragraph_gap_threshold(all_gaps)

        for page_no, lines, tables in pages:
            blocks += _page_blocks(page_no, lines, tables, break_threshold)
    return _blocks_to_document(blocks, doc_key, path.name, "pdf", page_count=page_count)


def parse_document(path: Path, doc_key: str) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path, doc_key)
    if suffix == ".docx":
        return parse_docx(path, doc_key)
    if suffix in {".txt", ".md"}:
        return parse_txt(path, doc_key)
    raise ValueError(f"Unsupported SOW format: {suffix}")
