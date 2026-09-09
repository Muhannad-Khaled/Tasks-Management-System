"""Derived artifacts: user stories, acceptance criteria and test cases.

The danger these tests guard is specific. A test case that says "$100 earns
1,000 points" looks like a fact. If the SOW never set the earn rate, a QA
engineer runs that case, watches it pass, and reports the system correct —
having validated a number this platform invented. So the flag matters more than
the wording, and it is decided in code, never taken from the model.
"""

import pytest

from app.graph.persistence import persist_artifacts, persist_document, persist_extraction
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.models import AcceptanceCriterion, Project, ProjectRequirement, ProjectTestCase, UserStory
from app.schemas.enums import ProjectStatus, SourceStatus
from app.validation.pipeline import validate_derived_artifacts
from tests.factories import CORPUS, StubLLM


@pytest.fixture
def extracted(db):
    """A project with requirements persisted, ready to derive from."""
    doc = parse_document(CORPUS / "sow_a_cairomart.pdf", "SOW-001")
    project = Project(name="Artifacts", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()
    _, chunks_by_key = persist_document(db, project, doc, "unused.pdf", "ok")
    citations = list(chunks_by_key)[:2]
    stub = StubLLM(citations=citations)
    persist_extraction(db, project, stub._extraction(), chunks_by_key)
    requirements = {
        r.requirement_key: r
        for r in db.query(ProjectRequirement)
        .filter(ProjectRequirement.project_id == project.id)
        .all()
    }
    return project, stub, requirements


def test_a_test_case_built_on_an_assumed_value_is_flagged(db, extracted):
    project, stub, requirements = extracted

    persist_artifacts(db, project, stub._artifacts(), requirements, {"earn_rate"})
    db.commit()

    case = db.query(ProjectTestCase).filter(ProjectTestCase.case_key == "TC-002").one()
    assert case.rests_on_assumption
    assert case.assumed_fields == "earn_rate"


def test_the_same_case_is_not_flagged_when_the_sow_states_the_value(db, extracted):
    # The flag must track what the SOW settled, not the shape of the case.
    project, stub, requirements = extracted

    persist_artifacts(db, project, stub._artifacts(), requirements, set())
    db.commit()

    case = db.query(ProjectTestCase).filter(ProjectTestCase.case_key == "TC-002").one()
    assert not case.rests_on_assumption
    assert case.assumed_fields == ""


def test_a_case_that_depends_on_nothing_assumed_is_left_alone(db, extracted):
    project, stub, requirements = extracted

    persist_artifacts(db, project, stub._artifacts(), requirements, {"earn_rate"})
    db.commit()

    case = db.query(ProjectTestCase).filter(ProjectTestCase.case_key == "TC-001").one()
    assert not case.rests_on_assumption


def test_a_field_the_model_invented_is_reported_and_ignored(db, extracted):
    # TC-003 declares "not_a_real_field". It must not silently become a
    # dependency, and it must not be silently discarded either.
    project, stub, requirements = extracted

    _, warnings = persist_artifacts(db, project, stub._artifacts(), requirements, {"earn_rate"})
    db.commit()

    case = db.query(ProjectTestCase).filter(ProjectTestCase.case_key == "TC-003").one()
    assert not case.rests_on_assumption
    assert any("not_a_real_field" in w for w in warnings)


def test_a_story_inherits_its_evidence_from_its_requirement(db, extracted):
    project, stub, requirements = extracted

    persist_artifacts(db, project, stub._artifacts(), requirements, set())
    db.commit()

    story = db.query(UserStory).filter(UserStory.story_key == "US-001").one()
    assert story.source_chunk_keys == requirements["REQ-001"].source_chunk_keys
    assert story.team == requirements["REQ-001"].team


def test_a_story_is_never_explicit_even_from_an_explicit_requirement(db, extracted):
    # A restatement cannot be stronger than what it restates, and it is not the
    # thing the SOW said.
    project, stub, requirements = extracted
    assert requirements["REQ-001"].source_status == str(SourceStatus.EXPLICIT)

    persist_artifacts(db, project, stub._artifacts(), requirements, set())
    db.commit()

    story = db.query(UserStory).filter(UserStory.story_key == "US-001").one()
    assert story.source_status == str(SourceStatus.INFERRED)


def test_a_story_citing_an_unknown_requirement_is_dropped_not_orphaned(db, extracted):
    project, stub, requirements = extracted
    artifacts = stub._artifacts()
    artifacts.stories[0].requirement_id = "REQ-404"

    stories, warnings = persist_artifacts(db, project, artifacts, requirements, set())
    db.commit()

    assert {s.story_key for s in stories} == {"US-002", "US-003"}
    assert any("REQ-404" in w for w in warnings)
    assert db.query(UserStory).filter(UserStory.requirement_id.is_(None)).count() == 0


def test_acceptance_criteria_hang_off_their_story(db, extracted):
    project, stub, requirements = extracted

    persist_artifacts(db, project, stub._artifacts(), requirements, set())
    db.commit()

    story = db.query(UserStory).filter(UserStory.story_key == "US-001").one()
    assert {c.criterion_key for c in story.acceptance_criteria} == {"AC-001", "AC-002"}


def test_the_story_reads_as_a_sentence(db, extracted):
    project, stub, requirements = extracted

    persist_artifacts(db, project, stub._artifacts(), requirements, set())
    db.commit()

    story = db.query(UserStory).filter(UserStory.story_key == "US-002").one()
    # The infinitive is supplied in code. Models return a bare verb phrase, so
    # this assertion used to encode "I want earn points" — the exact wording a
    # live run then printed onto every Trello card.
    assert story.sentence == (
        "As a customer, I want to earn points on a purchase so that I am rewarded for shopping"
    )


def test_re_deriving_replaces_the_previous_artifacts(db, extracted):
    project, stub, requirements = extracted

    persist_artifacts(db, project, stub._artifacts(), requirements, set())
    db.commit()
    persist_artifacts(db, project, stub._artifacts(), requirements, set())
    db.commit()

    assert db.query(UserStory).filter(UserStory.project_id == project.id).count() == 3
    assert db.query(ProjectTestCase).filter(ProjectTestCase.project_id == project.id).count() == 4
    # The criteria belong to the replaced stories and must go with them.
    assert db.query(AcceptanceCriterion).count() == 4


def test_the_validation_stage_passes_on_a_well_formed_set(db, extracted):
    project, stub, requirements = extracted
    persist_artifacts(db, project, stub._artifacts(), requirements, {"earn_rate"})
    db.commit()

    stage = validate_derived_artifacts(db, project.id)

    assert stage.passed
    assert "1 rest on an assumed value" in stage.detail


def test_the_validation_stage_catches_a_story_with_no_acceptance_criteria(db, extracted):
    project, stub, requirements = extracted
    artifacts = stub._artifacts()
    artifacts.stories[0].acceptance_criteria = []
    persist_artifacts(db, project, artifacts, requirements, set())
    db.commit()

    stage = validate_derived_artifacts(db, project.id)

    assert not stage.passed
    assert "no acceptance criteria" in stage.detail


def test_the_validation_stage_is_quiet_when_there_is_nothing_to_check(db, extracted):
    project, _, _ = extracted

    stage = validate_derived_artifacts(db, project.id)

    assert stage.passed
    assert "no derived artifacts" in stage.detail


def test_the_pipeline_produces_stories_and_cases_end_to_end(db):
    sow = CORPUS / "sow_a_cairomart.pdf"
    doc = parse_document(sow, "SOW-001")
    citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]
    project = Project(name="End to end", status=ProjectStatus.INGESTING)
    db.add(project)
    db.commit()

    state = run_sow_pipeline(
        db, project.id, str(sow), "SOW-001", client=StubLLM(citations=citations)
    )

    # Three client stories, one per requirement, plus one engineer story for
    # each of the two technical tasks.
    assert state["story_count"] == 5
    assert state["test_case_count"] == 6
    # The gap report leaves earn_rate assumed, so the accrual case must arrive
    # already carrying that warning.
    case = db.query(ProjectTestCase).filter(ProjectTestCase.case_key == "TC-002").one()
    assert case.rests_on_assumption


