"""Score a real extraction against the hand-written gold standards.

This is the M7 evaluation framework in miniature: enough to tell whether a
prompt or model change actually improved anything, rather than judging by eye.

    .venv/Scripts/python evals/score.py            # score what is in the DB
    .venv/Scripts/python evals/score.py --extract  # re-run extraction first

Metrics, per document:
  task_coverage        fraction of gold requirement areas that produced a task
  team_accuracy        tasks whose team matches the gold requirement's team
  citation_validity    fraction of cited chunk keys that resolve to real chunks
  evidence_coverage    fraction of tasks carrying at least one citation
  hallucinated_facts   gold-declared gaps asserted as explicit fact (must be 0)
  assumption_recall    gold-expected gap categories surfaced as assumptions
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.db import SessionLocal
from app.graph.workflow import run_sow_pipeline
from app.models import Assumption, Project, ProjectTask, SOWChunk, SOWDocument
from app.schemas.enums import ProjectStatus

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"
GOLD = CORPUS / "gold"

DOCS = [
    ("sow_a_cairomart", "SOW-EV-A"),
    ("sow_b_quickbite", "SOW-EV-B"),
    ("sow_c_glowbeauty", "SOW-EV-C"),
]

# Words that identify a gold requirement area in a generated task title.
# Matching on keywords rather than exact titles keeps the score meaningful when
# the model phrases a task differently but covers the same ground.
COVERAGE_KEYWORDS = {
    "contract": ["contract", "countersign", "sign"],
    "offers": ["offer"],
    "api_integration": ["api", "integration", "pos"],
    "points_logic": ["point", "accrual", "earn", "expiry"],
    "testing": ["test", "validation", "qa"],
    "configuration": ["configur", "setup", "provision"],
    "onboarding": ["onboard", "merchant account"],
    "training": ["train"],
    "go_live": ["go-live", "golive", "launch", "cutover"],
}


@dataclass
class DocScore:
    doc: str
    profile: str
    tasks: int
    covered: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    citation_validity: float = 0.0
    evidence_coverage: float = 0.0
    hallucinated: list[str] = field(default_factory=list)
    assumptions_found: list[str] = field(default_factory=list)
    assumptions_expected: list[str] = field(default_factory=list)
    provenance_mix: dict[str, int] = field(default_factory=dict)

    @property
    def task_coverage(self) -> float:
        total = len(self.covered) + len(self.missed)
        return len(self.covered) / total if total else 0.0

    @property
    def assumption_recall(self) -> float:
        if not self.assumptions_expected:
            return 1.0
        return len(self.assumptions_found) / len(self.assumptions_expected)


def _expected_areas(gold: dict) -> set[str]:
    """Which requirement areas this SOW should produce tasks for."""
    areas = set()
    for requirement in gold.get("requirements", []):
        title = requirement["title"].lower()
        for area, keywords in COVERAGE_KEYWORDS.items():
            if any(k in title for k in keywords):
                areas.add(area)
    return areas


def _gap_terms(gold: dict) -> dict[str, list[str]]:
    """Gold-declared gaps mapped to the numbers that must never be asserted."""
    known = gold.get("known_gaps", {})
    terms: dict[str, list[str]] = {}
    if "offer_count" in known:
        terms["offer_count"] = ["20 offers", "12 offers", "15 offers"]
    if "earn_rate" in known:
        terms["earn_rate"] = ["10 points", "5 points"]
    if "training_duration" in known:
        terms["training_duration"] = ["2 days", "3 days", "two days"]
    if "team_size" in known:
        terms["team_size"] = ["8 people", "5 technical"]
    return terms


def score_document(db, project: Project, gold: dict) -> DocScore:
    tasks = db.query(ProjectTask).filter(ProjectTask.project_id == project.id).all()
    assumptions = db.query(Assumption).filter(Assumption.project_id == project.id).all()
    score = DocScore(doc=gold["doc"], profile=gold["profile"], tasks=len(tasks))

    titles = " ".join(f"{t.title} {t.description}".lower() for t in tasks)
    for area in sorted(_expected_areas(gold)):
        keywords = COVERAGE_KEYWORDS[area]
        (score.covered if any(k in titles for k in keywords) else score.missed).append(area)

    cited = [k for t in tasks for k in t.source_chunk_keys.split(",") if k]
    if cited:
        real = sum(
            1
            for k in cited
            if db.query(SOWChunk).filter(SOWChunk.chunk_key == k).first() is not None
        )
        score.citation_validity = real / len(cited)
    if tasks:
        score.evidence_coverage = sum(1 for t in tasks if t.source_chunk_keys) / len(tasks)

    for task in tasks:
        score.provenance_mix[task.source_status] = score.provenance_mix.get(task.source_status, 0) + 1

    # A gap the SOW never filled must not appear as an explicit claim.
    explicit_text = " ".join(
        f"{t.title} {t.description}".lower() for t in tasks if t.source_status == "explicit"
    )
    for gap, terms in _gap_terms(gold).items():
        if any(term in explicit_text for term in terms):
            score.hallucinated.append(gap)

    expected = [c.lower() for c in gold.get("expected_assumption_categories", [])] or [
        c.lower() for c in gold.get("expected_assumption_or_question_categories", [])
    ]
    score.assumptions_expected = expected
    haystack = " ".join(f"{a.category} {a.value} {a.reason}".lower() for a in assumptions)
    for category in expected:
        # Categories are snake/upper case labels; match on their parts.
        if all(part in haystack for part in category.split("_")):
            score.assumptions_found.append(category)
    return score


def extract_all(db) -> dict[str, Project]:
    projects = {}
    for slug, doc_key in DOCS:
        project = Project(name=f"EVAL {slug}", status=ProjectStatus.INGESTING)
        db.add(project)
        db.commit()
        print(f"  extracting {slug} …", flush=True)
        try:
            run_sow_pipeline(db, project.id, str(CORPUS / f"{slug}.pdf"), doc_key)
        except Exception as exc:  # noqa: BLE001 - one bad document must not end the run
            db.rollback()
            db.delete(project)
            db.commit()
            print(f"  {slug} failed: {exc}")
            continue
        projects[slug] = project
    return projects


def find_existing(db) -> dict[str, Project]:
    """Locate previous eval runs by their SOW document key.

    Not by project name: extraction overwrites the name with the one it reads
    out of the SOW, so any name assigned before the run is gone afterwards.
    """
    projects = {}
    for slug, doc_key in DOCS:
        document = (
            db.query(SOWDocument)
            .filter(SOWDocument.doc_key == doc_key)
            .order_by(SOWDocument.uploaded_at.desc())
            .first()
        )
        if document:
            projects[slug] = db.get(Project, document.project_id)
    return projects


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extract", action="store_true", help="re-run extraction (uses quota)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        projects = extract_all(db) if args.extract else find_existing(db)
        if not projects:
            print("No EVAL projects found. Run with --extract first.")
            return

        scores = []
        for slug, _ in DOCS:
            if slug not in projects:
                continue
            gold = json.loads((GOLD / f"{slug}.json").read_text(encoding="utf-8"))
            scores.append(score_document(db, projects[slug], gold))

        print(f"\n{'document':22s} {'tasks':>5s} {'cover':>6s} {'cite':>6s} {'evid':>6s} {'asm':>6s}  halluc")
        print("-" * 74)
        for s in scores:
            print(
                f"{s.doc:22s} {s.tasks:5d} {s.task_coverage:6.0%} "
                f"{s.citation_validity:6.0%} {s.evidence_coverage:6.0%} "
                f"{s.assumption_recall:6.0%}  {','.join(s.hallucinated) or 'none'}"
            )

        print("\ndetail")
        for s in scores:
            print(f"\n  {s.doc} ({s.profile})")
            print(f"    provenance     : {s.provenance_mix}")
            if s.missed:
                print(f"    missed areas   : {', '.join(s.missed)}")
            if s.assumptions_expected:
                missing = set(s.assumptions_expected) - set(s.assumptions_found)
                print(f"    assumptions    : {len(s.assumptions_found)}/{len(s.assumptions_expected)}")
                if missing:
                    print(f"    gaps unflagged : {', '.join(sorted(missing))}")
            if s.hallucinated:
                print(f"    HALLUCINATED   : {', '.join(s.hallucinated)}")

        worst = min(s.citation_validity for s in scores)
        print(f"\ncitation validity (min across docs): {worst:.0%}")
        if any(s.hallucinated for s in scores):
            print("FAIL: a gap the SOW never filled was asserted as explicit fact.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
