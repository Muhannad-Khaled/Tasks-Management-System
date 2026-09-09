"""Putting the directory's people onto the roles a SOW asked for.

This is propagation, not inference. Somebody said once that Ahmed can work as a
Backend Engineer; the SOW says this project needs a Backend Engineer; the two
are joined. Nothing here reads a name and decides what that person is good at,
because nothing could — and a guess at it would be the same fabrication the
rest of the platform exists to keep out of a plan.

So the rule is deliberately timid. One candidate means one obvious answer and
it is taken. Two candidates is a real choice between real people, and the
system has no basis for making it, so the role is left open and said out loud.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import Person, PersonRole, ProjectRole, ProjectTask

logger = logging.getLogger(__name__)


def candidates_for(db: Session, role_title: str) -> list[Person]:
    """Everyone still here who has been marked as able to do this role."""
    return (
        db.query(Person)
        .join(PersonRole, PersonRole.person_id == Person.id)
        .filter(PersonRole.role_title == role_title, Person.active.is_(True))
        .order_by(Person.name)
        .all()
    )


def autostaff(db: Session, project_id: str) -> list[str]:
    """Fill every role that has exactly one person able to do it.

    A role somebody has already been put on is never touched: a human decision
    outranks a rule, and quietly replacing one would make the assignment
    untrustworthy the first time it happened.

    Returns what the PM needs to know — the roles left open, and why.
    """
    roles = db.query(ProjectRole).filter(ProjectRole.project_id == project_id).all()
    notes: list[str] = []
    staffed = 0

    for role in roles:
        if role.person_id:
            continue

        people = candidates_for(db, role.role_title)
        if len(people) == 1:
            role.person_id = people[0].id
            staffed += 1
        elif len(people) > 1:
            names = ", ".join(p.name for p in people)
            notes.append(
                f"{role.role_title}: {len(people)} people can do this "
                f"({names}) — pick one on the Structure tab"
            )
        else:
            notes.append(
                f"{role.role_title}: nobody in the directory does this yet — "
                "add them, or assign somebody on the Structure tab"
            )

    if staffed:
        logger.info("Staffed %d role(s) automatically on project %s", staffed, project_id)
    return notes


def restaff_cards(db: Session, project_id: str, role_title: str) -> None:
    """Mark the cards of a role whose holder just changed.

    The name is rendered into the card body, so every card owned by this role
    now says something the board does not.
    """
    for task in db.query(ProjectTask).filter(
        ProjectTask.project_id == project_id,
        ProjectTask.assignee_role == role_title,
        ProjectTask.external_ref != "",
    ):
        task.board_dirty = True
