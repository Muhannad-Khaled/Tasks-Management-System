"""Integrity checks for the synthetic SOW corpus.

These fixtures are the ground truth for the whole evaluation framework (M7),
so a silent change to them would invalidate every accuracy number. This test
guards the properties the later pipeline stages rely on.
"""

import json
from pathlib import Path

import pdfplumber
import pytest
from docx import Document

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"
SLUGS = ["sow_a_cairomart", "sow_b_quickbite", "sow_c_glowbeauty"]


@pytest.mark.parametrize("slug", SLUGS)
def test_all_three_formats_exist_and_have_text(slug):
    txt = (CORPUS / f"{slug}.txt").read_text(encoding="utf-8")
    assert len(txt) > 1000

    paragraphs = [p.text for p in Document(str(CORPUS / f"{slug}.docx")).paragraphs if p.text.strip()]
    assert len(paragraphs) > 10

    with pdfplumber.open(CORPUS / f"{slug}.pdf") as pdf:
        pdf_text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    assert len(pdf_text) > 1000


@pytest.mark.parametrize("slug", SLUGS)
def test_gold_standard_is_valid_json_with_required_keys(slug):
    gold = json.loads((CORPUS / "gold" / f"{slug}.json").read_text(encoding="utf-8"))
    assert gold["doc"] == slug
    assert gold["profile"] in {"clean_complete", "gappy", "messy_adversarial"}
    assert gold["project_info"]["merchant_name"]
    assert gold["requirements"]


def test_sow_a_covers_all_three_teams():
    gold = json.loads((CORPUS / "gold" / "sow_a_cairomart.json").read_text(encoding="utf-8"))
    teams = {r["team"] for r in gold["requirements"]}
    assert teams == {"commercial", "technical", "operations"}
    assert gold["expected_assumptions"] == [], "clean SOW should need no assumptions"


def test_sow_b_declares_the_gaps_that_drive_the_assumption_engine():
    gold = json.loads((CORPUS / "gold" / "sow_b_quickbite.json").read_text(encoding="utf-8"))
    assert "OFFER_COUNT" in gold["expected_assumption_categories"]
    assert "EARN_RATE" in gold["expected_assumption_categories"]
    text = (CORPUS / "sow_b_quickbite.txt").read_text(encoding="utf-8")
    # The gaps are only meaningful if the document genuinely omits the numbers.
    assert "10 points" not in text
    assert "20 offers" not in text


def test_sow_c_retains_its_adversarial_features():
    text = (CORPUS / "sow_c_glowbeauty.txt").read_text(encoding="utf-8")
    assert text.count("Offer list to be provided by GlowBeauty marketing") == 2, "duplicate chunk"
    assert "3." not in text.split("2.1 Offers")[1].split("4. Technical stuff")[0], "numbering gap"
    assert "15 if the marketing team" in text, "conditional offer-count trap"
    assert "Probably API keys" in text, "TBD value that must not become an EXPLICIT fact"
