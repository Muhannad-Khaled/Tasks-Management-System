"""The second kind of user story, and what it is allowed to say.

A story hung off a requirement was inherited by every task under it. On a live
NileBank run three technical tasks shared REQ-002, so the card for "Author and
Review Technical Integration Design" arrived carrying the acceptance criteria
and test cases of "Build and Test Core Accrual Engine" — three criteria and two
tests, all specific, all about different work, and nothing on the card saying
so.

The engineer story fixes that by hanging off the task. It also inverts a rule:
a client story written from the delivery side is a task in disguise and fails
validation, while an engineer story is addressed to the delivery role that owns
the task and must not.

What it may say is the constraint that matters. Technical notes and thresholds
come from the details the SOW yielded, so a plausible stack the document never
named cannot reach an engineer who would build to it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.graph.workflow import run_sow_pipeline
from app.ingestion.parser import parse_document
from app.main import app
from app.models import Project, ProjectTask, UserStory
from app.schemas.enums import ProjectStatus, StoryKind, Team
from app.schemas.roles import client_tokens, is_delivery_role
from app.taskmanager.cards import _board_task, artifacts_for_tasks
from app.validation.pipeline import validate_derived_artifacts
from tests.factories import StubLLM

CORPUS = Path(__file__).parent.parent / "data" / "sample_sows"


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def project(db) -> Project:
    sow = CORPUS / "sow_a_cairomart.pdf"
    doc = parse_document(sow, "SOW-001")
    citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]
    row = Project(name="Engineer stories", status=ProjectStatus.INGESTING)
    db.add(row)
    db.commit()
    run_sow_pipeline(db, row.id, str(sow), "SOW-001", client=StubLLM(citations=citations))
    return row


def _engineer_stories(db, project: Project) -> list[UserStory]:
    return (
        db.query(UserStory)
        .filter(
            UserStory.project_id == project.id,
            UserStory.kind == str(StoryKind.ENGINEER),
        )
        .all()
    )


# --- the split ------------------------------------------------------------


def test_every_technical_task_gets_its_own_story(db, project):
    technical = (
        db.query(ProjectTask)
        .filter(ProjectTask.project_id == project.id, ProjectTask.team == str(Team.TECHNICAL))
        .all()
    )
    stories = _engineer_stories(db, project)

    assert technical, "the fixture must produce technical tasks or this proves nothing"
    assert {s.task_id for s in stories} == {t.id for t in technical}


def test_sibling_tasks_no_longer_share_one_task_s_criteria(db, project):
    """The bug this whole change exists for.

    Two tasks under one requirement used to be handed the same criteria, so a
    design-document card carried the accrual engine's acceptance criteria.
    """
    artifacts = artifacts_for_tasks(db, project.id)
    technical = (
        db.query(ProjectTask)
        .filter(ProjectTask.project_id == project.id, ProjectTask.team == str(Team.TECHNICAL))
        .all()
    )
    siblings = [t for t in technical if t.requirement_id]
    by_requirement: dict[str, list[ProjectTask]] = {}
    for task in siblings:
        by_requirement.setdefault(task.requirement_id, []).append(task)
    shared = [group for group in by_requirement.values() if len(group) > 1]
    if not shared:
        pytest.skip("this fixture has no two technical tasks under one requirement")

    for group in shared:
        keys = [id(artifacts[t.id]) for t in group]
        assert len(set(keys)) == len(keys), "sibling tasks were handed the same criteria object"


def test_a_client_story_is_still_reached_through_its_requirement(db, project):
    """Commercial and operations tasks keep the behaviour they had."""
    artifacts = artifacts_for_tasks(db, project.id)
    commercial = (
        db.query(ProjectTask)
        .filter(ProjectTask.project_id == project.id, ProjectTask.team == str(Team.COMMERCIAL))
        .first()
    )

    assert commercial is not None
    assert artifacts.get(commercial.id, {}).get("criteria")


# --- the actor ------------------------------------------------------------


def test_the_actor_is_the_role_the_plan_already_assigned(db, project):
    """Never the model's choice, so it cannot name a role off the project."""
    for story in _engineer_stories(db, project):
        task = db.get(ProjectTask, story.task_id)
        assert story.actor == task.assignee_role


