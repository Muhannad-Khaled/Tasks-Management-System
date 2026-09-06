"""Board labels and their colours.

Kept apart from the adapters so BoardTask can name a label without importing
Trello, and so a new adapter inherits the same vocabulary.
"""

from __future__ import annotations

# Every colour Trello accepts on a label, from its own OpenAPI schema. There is
# no grey among them: asking for one fails the POST with "invalid value for
# color", and because labels are created before any card, a single bad colour
# stops the whole push rather than just losing a label.
TRELLO_LABEL_COLORS = frozenset(
    {"yellow", "purple", "blue", "red", "green", "orange", "black", "sky", "pink", "lime"}
)

# Fixed labels, each with a colour that carries meaning at a glance.
LABEL_COLORS: dict[str, str] = {
    "TEAM-COMMERCIAL": "blue",
    "TEAM-TECHNICAL": "purple",
    "TEAM-OPERATIONS": "orange",
    "PRIORITY-HIGH": "red",
    "PRIORITY-MEDIUM": "yellow",
    "PRIORITY-LOW": "lime",
    # Where the task came from.
    "FROM-SOW": "sky",
    "INFERRED": "sky",
    "ASSUMED": "pink",
    # Trello offers no grey, so the neutral states take its darkest colour.
    "SOURCE-UNKNOWN": "black",
    # Whether its claims held up against the SOW.
    "GROUNDED": "green",
    "REVIEW-REQUIRED": "yellow",
    "GROUNDING-FAILED": "red",
    "NOT-CHECKED": "black",
}

ROLE_PREFIX = "ROLE-"
ROLE_COLOR = "black"
FALLBACK_COLOR = "black"


def role_label(role_title: str) -> str:
    """A filterable label for a role.

    Role titles are not a closed set — a SOW may name its own — so these cannot
    live in the table above and get their colour from the prefix instead.
    """
    slug = "".join(c if c.isalnum() else "-" for c in role_title.upper())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"{ROLE_PREFIX}{slug.strip('-')}"


def label_color(name: str) -> str:
    """The colour for a label, always one Trello will accept.

    The fallback is deliberate rather than a guess: a label whose colour the
    table does not know still has to go up, and returning something invalid
    would fail the push for every task, not just this label.
    """
    if name.startswith(ROLE_PREFIX):
        return ROLE_COLOR
    return LABEL_COLORS.get(name, FALLBACK_COLOR)
