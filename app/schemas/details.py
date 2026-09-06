"""The things a SOW enumerates, per team.

Two kinds of fact come out of a SOW and they need different machinery:

- Scalar planning values — the earn rate, the auth mechanism, how long training
  runs. There is exactly one of each, a SOW either settles it or does not, and
  PLANNING_FIELDS plus the assumption engine already own them.
- Enumerable items — *which* APIs, *which* integrations, *which* merchants.
  There are many, the SOW either lists them or does not, and nothing recorded
  them until now.

This module covers the second kind. Keeping them in one categorised list rather
than a nested model per team is deliberate: every item needs the same three
things (what it is, where it came from, whether it held up), and a flat list
keeps the extraction schema small enough to stay reliable.
"""

from __future__ import annotations

from enum import StrEnum

from app.schemas.enums import Team


class DetailCategory(StrEnum):
    # Commercial
    MERCHANT = "merchant"
    OFFER = "offer"
    CONTRACT = "contract"
    PRICING_TERM = "pricing_term"
    SLA = "sla"
    LEGAL_REQUIREMENT = "legal_requirement"
    APPROVAL = "approval"
    COMMERCIAL_MILESTONE = "commercial_milestone"
    # Technical
    API = "api"
    INTEGRATION = "integration"
    AUTHENTICATION = "authentication"
    DATA_REQUIREMENT = "data_requirement"
    SECURITY_REQUIREMENT = "security_requirement"
    PERFORMANCE_REQUIREMENT = "performance_requirement"
    INFRASTRUCTURE_REQUIREMENT = "infrastructure_requirement"
    # Operations
    SYSTEM_OPERATION = "system_operation"
    SYSTEM_CONFIGURATION = "system_configuration"
    ONBOARDING_STEP = "onboarding_step"
    TRAINING_TOPIC = "training_topic"
    MERCHANT_TRADE = "merchant_trade"
    GO_LIVE_SUPPORT = "go_live_support"
    POST_GO_LIVE_SUPPORT = "post_go_live_support"


# Which team owns each category. This is a property of the category, not
# something the model gets to decide: an API belongs to the technical team
# whichever section of the SOW happens to mention it.
CATEGORY_TEAM: dict[DetailCategory, Team] = {
    DetailCategory.MERCHANT: Team.COMMERCIAL,
    DetailCategory.OFFER: Team.COMMERCIAL,
    DetailCategory.CONTRACT: Team.COMMERCIAL,
    DetailCategory.PRICING_TERM: Team.COMMERCIAL,
    DetailCategory.SLA: Team.COMMERCIAL,
    DetailCategory.LEGAL_REQUIREMENT: Team.COMMERCIAL,
    DetailCategory.APPROVAL: Team.COMMERCIAL,
    DetailCategory.COMMERCIAL_MILESTONE: Team.COMMERCIAL,
    DetailCategory.API: Team.TECHNICAL,
    DetailCategory.INTEGRATION: Team.TECHNICAL,
    DetailCategory.AUTHENTICATION: Team.TECHNICAL,
    DetailCategory.DATA_REQUIREMENT: Team.TECHNICAL,
    DetailCategory.SECURITY_REQUIREMENT: Team.TECHNICAL,
    DetailCategory.PERFORMANCE_REQUIREMENT: Team.TECHNICAL,
    DetailCategory.INFRASTRUCTURE_REQUIREMENT: Team.TECHNICAL,
    DetailCategory.SYSTEM_OPERATION: Team.OPERATIONS,
    DetailCategory.SYSTEM_CONFIGURATION: Team.OPERATIONS,
    DetailCategory.ONBOARDING_STEP: Team.OPERATIONS,
    DetailCategory.TRAINING_TOPIC: Team.OPERATIONS,
    DetailCategory.MERCHANT_TRADE: Team.OPERATIONS,
    DetailCategory.GO_LIVE_SUPPORT: Team.OPERATIONS,
    DetailCategory.POST_GO_LIVE_SUPPORT: Team.OPERATIONS,
}

# What each category means, so the model files items consistently rather than
# guessing from the name.
CATEGORY_HELP: dict[DetailCategory, str] = {
    DetailCategory.MERCHANT: "a merchant or merchant entity in scope",
    DetailCategory.OFFER: "a promotional offer to be launched",
    DetailCategory.CONTRACT: "a document that must be signed",
    DetailCategory.PRICING_TERM: "a pricing, fee or revenue-share term",
    DetailCategory.SLA: "a committed service level",
    DetailCategory.LEGAL_REQUIREMENT: "a legal or compliance obligation",
    DetailCategory.APPROVAL: "a sign-off the merchant or provider must give",
    DetailCategory.COMMERCIAL_MILESTONE: "a dated commercial checkpoint",
    DetailCategory.API: "an API endpoint or surface to be built or consumed",
    DetailCategory.INTEGRATION: "a system being integrated with",
    DetailCategory.AUTHENTICATION: "an authentication or authorisation mechanism",
    DetailCategory.DATA_REQUIREMENT: "data that must be stored, exchanged or migrated",
    DetailCategory.SECURITY_REQUIREMENT: "a security control or obligation",
    DetailCategory.PERFORMANCE_REQUIREMENT: "a throughput, latency or capacity target",
    DetailCategory.INFRASTRUCTURE_REQUIREMENT: "hosting, environments or infrastructure",
    DetailCategory.SYSTEM_OPERATION: "monitoring, support or incident handling in production",
    DetailCategory.SYSTEM_CONFIGURATION: "something that must be configured before go-live",
    DetailCategory.ONBOARDING_STEP: "a step in bringing a merchant onto the platform",
    DetailCategory.TRAINING_TOPIC: "a subject staff must be trained on",
    DetailCategory.MERCHANT_TRADE: "a live or test transaction scenario to validate",
    DetailCategory.GO_LIVE_SUPPORT: "support committed around go-live",
    DetailCategory.POST_GO_LIVE_SUPPORT: "support committed after go-live",
}


def team_for(category: DetailCategory | str) -> Team:
    return CATEGORY_TEAM[DetailCategory(category)]


def categories_for_team(team: Team | str) -> list[DetailCategory]:
    return [c for c, t in CATEGORY_TEAM.items() if t == Team(team)]


def render_category_guide() -> str:
    """The categories as prompt text, grouped by the team that owns them."""
    lines = []
    for team in Team:
        lines.append(f"{team}:")
        lines.extend(
            f"  - {category}: {CATEGORY_HELP[category]}" for category in categories_for_team(team)
        )
    return "\n".join(lines)
