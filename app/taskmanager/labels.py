"""Board labels and their colours.

Kept apart from the adapters so BoardTask can name a label without importing
Trello, and so a new adapter inherits the same vocabulary.
"""

from __future__ import annotations

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
    "SOURCE-UNKNOWN": "light-gray",
    # Whether its claims held up against the SOW.
    "GROUNDED": "green",
    "REVIEW-REQUIRED": "yellow",
    "GROUNDING-FAILED": "red",
    "NOT-CHECKED": "light-gray",
}

ROLE_PREFIX = "ROLE-"
ROLE_COLOR = "black"


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
    """The colour for a label. An unknown label must never silently go grey
    alongside the ones that mean 'unknown'."""
    if name.startswith(ROLE_PREFIX):
        return ROLE_COLOR
    return LABEL_COLORS.get(name, "light-gray")