def test_a_delivery_side_actor_fails_a_client_story_and_not_an_engineer_one(db, project):
    """The inverted rule, checked in both directions at once."""
    stage = validate_derived_artifacts(db, project.id)
    engineer = _engineer_stories(db, project)

    assert engineer, "there must be engineer stories for this to mean anything"
    assert all(s.actor_is_delivery_side for s in engineer)
    assert "delivery team's point of view" not in stage.detail


def test_the_sentence_reads_as_english_whatever_the_model_returned(db, project):
    """Every story on the last live run read "I want establish".

    Models return the capability as a bare verb phrase. That went straight onto
    a Trello card, which is the one place the sentence is read by somebody who
    cannot see how it was assembled.
    """
    for story in db.query(UserStory).filter(UserStory.project_id == project.id):
        assert " I want to " in story.sentence, story.sentence

    already_infinitive = UserStory(
        actor="Backend Engineer", capability="to expose the accrual endpoint", benefit="x"
    )
    assert "I want to expose" in already_infinitive.sentence
    assert "to to" not in already_infinitive.sentence


# --- whose staff the actor is ---------------------------------------------


@pytest.mark.parametrize(
    ("actor", "merchant", "ours"),
    [
        # The live run's false positive: the bank is the client, and
        # _SYNONYMS maps "compliance" onto our Legal Advisor.
        ("bank compliance officer", "NileBank S.A.E.", False),
        ("bank operations manager", "NileBank S.A.E.", False),
        ("NileBank card product manager", "NileBank S.A.E.", False),
        # The same title is ours when the client is not a bank. This is what a
        # hardcoded "bank" token could not express.
        ("compliance officer", "QuickBite Restaurants", True),
        ("restaurant manager", "QuickBite Restaurants", False),
        # Ours regardless, and the check must keep catching them.
        ("QA engineer", "NileBank S.A.E.", True),
        ("support agent", "NileBank S.A.E.", True),
        ("legal advisor", "NileBank S.A.E.", True),
    ],
)
def test_a_role_named_after_the_client_is_theirs(actor, merchant, ours):
    assert is_delivery_role(actor, merchant) is ours


def test_a_compound_client_name_is_split(db):
    """"NileBank S.A.E." tokenises to {nilebank}, which never meets "bank".

    Without splitting it, the actor the live run produced went on failing.
    """
    tokens = client_tokens("NileBank S.A.E.")

    assert {"bank", "nile", "nilebank"} <= tokens
    # Initials would otherwise match any actor containing the letter.
    assert not any(len(t) <= 2 for t in tokens)


# --- what the notes may say -----------------------------------------------


def test_notes_citing_a_detail_the_sow_never_produced_are_dropped(db, project):
    """The stub writes 'Redis Streams with a Kafka fallback' against a detail
    that does not exist. Nothing the SOW did not name may survive."""
    stories = _engineer_stories(db, project)
    kept = [s for s in stories if s.technical_notes]

    assert all("Kafka" not in s.technical_notes for s in stories)
    assert all(s.technical_notes_chunk_keys for s in kept), "kept notes must cite something"


def test_a_threshold_with_no_evidence_is_dropped_rather_than_shown(db, project):
    """A number on a card is read as one the client agreed to.

    Storing it and flagging it would still put it in front of an engineer, so
    the threshold goes and the criterion keeps its wording.
    """
    stories = _engineer_stories(db, project)
    criteria = [c for s in stories for c in s.acceptance_criteria]

    assert criteria, "the fixture must produce criteria"
    assert all(c.text for c in criteria), "dropping a measure must not drop the criterion"
    assert all(c.source_chunk_keys for c in criteria if c.measure)
    assert not any(c.measure == "under 50ms" for c in criteria)


def test_the_pipeline_reports_what_it_dropped(db):
    """Silently discarding it would leave nobody able to tell it happened."""
    sow = CORPUS / "sow_a_cairomart.pdf"
    doc = parse_document(sow, "SOW-001")
    citations = [doc.chunk_key(s, c) for s, c in doc.iter_chunks()][:2]
    row = Project(name="Warnings", status=ProjectStatus.INGESTING)
    db.add(row)
    db.commit()

    state = run_sow_pipeline(db, row.id, str(sow), "SOW-001", client=StubLLM(citations=citations))

    warnings = state.get("warnings", [])
    assert any("did not produce" in w for w in warnings), warnings


