"""Task manager abstraction (brief sections 24 and 27).

The core application talks only to this interface so Trello and Plane can be
evaluated against each other without touching the pipeline.
"""

from __future__ import annotations

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
    source_status: str = "explicit"
    validation_status: str = "pending"
    source_section: str = ""
    source_chunk_keys: list[str] = field(default_factory=list)
    grounding_score: float | None = None
    depends_on_titles: list[str] = field(default_factory=list)
    # The criteria themselves, not a count. A card that says "2 criteria,
    # look them up elsewhere" leaves the person doing the work unable to
    # tell when they are done without opening another system.
    acceptance_criteria: list[str] = field(default_factory=list)
    # Planning fields the task's tests rely on that the SOW never settled.
    assumed_test_fields: list[str] = field(default_factory=list)
    test_cases: list[BoardCase] = field(default_factory=list)

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

        facts = []
        if self.assignee_role:
            facts.append(f"**Role:** {self.assignee_role}")
        if self.depends_on_titles:
            facts.append("**Blocked by:** " + "; ".join(self.depends_on_titles))
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

    def card_locations(self, board_id: str) -> dict[str, str]:
        """Where each card actually sits now: external id -> list name.

        Returning nothing is a valid answer for a backend that cannot report
        this. Drift then goes unnoticed, which is better than reporting it
        wrongly against a board whose shape we cannot read.
        """
        return {}

    @abstractmethod
    def update_task(self, board_id: str, external_id: str, task: BoardTask) -> bool:
        """Bring an existing card/issue back in line with the task.

        Returns False when the item is gone from the board — somebody deleted
        it by hand — so the caller can create it again rather than reporting a
        success that left nothing behind.
        """
