"""The roles a loyalty-program delivery team is made of.

A task that says which *team* owns it still leaves a PM asking "so who picks
this up?". Roles close that gap without inventing people: the platform has no
HR system, no user table and no auth, so naming an individual would mean
fabricating one — the precise failure mode this system exists to prevent.

So tasks are assigned to a role. Mapping a role to a person is a human act and
stays outside the pipeline (ProjectTask.assignee is reserved for it).

Like PLANNING_FIELDS, this list is the deterministic backstop. A SOW that names
its team is believed; one that does not gets this roster, recorded as ASSUMED.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.enums import Team


@dataclass(frozen=True)
class RoleSpec:
    title: str
    team: Team
    responsibility: str
    # The role work falls to when the model names a role that does not exist.
    # Every team needs exactly one, or unmatched tasks have nowhere to go.
    is_lead: bool = False


TEAM_ROLES: list[RoleSpec] = [
    # Commercial — the business relationship with the merchant.
    RoleSpec(
        title="Commercial Manager",
        team=Team.COMMERCIAL,
        responsibility="Contract, pricing and commercial terms",
        is_lead=True,
    ),
    RoleSpec(
        title="Account Manager",
        team=Team.COMMERCIAL,
        responsibility="Merchant relationship and offer sign-off",
    ),
    RoleSpec(
        title="Business Development Manager",
        team=Team.COMMERCIAL,
        responsibility="Merchant acquisition and deal scoping",
    ),
    RoleSpec(
        title="Legal Advisor",
        team=Team.COMMERCIAL,
        responsibility="Contract review, legal and compliance requirements",
    ),
    # Technical — turning requirements into something buildable and testable.
    RoleSpec(
        title="Technical Lead",
        team=Team.TECHNICAL,
        responsibility="Integration architecture and technical sign-off",
        is_lead=True,
    ),
    RoleSpec(
        title="Backend Engineer",
        team=Team.TECHNICAL,
        responsibility="API development, points logic and integrations",
    ),
    RoleSpec(
        title="Frontend Engineer",
        team=Team.TECHNICAL,
        responsibility="Merchant and customer-facing interfaces",
    ),
    RoleSpec(
        title="AI Engineer",
        team=Team.TECHNICAL,
        responsibility="Models, extraction and intelligent features",
    ),
    RoleSpec(
        title="QA Engineer",
        team=Team.TECHNICAL,
        responsibility="Test cases, validation and acceptance testing",
    ),
    RoleSpec(
        title="DevOps Engineer",
        team=Team.TECHNICAL,
        responsibility="Infrastructure, deployment and environments",
    ),
    # Operations — turning a built system into a running one.
    RoleSpec(
        title="Operations Manager",
        team=Team.OPERATIONS,
        responsibility="Configuration, go-live and operational readiness",
        is_lead=True,
    ),
    RoleSpec(
        title="Operations Specialist",
        team=Team.OPERATIONS,
        responsibility="System, merchant and offer configuration",
    ),
    RoleSpec(
        title="Merchant Success Specialist",
        team=Team.OPERATIONS,
        responsibility="Merchant onboarding and on-ground training",
    ),
    RoleSpec(
        title="Support Agent",
        team=Team.OPERATIONS,
        responsibility="Post-go-live support and incident handling",
    ),
    RoleSpec(
        title="Deployment Specialist",
        team=Team.OPERATIONS,
        responsibility="Release coordination and go-live execution",
    ),
]

ROLES_BY_TEAM: dict[Team, list[RoleSpec]] = {
    team: [r for r in TEAM_ROLES if r.team == team] for team in Team
}

ROLE_TITLES = {r.title for r in TEAM_ROLES}


def roles_for_team(team: Team | str) -> list[RoleSpec]:
    return ROLES_BY_TEAM.get(Team(team), [])


def lead_role(team: Team | str) -> RoleSpec:
    """The role that owns work no more specific role claims."""
    roles = roles_for_team(team)
    return next((r for r in roles if r.is_lead), roles[0])


# Words that appear in many titles and so identify none of them on their own.
# "Engineer" matches five technical roles; "backend" matches exactly one.
_GENERIC_TOKENS = {
    "manager",
    "engineer",
    "specialist",
    "lead",
    "agent",
    "advisor",
    "officer",
    "developer",
    "team",
}

# What the model writes -> what the roster calls it.
_SYNONYMS = {
    "dev": "engineer",
    "developer": "engineer",
    "programmer": "engineer",
    "tester": "qa",
    "testing": "qa",
    "quality": "qa",
    "sre": "devops",
    "infrastructure": "devops",
    "infra": "devops",
    "platform": "devops",
    "architect": "technical",
    "ops": "operations",
    "operational": "operations",
    "bd": "business",
    "lawyer": "legal",
    "counsel": "legal",
    "compliance": "legal",
    "onboarding": "success",
    "training": "success",
    "trainer": "success",
    "ui": "frontend",
    "ux": "frontend",
    "web": "frontend",
    "api": "backend",
    "server": "backend",
    "ml": "ai",
    "llm": "ai",
    "release": "deployment",
    "deploy": "deployment",
    "helpdesk": "support",
}

_DISTINCTIVE_WEIGHT = 10
_GENERIC_WEIGHT = 1


def _tokens(text: str) -> set[str]:
    raw = "".join(c if c.isalnum() else " " for c in text.lower()).split()
    return {_SYNONYMS.get(t, t) for t in raw}


def _score(candidate: RoleSpec, wanted: set[str]) -> int:
    shared = _tokens(candidate.title) & wanted
    distinctive = len(shared - _GENERIC_TOKENS)
    return distinctive * _DISTINCTIVE_WEIGHT + len(shared & _GENERIC_TOKENS) * _GENERIC_WEIGHT


def match_role(
    team: Team | str, raw: str, roles: list[RoleSpec] | None = None
) -> tuple[RoleSpec, str]:
    """Resolve a free-text role to one this team actually has.

    Returns the role and a warning describing any correction, so the audit
    trail records what the model asked for and what it got. An unrecognised
    role falls to the team lead rather than being stored verbatim: a card
    assigned to "Software Engineer" names nobody on the team and is worse
    than one the lead has to hand out.
    """
    # A SOW that names its own roles ("Integration Architect") is telling the
    # truth about this project's team, so those count as valid targets
    # alongside the defaults. Only the fallback comes from the roster.
    roles = roles or roles_for_team(team)
    fallback = next((r for r in roles if r.is_lead), None) or lead_role(team)
    wanted = _tokens(raw)
    if not wanted:
        return fallback, ""

    best = max(roles, key=lambda r: _score(r, wanted))
    # A distinctive token must match. Sharing only "Engineer" or "Manager"
    # picks a role essentially at random.
    if _score(best, wanted) >= _DISTINCTIVE_WEIGHT:
        return best, ""

    other = next(
        (r for r in TEAM_ROLES if r.team != Team(team) and _score(r, wanted) >= _DISTINCTIVE_WEIGHT),
        None,
    )
    if other is not None:
        return fallback, (
            f"role {raw!r} belongs to the {other.team} team but the task is "
            f"{team}; assigned to {fallback.title}"
        )
    return fallback, f"unknown {team} role {raw!r}, assigned to {fallback.title}"


def render_role_guide() -> str:
    """The roster as prompt text, so the model picks from roles that exist.

    Letting it invent a title means every task falls back to the team lead,
    which is the same as having no assignment at all.
    """
    lines = []
    for team, roles in ROLES_BY_TEAM.items():
        titles = ", ".join(r.title for r in roles)
        lines.append(f"- {team}: {titles}")
    return "\n".join(lines)


# Words that place someone on the client's side of the engagement. A merchant's
# own operations manager shares two words with our Operations Manager, so
# without this "merchant operations manager" would be mistaken for our staff.
_CLIENT_SIDE = {
    "merchant",
    "customer",
    "shopper",
    "client",
    "store",
    "branch",
    "cashier",
    "till",
    "retail",
    "end",
}


def is_delivery_role(actor: str) -> bool:
    """Whether a described person is one of *ours* rather than the client's.

    Used to catch a user story written from the delivery team's point of view.
    "As a support agent, I want to deliver training" is a task in disguise: it
    describes work being done, not value someone receives, and a story like
    that cannot be judged done by anyone outside the project.
    """
    wanted = _tokens(actor)
    if not wanted or wanted & _CLIENT_SIDE:
        return False
    return any(_score(role, wanted) >= _DISTINCTIVE_WEIGHT for role in TEAM_ROLES)