def test_the_stage_says_how_much_of_a_criterion_it_actually_checked(db, project):
    """A criterion with no number is checked by nothing at all.

    Grounding runs before these exist and reads task descriptions, not
    criteria, so "the dynamic campaign engine evaluates AI segmentation
    models" ships to a board unverified — and a campaign engine is exactly
    what grounding rejected on a live run when the words sat in a task.

    That cannot be fixed here without another model call. What can be fixed is
    a pass reading as though the wording had been checked.
    """
    # The stub's grounding rejects the technical tasks, which the check below
    # this one is about. Clear it so what is under test is the clean pass.
    for story in _engineer_stories(db, project):
        db.get(ProjectTask, story.task_id).validation_status = "accept"
    db.commit()

    stage = validate_derived_artifacts(db, project.id)

    assert stage.passed, stage.detail
    assert "was not verified against the document" in stage.detail


def test_criteria_derived_from_a_rejected_task_are_reported(db, project):
    """The one thing the existing evidence can already settle."""
    story = next(s for s in _engineer_stories(db, project) if s.acceptance_criteria)
    db.get(ProjectTask, story.task_id).validation_status = "reject"
    db.commit()

    stage = validate_derived_artifacts(db, project.id)

    assert not stage.passed
    assert "the grounding engine rejected" in stage.detail


# --- what reaches the board -----------------------------------------------


def test_a_technical_card_carries_the_approach_and_its_notes(db, project):
    story = next(s for s in _engineer_stories(db, project) if s.technical_notes)
    task = db.get(ProjectTask, story.task_id)

    body = _board_task(db, task, artifacts_for_tasks(db, project.id)).rendered_description()

    assert "**Approach**" in body
    assert story.capability in body
    assert story.technical_notes in body


def test_a_technical_card_with_no_notes_says_the_sow_specified_none(db, project):
    """Silence here is indistinguishable from the detail living elsewhere.

    An engineer who reads nothing goes looking for the constraints; one who
    reads this knows the choice is theirs and has to be agreed.
    """
    story = next(s for s in _engineer_stories(db, project) if not s.technical_notes)
    task = db.get(ProjectTask, story.task_id)

    body = _board_task(db, task, artifacts_for_tasks(db, project.id)).rendered_description()

    assert "does not specify the interfaces" in body


def test_a_commercial_card_stays_silent_about_technical_notes(db, project):
    """The card was trimmed on purpose. A line that appears on every card is
    one nobody reads, which is how the warnings beside it stop working."""
    task = (
        db.query(ProjectTask)
        .filter(ProjectTask.project_id == project.id, ProjectTask.team == str(Team.COMMERCIAL))
        .first()
    )

    body = _board_task(db, task, artifacts_for_tasks(db, project.id)).rendered_description()

    assert "Technical notes" not in body


def test_a_measured_criterion_reaches_the_checklist_with_its_threshold(db, project):
    story = next(
        s for s in _engineer_stories(db, project) if any(c.measure for c in s.acceptance_criteria)
    )
    task = db.get(ProjectTask, story.task_id)

    card = _board_task(db, task, artifacts_for_tasks(db, project.id))
    items = dict(card.checklists())["Acceptance criteria"]

    assert any("under 400ms" in item for item in items)


# --- what reaches the PM --------------------------------------------------


def test_the_structure_endpoint_hangs_engineer_stories_off_their_task(client, db, project):
    data = client.get(f"/projects/{project.id}/structure").json()

    tasks = [
        task
        for team in data["teams"].values()
        for requirement in team["requirements"]
        for task in requirement["tasks"]
    ]
    carrying = [t for t in tasks if t["engineer_stories"]]

    assert carrying, "no task exposed an engineer story"
    assert all(t["team"] == str(Team.TECHNICAL) for t in carrying)
    assert data["totals"]["engineer_stories"] == len(_engineer_stories(db, project))


def test_a_threshold_travels_with_its_citation(client, project):
    """Shown apart, a number the client agreed to and one that arrived from
    nowhere look identical."""
    data = client.get(f"/projects/{project.id}/structure").json()

    criteria = [
        criterion
        for team in data["teams"].values()
        for requirement in team["requirements"]
        for task in requirement["tasks"]
        for story in task["engineer_stories"]
        for criterion in story["acceptance_criteria"]
    ]
    measured = [c for c in criteria if c["measure"]]

    assert measured, "the fixture must produce a measured criterion"
    assert all(c["source_chunk_keys"] for c in measured)
