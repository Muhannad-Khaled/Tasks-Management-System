"""Task manager abstraction (brief sections 24 and 27).

The core application talks only to this interface so Trello and Plane can be
evaluated against each other without touching the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


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
    source_status: str = "explicit"
    validation_status: str = "pending"
    source_section: str = ""
    source_chunk_keys: list[str] = field(default_factory=list)
    grounding_score: float | None = None
    depends_on_titles: list[str] = field(default_factory=list)

    def labels(self) -> list[str]:
        labels = [f"TEAM-{self.team.upper()}", f"PRIORITY-{self.priority.upper()}"]
        labels.append(
            {"explicit": "GROUNDED", "inferred": "INFERRED", "assumed": "ASSUMED"}.get(
                self.source_status, "REVIEW-REQUIRED"
            )
        )
        # A task that only scraped through grounding should say so on the board,
        # not look identical to one the SOW fully supports.
        if self.validation_status == "review":
            labels.append("REVIEW-REQUIRED")
        return labels

    def rendered_description(self) -> str:
        """Card body carrying the traceability the platform cannot model itself."""
        parts = [self.description.strip() or "(no description)"]
        provenance = [f"**Source status:** {self.source_status.upper()}"]
        if self.source_section:
            provenance.append(f"**SOW section:** {self.source_section}")
        if self.source_chunk_keys:
            provenance.append(f"**Evidence chunks:** {', '.join(self.source_chunk_keys)}")
        if self.grounding_score is not None:
            provenance.append(f"**Grounding score:** {self.grounding_score:.0%}")
        if self.depends_on_titles:
            provenance.append("**Blocked by:** " + "; ".join(self.depends_on_titles))
        parts.append("\n".join(provenance))
        parts.append("_Managed by the SOW platform. Full metadata lives in PostgreSQL._")
        return "\n\n---\n\n".join(parts)


@dataclass
class PushResult:
    board_id: str
    board_url: str
    created: dict[str, str] = field(default_factory=dict)  # task_id -> external id


class TaskManagerInterface(ABC):
    """Implemented by TrelloAdapter and (in M7) PlaneAdapter."""

    name: str

    @abstractmethod
    def create_board(self, project_name: str) -> tuple[str, str]:
        """Create the board/project; return (id, url)."""

    @abstractmethod
    def push_tasks(self, board_id: str, tasks: list[BoardTask]) -> dict[str, str]:
        """Create one card/issue per task; return task_id -> external id."""

    def push_project(self, project_name: str, tasks: list[BoardTask]) -> PushResult:
        board_id, board_url = self.create_board(project_name)
        created = self.push_tasks(board_id, tasks)
        return PushResult(board_id=board_id, board_url=board_url, created=created)
