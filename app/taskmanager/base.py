"""Task manager abstraction (brief sections 24 and 27).

The core application talks only to this interface so Trello and Plane can be
evaluated against each other without touching the pipeline.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date

from app.taskmanager.labels import role_label


@dataclass
class BoardCase:
    """A test the board should show, reduced to what fits a tickable line."""

    title: str
    expected_result: str = ""
    rests_on_assumption: bool = False
    assumed_fields: list[str] = field(default_factory=list)

    def as_item(self) -> str:
        """One checklist line: which test, and what makes it pass.

        The Given/When steps stay in the platform. Ten tests' worth of steps
        would run to thousands of characters on a card nobody could tick
        through, whereas the pass condition is what a tick actually asserts.
        """
        line = self.title
        if self.expected_result:
            line = f"{line} — expect: {self.expected_result}"
        if self.rests_on_assumption:
            fields = ", ".join(self.assumed_fields)
            line = f"⚠️ {line} [assumes {fields}]" if fields else f"⚠️ {line}"
        return line


@dataclass
class BoardBlocker:
    """A task this one waits on, and whether the SOW said so.

    The distinction belongs on the card and not only in the platform: the
    person reading it is the one who would have to argue the date, and an
    ordering somebody inferred is the kind that can be argued.
    """

    title: str
    source_status: str = ""

    def render(self) -> str:
        note = {
            "inferred": " (inferred, not stated)",
            "assumed": " (assumed — the SOW does not order these)",
            "": " (source not recorded)",
        }.get(self.source_status, "")
        return f"{self.title}{note}"


@dataclass
class BoardTask:
    """Platform-neutral view of a task being pushed to a task manager."""

    task_id: str
    title: str
    description: str
    team: str
    priority: str
    status: str
    due_date: date | None = None
    assignee: str = ""
    assignee_role: str = ""
    # The person filling that role, when somebody has said who. Kept apart from
    # the role because the role came from the SOW and the name came from a
    # human, and a card should not blur the two.
    assignee_name: str = ""
    # Their Trello account, when the directory knows it. Empty means the card
    # still says who owns the work in its body but nobody is assigned on the
    # board — which is the normal case for anyone without a Trello login.
    assignee_member_id: str = ""
    source_status: str = "explicit"
    validation_status: str = "pending"
    source_section: str = ""
    source_chunk_keys: list[str] = field(default_factory=list)
    grounding_score: float | None = None
    blocked_by: list[BoardBlocker] = field(default_factory=list)
    # The criteria themselves, not a count. A card that says "2 criteria,
    # look them up elsewhere" leaves the person doing the work unable to
    # tell when they are done without opening another system.
    acceptance_criteria: list[str] = field(default_factory=list)
    # Planning fields the task's tests rely on that the SOW never settled.
    assumed_test_fields: list[str] = field(default_factory=list)
    test_cases: list[BoardCase] = field(default_factory=list)
    # How the assigned engineer is meant to approach this task, and the
    # interfaces and constraints it has to respect. Both come from the
    # project's own extracted details, so an empty `technical_notes` on a
    # technical card is a fact about the SOW rather than a gap in the output.
    story_sentence: str = ""
    technical_notes: str = ""
    expects_technical_notes: bool = False

    def labels(self) -> list[str]:
        """Board labels: where the task came from, and whether it held up.

        Provenance and grounding are separate facts and get separate labels.
        Conflating them let a task that FAILED grounding carry a "GROUNDED"
        label — because its source was explicit — telling the team the SOW
        fully supported work the system had just rejected.
        """
        labels = [f"TEAM-{self.team.upper()}", f"PRIORITY-{self.priority.upper()}"]
        if self.assignee_role:
            # A role label is the closest thing to "my cards" on a board with
            # no people on it, and filtering is the whole reason to assign.
            labels.append(role_label(self.assignee_role))
        labels.append(
            {"explicit": "FROM-SOW", "inferred": "INFERRED", "assumed": "ASSUMED"}.get(
                self.source_status, "SOURCE-UNKNOWN"
            )
        )
        labels.append(
            {
                "accept": "GROUNDED",
                "review": "REVIEW-REQUIRED",
                "reject": "GROUNDING-FAILED",
            }.get(self.validation_status, "NOT-CHECKED")
        )
        return labels

    def checklists(self) -> list[tuple[str, list[str]]]:
        """Named lists of items the board should render as tickable.

        Acceptance criteria are the clearest case: each is one observable
        condition, which is exactly what a checklist item is. Rendering them
        into prose loses the ticking, and rendering them as a count loses them
        entirely.
        """
        lists: list[tuple[str, list[str]]] = []
        if self.acceptance_criteria:
            lists.append(("Acceptance criteria", list(self.acceptance_criteria)))
        if self.test_cases:
            lists.append(("Test cases", [c.as_item() for c in self.test_cases]))
        return lists

    def rendered_description(self) -> str:
        """The card body.

        Everything here is aimed at whoever picks the card up, which rules out
        most of what the platform knows. Chunk keys identify nothing outside
        this system; "source status: EXPLICIT" is our vocabulary, not theirs;
        and a grounding score of 100% tells them to do nothing differently.

        So the block is silent when the task is in good order and speaks up
        when it is not. A reader who sees a warning here can trust that it
        needed saying, which is the only way warnings keep working.
        """
        parts = [self.description.strip() or "(no description)"]

        # Between the description and the metadata: this is what the person
        # holding the card actually has to do, so it reads before the
        # bookkeeping and after the summary.
        if self.story_sentence:
            parts.append(f"**Approach**\n\n{self.story_sentence}")
        if self.technical_notes:
            parts.append(f"**Technical notes**\n\n{self.technical_notes}")
        elif self.expects_technical_notes:
            # Named rather than left blank. An engineer who sees nothing here
            # assumes the constraints are recorded somewhere else and goes
            # looking; one who reads this knows there is nothing to find and
            # that whatever they choose is their own decision to defend.
            parts.append(
                "**Technical notes**\n\nThe SOW does not specify the interfaces, "
                "protocols or environments for this work. Nothing has been "
                "assumed on your behalf — agree them before building."
            )

        facts = []
        if self.assignee_role:
            # Both, when both are known: the name says who to ask, the role says
            # why it is theirs. Only the role is traceable to the SOW.
            owner = (
                f"{self.assignee_name} ({self.assignee_role})"
                if self.assignee_name
                else self.assignee_role
            )
            facts.append(f"**Owner:** {owner}")
        if self.blocked_by:
            facts.append("**Blocked by:** " + "; ".join(b.render() for b in self.blocked_by))
        if self.source_section:
            facts.append(f"**From SOW:** {self.source_section}")

        warnings = []
        if self.source_status == "assumed":
            warnings.append(
                "⚠️ This task is an assumption. The SOW does not ask for it — "
                "confirm before starting."
            )
        if self.validation_status in {"review", "reject"} and self.grounding_score is not None:
            warnings.append(
                f"⚠️ Only {self.grounding_score:.0%} of this task's claims are supported "
                "by the SOW. Check the details before relying on them."
            )
        if self.assumed_test_fields:
            warnings.append(
                "⚠️ Test cases for this task rely on values the SOW never stated "
                f"({', '.join(self.assumed_test_fields)}). Confirm them before "
                "treating a passing test as acceptance."
            )

        body = warnings + facts
        if body:
            parts.append("\n\n".join(warnings) + ("\n\n" if warnings and facts else "") + "\n".join(facts))
        parts.append("_Managed by the SOW platform. Full traceability lives there._")
        return "\n\n---\n\n".join(parts)


@dataclass(frozen=True)
class CardSnapshot:
    """A card as the board holds it right now.

    Read back rather than assumed. Everything the platform can tell about what
    a person did to a card by hand — moved it, retitled it, rewrote it — comes
    from comparing this against what was last written.
    """

    location: str
    title: str
    description: str
    # Archived, not gone. Kept apart from a card that is missing entirely
    # because an archived card can be put back exactly as it was.
    archived: bool = False
    # Who Trello has on the card. Deliberately outside the fingerprint below:
    # a reassignment is not an edit, and folding it in would report that the
    # card's text changed when only its owner did.
    members: tuple[str, ...] = ()

    def fingerprint(self) -> str:
        return content_fingerprint(self.title, self.description)


def content_fingerprint(title: str, description: str) -> str:
    """A short digest of what a card says.

    Stored at push time and compared later. Comparing the card against what the
    plan would render *now* cannot tell a person's edit from a plan that has
    legitimately moved on since — this can.
    """
    digest = hashlib.sha256(f"{title}\x00{description}".encode())
    return digest.hexdigest()[:32]


class TaskManagerInterface(ABC):
    """Implemented by TrelloAdapter and (in M7) PlaneAdapter.

    There is deliberately no push_project() that creates a board and fills it in
    one go. The PM pushes one task at a time, so the board has to outlive any
    single push; a method that always created one made every push after the
    first land on a board of its own.
    """

    name: str

    @abstractmethod
    def create_board(self, project_name: str) -> tuple[str, str]:
        """Create the board/project; return (id, url)."""

    @abstractmethod
    def push_tasks(self, board_id: str, tasks: list[BoardTask]) -> dict[str, str]:
        """Create one card/issue per task; return task_id -> external id."""

    def expected_location(self, task: BoardTask) -> str:
        """The name of the list/column the plan puts this task's card in."""
        return ""

    def board_members(self, board_id: str) -> list[dict]:
        """Accounts this board can assign a card to. Empty when it has none."""
        return []

    def close(self) -> None:
        """Release whatever the adapter is holding open.

        Declared here with a default because every caller closes the adapter it
        opened, and an implementation with nothing to release should not have
        to write an empty method to avoid an AttributeError at runtime.
        """

    def restore_card(self, board_id: str, external_id: str) -> bool:
        """Put an archived card back, with everything it still holds.

        False when the backend cannot do it, in which case the only route left
        is creating a new card — and that loses the comments and ticks the old
        one carried.
        """
        return False

    def card_snapshots(self, board_id: str) -> dict[str, CardSnapshot]:
        """Every card on the board as it stands: external id -> snapshot.

        Returning nothing is a valid answer for a backend that cannot read its
        board back. Drift then goes unnoticed, which is better than reporting
        it wrongly — and an id missing from a populated mapping is taken to
        mean the card was deleted, so a partial answer would be worse than none.
        """
        return {}

    @abstractmethod
    def update_task(self, board_id: str, external_id: str, task: BoardTask) -> bool:
        """Bring an existing card/issue back in line with the task.

        Returns False when the item is gone from the board — somebody deleted
        it by hand — so the caller can create it again rather than reporting a
        success that left nothing behind.
        """
