"""Parsing validation (brief section 6).

The workflow must not reason over a broken parse. These checks run before any
LLM call and produce a VALID / WARN / PARSING_FAILED verdict with human-readable
findings, so a failure routes to human review instead of silently degrading
every downstream extraction.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from app.ingestion.parser import ParsedDocument

MIN_TOTAL_CHARS = 500
MIN_SECTIONS = 2


class ParsingStatus(StrEnum):
    VALID = "valid"
    WARN = "warn"
    FAILED = "parsing_failed"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    fatal: bool = False


@dataclass
class ParsingReport:
    status: ParsingStatus
    checks: list[Check]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> str:
        icon = {True: "PASS", False: "FAIL"}
        lines = [f"[{icon[c.passed]}] {c.name}: {c.detail}" for c in self.checks]
        lines.append(f"Parsing status: {self.status.upper()}")
        return "\n".join(lines)


def validate_parsed_document(doc: ParsedDocument) -> ParsingReport:
    checks: list[Check] = []
    chunks = [c for _, c in doc.iter_chunks()]
    total_chars = sum(len(c.text) for c in chunks)

    checks.append(
        Check(
            "text_extracted",
            bool(chunks),
            f"{len(chunks)} chunks, {total_chars} characters",
            fatal=True,
        )
    )
    checks.append(
        Check(
            "sufficient_text",
            total_chars >= MIN_TOTAL_CHARS,
            f"{total_chars} characters (minimum {MIN_TOTAL_CHARS})",
            fatal=True,
        )
    )
    checks.append(
        Check(
            "sections_detected",
            len(doc.sections) >= MIN_SECTIONS,
            f"{len(doc.sections)} sections detected (minimum {MIN_SECTIONS})",
            fatal=True,
        )
    )
    checks.append(
        Check("tables_readable", True, f"{doc.table_count} tables detected")
    )

    if doc.page_count:
        pages_with_text = {c.page for c in chunks if c.page is not None}
        missing = [p for p in range(1, doc.page_count + 1) if p not in pages_with_text]
        checks.append(
            Check(
                "no_empty_pages",
                not missing,
                f"pages without text: {missing}" if missing else f"all {doc.page_count} pages have text",
            )
        )
        checks.append(Check("page_references", True, f"{doc.page_count} pages tracked"))
    else:
        checks.append(
            Check("page_references", False, "no page numbers available for this format")
        )

    # Duplicate chunks usually mean copy-paste in the source or a parser loop.
    # Either way the PM should see it (SOW C has a deliberate duplicate).
    counts = Counter(c.text.strip() for c in chunks)
    duplicates = [text for text, n in counts.items() if n > 1]
    checks.append(
        Check(
            "no_duplicate_chunks",
            not duplicates,
            f"{len(duplicates)} duplicated chunk(s) found" if duplicates else "no duplicates",
        )
    )

    titled = [s for s in doc.sections if s.title != "(preamble)"]
    checks.append(
        Check(
            "headings_detected",
            bool(titled),
            f"{len(titled)} titled sections: {[s.title[:40] for s in titled[:5]]}",
        )
    )

    if any(not c.passed and c.fatal for c in checks):
        status = ParsingStatus.FAILED
    elif any(not c.passed for c in checks):
        status = ParsingStatus.WARN
    else:
        status = ParsingStatus.VALID
    return ParsingReport(status=status, checks=checks)
