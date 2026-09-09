from enum import StrEnum


class Team(StrEnum):
    COMMERCIAL = "commercial"
    TECHNICAL = "technical"
    OPERATIONS = "operations"


class FieldScope(StrEnum):
    """Who an unanswered planning field is a question for."""

    MERCHANT = "merchant"
    OFFER = "offer"
    PROJECT = "project"


class StoryKind(StrEnum):
    """Whose point of view a user story is written from.

    CLIENT stories say what someone outside the project gets, and an actor
    from the delivery team disqualifies them. ENGINEER stories say how one
    technical task will be built, and a delivery role is the whole point.
    """

    CLIENT = "client"
    ENGINEER = "engineer"


class SourceStatus(StrEnum):
    """Provenance of a piece of information (brief section 2)."""

    EXPLICIT = "explicit"  # directly stated in the SOW
    INFERRED = "inferred"  # derived logically from the SOW
    ASSUMED = "assumed"  # introduced by the system to fill a gap


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    TESTING = "testing"
    DONE = "done"


class ValidationStatus(StrEnum):
    PENDING = "pending"
    GROUNDED = "grounded"
    REVIEW = "review"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"


class ReviewStatus(StrEnum):
    """Where a task stands with the PM (brief section 23)."""

    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"
    REJECTED = "rejected"


class ProjectStatus(StrEnum):
    INGESTING = "ingesting"
    PARSING_FAILED = "parsing_failed"
    EXTRACTING = "extracting"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    # Some tasks are on the board and some are not. The PM pushes task by task,
    # so this is the normal state for most of a review, not an error.
    PARTIALLY_SYNCED = "partially_synced"
    SYNCED = "synced"
