"""Citation normalization.

Regression: chunks were rendered to the model as "[KEY p1]", so it echoed the
page number back inside the key. Every citation then failed to match a chunk
and was dropped, leaving all tasks with no evidence while the pipeline
reported success.
"""

import pytest

from app.graph.persistence import normalize_citation
from app.ingestion.parser import parse_document
from app.llm.client import build_chunked_text
from tests.factories import CORPUS


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("SOW-001-S04-C02", "SOW-001-S04-C02"),
        ("SOW-001-S04-C02 p1", "SOW-001-S04-C02"),
        ("SOW-001-S04-C02 (page 7)", "SOW-001-S04-C02"),
        ("[SOW-001-S04-C02]", "SOW-001-S04-C02"),
        ("  SOW-001-S04-C02  ", "SOW-001-S04-C02"),
        ("SOW-R01-S12-C03 p12", "SOW-R01-S12-C03"),
    ],
)
def test_decorated_citations_resolve_to_the_bare_key(raw, expected):
    assert normalize_citation(raw) == expected


def test_unrecognizable_citation_is_returned_unchanged():
    # It must still fail the membership check rather than match something wrong.
    assert normalize_citation("section 4.2") == "section 4.2"


def test_rendered_chunks_keep_the_page_outside_the_citation_bracket():
    doc = parse_document(CORPUS / "sow_a_cairomart.pdf", "SOW-001")
    rendered = build_chunked_text(doc)
    keys = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()]
    for key in keys:
        assert f"[{key}]" in rendered, f"{key} is not citable as written"
    assert " p1]" not in rendered and "p1]" not in rendered
