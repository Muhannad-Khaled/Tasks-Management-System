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


class SourceStatus(StrEnum):
    """Provenance of a piece of information (brief section 2)."""

    EXPLICIT = "explicit"  # directly stated in the SOW
    INFERRED = "inferred"  # derived logically from the SOW
    ASSUMED = "assumed"  # introduced by the system to fill a gap


class Priority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


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
    SYNCED = "synced"
