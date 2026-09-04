"""Ingestion tests: parsing must be consistent across formats and produce
citable chunks, because every grounding claim later resolves through a chunk key.
"""

from pathlib import Path

import pytest

from app.ingestion.parser import ParsedDocument, looks_like_heading, parse_document
from app.ingestion.validation import ParsingStatus, validate_parsed_document

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"
FORMATS = ["txt", "docx", "pdf"]
DOCS = [("sow_a_cairomart", "SOW-001"), ("sow_b_quickbite", "SOW-002"), ("sow_c_glowbeauty", "SOW-003")]


def _parse(slug: str, ext: str, key: str) -> ParsedDocument:
    return parse_document(CORPUS / f"{slug}.{ext}", key)


@pytest.mark.parametrize("slug,key", DOCS)
@pytest.mark.parametrize("ext", FORMATS)
def test_parses_into_sections_and_chunks(slug, key, ext):
    doc = _parse(slug, ext, key)
    assert len(doc.sections) >= 5
    assert len(list(doc.iter_chunks())) >= 5
    assert doc.full_text.strip()


@pytest.mark.parametrize("slug,key", DOCS)
def test_section_count_is_consistent_across_formats(slug, key):
    counts = {ext: len(_parse(slug, ext, key).sections) for ext in FORMATS}
    assert len(set(counts.values())) == 1, f"formats disagree on section count: {counts}"


@pytest.mark.parametrize("slug,key", DOCS)
@pytest.mark.parametrize("ext", FORMATS)
def test_chunk_keys_are_unique_and_well_formed(slug, key, ext):
    doc = _parse(slug, ext, key)
    keys = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()]
    assert len(keys) == len(set(keys)), "chunk keys must be unique to be citable"
    assert all(k.startswith(f"{key}-S") and "-C" in k for k in keys)


@pytest.mark.parametrize("slug,key", DOCS)
def test_pdf_retains_page_numbers(slug, key):
    doc = _parse(slug, "pdf", key)
    assert all(c.page is not None for _, c in doc.iter_chunks())
    assert doc.page_count and doc.page_count >= 1


def test_pdf_paragraphs_are_split_not_merged_into_one_block():
    # Regression: PDFs have no blank lines, so a naive whitespace split collapsed
    # each page into a single unciteable chunk.
    doc = _parse("sow_b_quickbite", "pdf", "SOW-002")
    chunks = list(doc.iter_chunks())
    assert len(chunks) > len(doc.sections), "expected multiple chunks per section"
    assert max(len(c.text) for _, c in chunks) < 1400


def test_headings_are_detected_including_messy_ones():
    doc = _parse("sow_c_glowbeauty", "docx", "SOW-003")
    titles = [s.title for s in doc.sections]
    assert any("BACKGROUND" in t for t in titles), "ALL-CAPS heading"
    assert any(t.startswith("2.1") for t in titles), "sub-numbered heading"
    assert any(t.upper().startswith("FWD") for t in titles), "email-style heading"


def test_body_sentences_are_not_mistaken_for_headings():
    assert looks_like_heading("4. Commercial Requirements")
    assert looks_like_heading("2.1 Offers")
    assert looks_like_heading("FWD: RE: RE: training dates")
    assert not looks_like_heading("The Merchant will launch 20 loyalty offers at go-live.")
    assert not looks_like_heading("")


@pytest.mark.parametrize("slug,key", DOCS)
def test_clean_documents_pass_parsing_validation(slug, key):
    report = validate_parsed_document(_parse(slug, "pdf", key))
    assert report.status is not ParsingStatus.FAILED
    assert report.summary()


def test_duplicate_chunks_are_detected_in_every_format():
    # SOW C repeats a paragraph verbatim; the PM must see that, and a parser
    # that merges the repeats would hide it.
    for ext in FORMATS:
        report = validate_parsed_document(_parse("sow_c_glowbeauty", ext, "SOW-003"))
        dup = next(c for c in report.checks if c.name == "no_duplicate_chunks")
        assert not dup.passed, f"{ext}: deliberate duplicate not detected"


def test_tables_are_extracted_from_structured_formats():
    assert _parse("sow_a_cairomart", "docx", "SOW-001").table_count >= 3
    assert _parse("sow_a_cairomart", "pdf", "SOW-001").table_count >= 3


def test_empty_document_fails_validation(tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_text("nothing here", encoding="utf-8")
    report = validate_parsed_document(parse_document(empty, "SOW-999"))
    assert report.status is ParsingStatus.FAILED
    assert report.failures


def test_unsupported_format_is_rejected(tmp_path):
    bad = tmp_path / "sow.rtf"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        parse_document(bad, "SOW-999")