def test_deleting_a_project_takes_its_stories_and_cases(db, extracted):
    project, stub, requirements = extracted
    persist_artifacts(db, project, stub._artifacts(), requirements, set())
    db.commit()

    db.delete(project)
    db.commit()

    assert db.query(UserStory).count() == 0
    assert db.query(ProjectTestCase).count() == 0
    assert db.query(AcceptanceCriterion).count() == 0


def test_a_story_written_from_the_delivery_side_is_flagged(db, extracted):
    # "As a support agent, I want to deliver training" is a task wearing a user
    # story's grammar: it states work being done, not value received, so nobody
    # outside the project can say whether it is done.
    project, stub, requirements = extracted
    artifacts = stub._artifacts()
    artifacts.stories[0].actor = "support agent"
    artifacts.stories[0].capability = "deliver two days of on-ground training"

    _, warnings = persist_artifacts(db, project, artifacts, requirements, set())
    db.commit()

    story = db.query(UserStory).filter(UserStory.story_key == "US-001").one()
    assert story.actor_is_delivery_side
    assert any("delivery side" in w for w in warnings)


def test_a_story_about_the_merchants_own_staff_is_not_flagged(db, extracted):
    # "Merchant operations manager" shares two words with our own Operations
    # Manager but is the client's person, and must not be mistaken for ours.
    project, stub, requirements = extracted
    artifacts = stub._artifacts()
    artifacts.stories[0].actor = "merchant operations manager"

    _, warnings = persist_artifacts(db, project, artifacts, requirements, set())
    db.commit()

    story = db.query(UserStory).filter(UserStory.story_key == "US-001").one()
    assert not story.actor_is_delivery_side
    assert not any("delivery side" in w for w in warnings)


def test_the_validation_stage_fails_on_an_inward_facing_story(db, extracted):
    project, stub, requirements = extracted
    artifacts = stub._artifacts()
    artifacts.stories[1].actor = "QA engineer"
    persist_artifacts(db, project, artifacts, requirements, set())
    db.commit()

    stage = validate_derived_artifacts(db, project.id)

    assert not stage.passed
    assert "delivery team's point of view" in stage.detail


def test_the_stubs_own_actors_are_all_user_facing(db, extracted):
    # A guard on the fixture itself: if the stub drifts to delivery-side actors
    # the other tests would start passing for the wrong reason.
    project, stub, requirements = extracted

    persist_artifacts(db, project, stub._artifacts(), requirements, set())
    db.commit()

    stories = db.query(UserStory).filter(UserStory.project_id == project.id).all()
    assert not [s.story_key for s in stories if s.actor_is_delivery_side]
