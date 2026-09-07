"""Persist a parsed SOW and its extraction into PostgreSQL.

Chunk keys are the join between the document and everything generated from it,
so they are written first and every task records the keys it cites.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.ingestion.parser import ParsedDocument
from app.models import (
    AcceptanceCriterion,
    Assumption,
    ClaimRecord,
    ClarificationQuestion,
    Project,
    ProjectDetail,
    ProjectMilestone,
    ProjectRequirement,
    ProjectRole,
    ProjectTask,
    ProjectTestCase,
    SOWChunk,
    SOWDocument,
    SOWSection,
    TaskDependency,
    UserStory,
    milestone_tasks,
)
from app.schemas.details import team_for
from app.schemas.enums import SourceStatus, Team
from app.schemas.fields import FIELDS_BY_KEY
from app.schemas.roles import (
    TEAM_ROLES,
    RoleSpec,
    is_delivery_role,
    match_role,
    roles_for_team,
)
from app.schemas.sow import StructuredSOW

_CHUNK_KEY = re.compile(r"[A-Za-z0-9_.-]*SOW[A-Za-z0-9_.-]*-S\d+-C\d+", re.IGNORECASE)


def normalize_citation(raw: str) -> str:
    """Pull the canonical chunk key out of whatever the model emitted.

    Models decorate citations — a trailing page number, surrounding brackets,
    stray whitespace. Comparing those raw against the document's keys silently
    discards every citation and leaves tasks with no evidence, which is the one
    failure this system cannot afford to have happen quietly.
    """
    match = _CHUNK_KEY.search(raw.strip().strip("[]()"))
    return match.group(0) if match else raw.strip()


def persist_document(
    db: Session, project: Project, doc: ParsedDocument, stored_path: str, parsing_status: str
) -> tuple[SOWDocument, dict[str, SOWChunk]]:
    """Write the document tree; return it with a chunk_key -> chunk index."""
    sow_doc = SOWDocument(
        project_id=project.id,
        doc_key=doc.doc_key,
        filename=doc.filename,
        file_type=doc.file_type,
        stored_path=stored_path,
        parsing_status=parsing_status,
    )
    db.add(sow_doc)
    db.flush()

    chunks_by_key: dict[str, SOWChunk] = {}
    for section in doc.sections:
        db_section = SOWSection(
            sow_document_id=sow_doc.id,
            section_index=section.section_index,
            title=section.title,
            page_start=section.page_start,
            page_end=section.page_end,
        )
        db.add(db_section)
        db.flush()
        for chunk in section.chunks:
            key = doc.chunk_key(section, chunk)
            db_chunk = SOWChunk(
                section_id=db_section.id,
                chunk_index=chunk.chunk_index,
                chunk_key=key,
                text=chunk.text,
                page=chunk.page,
            )
            db.add(db_chunk)
            chunks_by_key[key] = db_chunk
    db.flush()
    return sow_doc, chunks_by_key


def _valid_citations(raw_keys: list[str], chunks_by_key: dict[str, SOWChunk]) -> list[str]:
    """Citations that point at text the document actually contains."""
    return [k for k in (normalize_citation(k) for k in raw_keys) if k in chunks_by_key]


def persist_requirements(
    db: Session,
    project: Project,
    extraction: StructuredSOW,
    chunks_by_key: dict[str, SOWChunk],
) -> dict[str, ProjectRequirement]:
    """Write requirements, keyed by the id the extraction gave them.

    Tasks reference these ids, so they are written before tasks and returned as
    a lookup rather than a list.
    """
    by_extracted_id: dict[str, ProjectRequirement] = {}
    for extracted in extraction.requirements:
        valid_keys = _valid_citations(extracted.source_chunk_keys, chunks_by_key)
        requirement = ProjectRequirement(
            project_id=project.id,
            requirement_key=extracted.requirement_id,
            team=str(extracted.team),
            title=extracted.title,
            description=extracted.description,
            source_status=str(extracted.source_status),
            source_sow_section_id=chunks_by_key[valid_keys[0]].section_id if valid_keys else None,
            source_chunk_keys=",".join(valid_keys),
        )
        db.add(requirement)
        db.flush()
        by_extracted_id[extracted.requirement_id] = requirement
    return by_extracted_id


def persist_roles(
    db: Session,
    project: Project,
    extraction: StructuredSOW,
    chunks_by_key: dict[str, SOWChunk],
) -> list[ProjectRole]:
    """The team, as the SOW describes it or as the platform assumes it.

    Titles the SOW states are stored verbatim. Canonicalising "Integration
    Architect" into "Technical Lead" would discard something the document
    actually said, which is the opposite of what this system is for.
    """
    db.query(ProjectRole).filter(ProjectRole.project_id == project.id).delete()

    if extraction.team_roster:
        rows = [
            ProjectRole(
                project_id=project.id,
                team=str(role.team),
                role_title=role.title,
                responsibility=role.responsibility,
                headcount=role.headcount,
                source_status=str(role.source_status),
                source_chunk_keys=",".join(
                    _valid_citations(role.source_chunk_keys, chunks_by_key)
                ),
            )
            for role in extraction.team_roster
        ]
    else:
        # No roster in the SOW is a gap like any other: fill it with the
        # default, and mark every row ASSUMED so nobody reads it as fact.
        rows = [
            ProjectRole(
                project_id=project.id,
                team=str(spec.team),
                role_title=spec.title,
                responsibility=spec.responsibility,
                headcount=1,
                source_status=str(SourceStatus.ASSUMED),
            )
            for spec in TEAM_ROLES
        ]
    db.add_all(rows)
    db.flush()
    return rows


def _project_role_specs(roles: list[ProjectRole], team: str) -> list[RoleSpec]:
    """Roles a task on this team may be assigned to: the defaults plus any the
    SOW named itself."""
    specs = list(roles_for_team(team))
    known = {s.title.casefold() for s in specs}
    for row in roles:
        if row.team == team and row.role_title.casefold() not in known:
            specs.append(
                RoleSpec(title=row.role_title, team=Team(team), responsibility=row.responsibility)
            )
    return specs


def persist_details(
    db: Session,
    project: Project,
    extraction: StructuredSOW,
    chunks_by_key: dict[str, SOWChunk],
) -> list[ProjectDetail]:
    """Write everything the SOW enumerates.

    The team comes from the category rather than from the model, so an API
    cannot end up owned by the commercial team because of which section of the
    document happened to mention it.
    """
    db.query(ProjectDetail).filter(ProjectDetail.project_id == project.id).delete()
    rows = [
        ProjectDetail(
            project_id=project.id,
            category=str(detail.category),
            team=str(team_for(detail.category)),
            name=detail.name,
            description=detail.description,
            source_status=str(detail.source_status),
            source_chunk_keys=",".join(_valid_citations(detail.source_chunk_keys, chunks_by_key)),
        )
        for detail in extraction.details
    ]
    db.add_all(rows)
    db.flush()
    return rows


def _close_the_roster(db: Session, project: Project, tasks) -> list[str]:
    """Add any role a task was assigned to that the roster does not list.

    A SOW that mentions one role in passing is not describing a one-person
    team, but taking its word for the whole roster is what happens if nothing
    reconciles the two. Then the roster shows an Account Manager while five
    tasks sit with a Backend Engineer the project supposedly does not have.

    The roles added here are marked ASSUMED, because that is exactly what they
    are: the SOW never named them.
    """
    existing = {
        (r.team, r.role_title.casefold())
        for r in db.query(ProjectRole).filter(ProjectRole.project_id == project.id)
    }
    added: list[str] = []
    for task in tasks:
        if not task.assignee_role:
            continue
        key = (task.team, task.assignee_role.casefold())
        if key in existing:
            continue
        spec = next(
            (r for r in roles_for_team(task.team) if r.title == task.assignee_role), None
        )
        db.add(
            ProjectRole(
                project_id=project.id,
                team=task.team,
                role_title=task.assignee_role,
                responsibility=spec.responsibility if spec else "",
                source_status=str(SourceStatus.ASSUMED),
            )
        )
        existing.add(key)
        added.append(f"{task.team}/{task.assignee_role}")
    if added:
        db.flush()
        return [
            "roster did not list role(s) tasks were assigned to; added as assumed: "
            + ", ".join(sorted(added))
        ]
    return []


def _persist_milestones(
    db: Session,
    project: Project,
    extraction: StructuredSOW,
    chunks_by_key: dict[str, SOWChunk],
) -> dict[str, ProjectMilestone]:
    """Write the SOW's dated checkpoints, keyed by name for tasks to attach to.

    Written before tasks, because these dates are the windows the estimates
    have to answer to and the model produces its output in field order. While
    milestones came last and had to name task ids that did not exist yet, they
    were simply dropped from the response.
    """
    # A bulk delete skips the association table, so the link rows outlive the
    # milestones they point at and the foreign key blocks the delete. Same
    # shape as the acceptance-criteria bug: delete-orphan is an ORM cascade and
    # a bulk query never runs it.
    stale = [
        row.id
        for row in db.query(ProjectMilestone.id).filter(
            ProjectMilestone.project_id == project.id
        )
    ]
    if stale:
        db.execute(milestone_tasks.delete().where(milestone_tasks.c.milestone_id.in_(stale)))
    db.query(ProjectMilestone).filter(ProjectMilestone.project_id == project.id).delete()
    by_name: dict[str, ProjectMilestone] = {}
    for extracted in extraction.milestones:
        milestone = ProjectMilestone(
            project_id=project.id,
            name=extracted.name,
            target_date=extracted.target_date,
            source_status=str(extracted.source_status),
            source_chunk_keys=",".join(
                _valid_citations(extracted.source_chunk_keys, chunks_by_key)
            ),
        )
        db.add(milestone)
        by_name[extracted.name.casefold()] = milestone
    db.flush()
    return by_name


def _milestone_warnings(milestones: dict[str, ProjectMilestone]) -> list[str]:
    """A milestone nothing was attached to cannot be checked against anything."""
    return [
        f"milestone {m.name!r} has no task attached, so its date cannot be "
        "compared with the schedule"
        for m in milestones.values()
        if not m.tasks
    ]


def persist_extraction(
    db: Session,
    project: Project,
    extraction: StructuredSOW,
    chunks_by_key: dict[str, SOWChunk],
) -> tuple[list[ProjectTask], list[str]]:
    """Write the roster, requirements and tasks, resolving citations and links.

    Citations the model invented (keys that are not in the document) are dropped
    rather than stored, so a task can never point at evidence that does not exist.
    Returns the tasks and any warnings about corrections made along the way.
    """
    warnings: list[str] = []
    roles = persist_roles(db, project, extraction, chunks_by_key)
    persist_details(db, project, extraction, chunks_by_key)
    requirements = persist_requirements(db, project, extraction, chunks_by_key)
    milestones = _persist_milestones(db, project, extraction, chunks_by_key)
    specs_by_team = {
        team: _project_role_specs(roles, team) for team in {str(t.team) for t in extraction.tasks}
    }

    tasks_by_extracted_id: dict[str, ProjectTask] = {}
    for extracted in extraction.tasks:
        team = str(extracted.team)
        valid_keys = _valid_citations(extracted.source_chunk_keys, chunks_by_key)
        section_id = chunks_by_key[valid_keys[0]].section_id if valid_keys else None

        # An unrecognised role is corrected to the team lead rather than stored
        # verbatim: a card assigned to a role nobody holds names nobody at all.
        role, warning = match_role(team, extracted.assignee_role, specs_by_team.get(team))
        if warning:
            warnings.append(f"{extracted.title!r}: {warning}")

        requirement = requirements.get(extracted.requirement_id or "")
        if extracted.requirement_id and requirement is None:
            warnings.append(
                f"{extracted.title!r}: cites unknown requirement "
                f"{extracted.requirement_id!r}, left unlinked"
            )

        task = ProjectTask(
            project_id=project.id,
            title=extracted.title,
            description=extracted.description,
            team=team,
            assignee_role=role.title,
            requirement_id=requirement.id if requirement is not None else None,
            priority=str(extracted.priority),
            estimated_hours=extracted.estimated_hours,
            estimate_source="estimated" if extracted.estimated_hours else "assumed",
            source_status=str(extracted.source_status),
            source_sow_section_id=section_id,
            source_chunk_keys=",".join(valid_keys),
        )
        # The task names its milestone rather than the other way round: the
        # milestone was written before any task id existed.
        if extracted.milestone:
            milestone = milestones.get(extracted.milestone.casefold())
            if milestone is None:
                warnings.append(
                    f"{extracted.title!r} names milestone {extracted.milestone!r}, "
                    "which the SOW does not list"
                )
            else:
                milestone.tasks.append(task)

        db.add(task)
        db.flush()
        tasks_by_extracted_id[extracted.task_id] = task

    warnings.extend(_close_the_roster(db, project, tasks_by_extracted_id.values()))
    warnings.extend(_milestone_warnings(milestones))

    for extracted in extraction.tasks:
        task = tasks_by_extracted_id[extracted.task_id]
        seen: set[str] = set()
        for link in extracted.depends_on:
            upstream = tasks_by_extracted_id.get(link.depends_on_id)
            # Unknown ids are dropped and self-dependencies would deadlock the graph.
            if upstream is None or upstream.id == task.id or upstream.id in seen:
                continue
            seen.add(upstream.id)
            status = str(link.source_status)
            valid_keys = _valid_citations(link.source_chunk_keys, chunks_by_key)

            # An arrow called EXPLICIT is a claim that the SOW ordered these two
            # itself, and it is the one status a PM would not think to question.
            # Without a citation there is nothing behind it, so it is demoted
            # rather than trusted.
            if status == str(SourceStatus.EXPLICIT) and not valid_keys:
                warnings.append(
                    f"{task.title!r} waits on {upstream.title!r}: called explicit "
                    "with no citation the document contains, recorded as inferred"
                )
                status = str(SourceStatus.INFERRED)

            task.blockers.append(
                TaskDependency(
                    depends_on_id=upstream.id,
                    source_status=status,
                    source_chunk_keys=",".join(valid_keys),
                    rationale=link.rationale.strip(),
                )
            )

    db.commit()
    return list(tasks_by_extracted_id.values()), warnings


def persist_assumptions(db: Session, project: Project, assumptions: list[dict]) -> list[Assumption]:
    """Write the assumption engine's findings.

    These come from the explicit field audit rather than from whatever the
    extraction pass chose to mention, so they replace any already recorded for
    the project instead of accumulating alongside them.
    """
    db.query(Assumption).filter(Assumption.project_id == project.id).delete()
    rows = [
        Assumption(
            project_id=project.id,
            assumption_key=a["assumption_key"],
            category=a["category"],
            value=a["value"],
            reason=a["reason"],
            confidence=a["confidence"],
        )
        for a in assumptions
    ]
    db.add_all(rows)
    db.flush()
    return rows


def persist_artifacts(
    db: Session,
    project: Project,
    artifacts,
    requirements: dict[str, ProjectRequirement],
    assumed_fields: set[str],
) -> tuple[list[UserStory], list[str]]:
    """Write stories, criteria and cases, inheriting provenance downwards.

    Two things are decided here rather than by the model:

    Provenance. A story restates a requirement, so it is INFERRED at best and
    carries the requirement's citations. Asking the model to cite for it would
    produce evidence for the requirement dressed up as evidence for the story.

    Whether a test case stands on an assumption. The model says which planning
    fields a case used; which of those the SOW never settled is read from the
    gap audit. A case built on an invented number must be marked, or a QA
    engineer runs it, sees it pass, and reports the system correct.
    """
    warnings: list[str] = []
    # A bulk delete does not run the ORM's delete-orphan cascade, so the
    # criteria of the stories being replaced would survive them and the foreign
    # key would block the delete. Clear children first, deepest last-referenced
    # first.
    story_ids = [
        row.id for row in db.query(UserStory.id).filter(UserStory.project_id == project.id)
    ]
    if story_ids:
        db.query(AcceptanceCriterion).filter(
            AcceptanceCriterion.user_story_id.in_(story_ids)
        ).delete(synchronize_session=False)
    db.query(ProjectTestCase).filter(ProjectTestCase.project_id == project.id).delete(
        synchronize_session=False
    )
    db.query(UserStory).filter(UserStory.project_id == project.id).delete(
        synchronize_session=False
    )

    stories: list[UserStory] = []
    for draft in artifacts.stories:
        requirement = requirements.get(draft.requirement_id)
        if requirement is None:
            warnings.append(
                f"story {draft.story_key} cites unknown requirement "
                f"{draft.requirement_id!r}, dropped"
            )
            continue

        story = UserStory(
            project_id=project.id,
            requirement_id=requirement.id,
            story_key=draft.story_key,
            team=requirement.team,
            actor=draft.actor,
            capability=draft.capability,
            benefit=draft.benefit,
            # A restatement is never stronger than what it restates, and never
            # explicit even when the requirement is.
            source_status=(
                str(SourceStatus.ASSUMED)
                if requirement.source_status == str(SourceStatus.ASSUMED)
                else str(SourceStatus.INFERRED)
            ),
            source_chunk_keys=requirement.source_chunk_keys,
            actor_is_delivery_side=is_delivery_role(draft.actor),
        )
        if story.actor_is_delivery_side:
            warnings.append(
                f"story {draft.story_key} is written from the delivery side "
                f"({draft.actor!r}), so it states work rather than value"
            )
        db.add(story)
        db.flush()

        for criterion in draft.acceptance_criteria:
            db.add(
                AcceptanceCriterion(
                    user_story_id=story.id,
                    criterion_key=criterion.criterion_key,
                    text=criterion.text,
                )
            )

        for case in draft.test_cases:
            declared = [f for f in case.depends_on_fields if f in FIELDS_BY_KEY]
            unknown = [f for f in case.depends_on_fields if f not in FIELDS_BY_KEY]
            if unknown:
                warnings.append(f"test case {case.case_key} names unknown field(s) {unknown}")
            resting_on = sorted(set(declared) & assumed_fields)
            db.add(
                ProjectTestCase(
                    project_id=project.id,
                    user_story_id=story.id,
                    case_key=case.case_key,
                    title=case.title,
                    preconditions=case.preconditions,
                    action=case.action,
                    expected_result=case.expected_result,
                    kind=str(case.kind),
                    rests_on_assumption=bool(resting_on),
                    assumed_fields=",".join(resting_on),
                )
            )
        stories.append(story)

    db.flush()
    return stories, warnings


def persist_questions(db: Session, project: Project, questions: list[dict]) -> list:
    """Write the open questions, replacing any from a previous run.

    Like assumptions, these are derived wholly from the latest gap audit, so
    keeping older rows alongside them would leave the PM a list of questions
    the current plan no longer depends on.
    """
    db.query(ClarificationQuestion).filter(
        ClarificationQuestion.project_id == project.id
    ).delete()
    rows = [ClarificationQuestion(project_id=project.id, **q) for q in questions]
    db.add_all(rows)
    db.flush()
    return rows


def persist_grounding(db: Session, project: Project, groundings: list) -> None:
    """Write per-claim verdicts and roll the score up onto each task.

    Only the claims of the tasks being persisted are replaced. Clearing the
    whole project would mean regenerating one task silently destroyed the
    evidence behind every other task's score, leaving scores on screen with
    nothing to justify them.
    """
    task_ids = [g.task_id for g in groundings]
    if task_ids:
        db.query(ClaimRecord).filter(
            ClaimRecord.project_id == project.id, ClaimRecord.task_id.in_(task_ids)
        ).delete(synchronize_session=False)
    for grounding in groundings:
        by_id = {j.claim_id: j for j in grounding.judgements}
        for claim in grounding.claims:
            judgement = by_id.get(claim.claim_id)
            db.add(
                ClaimRecord(
                    project_id=project.id,
                    task_id=grounding.task_id,
                    claim_key=claim.claim_id,
                    text=claim.text,
                    is_quantitative=claim.is_quantitative,
                    verdict=str(judgement.verdict) if judgement else "unsupported",
                    reasoning=judgement.reasoning if judgement else "",
                    supporting_chunk_keys=(
                        ",".join(judgement.supporting_chunk_keys) if judgement else ""
                    ),
                )
            )
        task = db.get(ProjectTask, grounding.task_id)
        if task is not None:
            task.grounding_score = grounding.score
            task.validation_status = grounding.status
    db.commit()


def invented_citations(extraction: StructuredSOW, chunks_by_key: dict[str, SOWChunk]) -> list[str]:
    """Citation keys the model produced that do not exist in the document."""
    cited = {normalize_citation(k) for t in extraction.tasks for k in t.source_chunk_keys}
    cited |= {normalize_citation(k) for r in extraction.requirements for k in r.source_chunk_keys}
    return sorted(cited - set(chunks_by_key))
