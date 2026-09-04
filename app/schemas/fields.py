"""The planning-critical fields a loyalty-program project needs to be schedulable.

This checklist is the backbone of the assumption engine. Relying on the model to
volunteer what a SOW left out does not work — measured recall was 0/7 gaps on a
deliberately vague SOW. The model never invented the missing numbers, it simply
said nothing about them, which leaves a PM reading a plan with no signal that
its foundations were never specified.

So the gaps are enumerated in code. The model is asked about each field
explicitly and must either cite the SOW text that states it or declare it
absent; anything absent becomes a recorded assumption with a default and a
reason (brief section 12).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.enums import Team


@dataclass(frozen=True)
class FieldSpec:
    key: str
    category: str
    question: str
    team: Team | None
    default: str
    unit: str = ""

    @property
    def label(self) -> str:
        return self.key.replace("_", " ")


# Defaults are deliberately conservative and are always surfaced as ASSUMED,
# never as fact. They exist so the plan is schedulable, not to be believed.
PLANNING_FIELDS: list[FieldSpec] = [
    # Commercial
    FieldSpec(
        key="merchant_count",
        category="COMMERCIAL_SCOPE",
        question="How many merchants or merchant entities are in scope?",
        team=Team.COMMERCIAL,
        default="1",
    ),
    FieldSpec(
        key="offer_count",
        category="OFFER_COUNT",
        question="How many promotional offers will launch?",
        team=Team.COMMERCIAL,
        default="10",
    ),
    FieldSpec(
        key="pricing_terms",
        category="PRICING_TERMS",
        question="What are the pricing or revenue-share terms?",
        team=Team.COMMERCIAL,
        default="standard revenue-share model, terms to be confirmed",
    ),
    FieldSpec(
        key="contract_requirements",
        category="CONTRACT",
        question="What must be signed, and is signature a precondition for other work?",
        team=Team.COMMERCIAL,
        default="signed contract required before integration begins",
    ),
    FieldSpec(
        key="sla_targets",
        category="SLA_TARGETS",
        question="What uptime and support-response service levels are committed?",
        team=Team.COMMERCIAL,
        default="99.5% uptime, 4 business-hour response for priority incidents",
    ),
    # Technical
    FieldSpec(
        key="earn_rate",
        category="EARN_RATE",
        question="How many points do customers earn per unit of currency spent?",
        team=Team.TECHNICAL,
        default="10 points per 1 USD",
    ),
    FieldSpec(
        key="points_expiry",
        category="POINTS_EXPIRY",
        question="When do accrued points expire?",
        team=Team.TECHNICAL,
        default="12 months after accrual",
    ),
    FieldSpec(
        key="auth_mechanism",
        category="AUTH_MECHANISM",
        question="How do the platform and the merchant's system authenticate to each other?",
        team=Team.TECHNICAL,
        default="OAuth 2.0 client-credentials",
    ),
    FieldSpec(
        key="integration_target",
        category="INTEGRATION",
        question="Which merchant system is being integrated with, and by what interface?",
        team=Team.TECHNICAL,
        default="merchant POS via REST API",
    ),
    FieldSpec(
        key="performance_targets",
        category="PERFORMANCE",
        question="What throughput and latency must the integration sustain?",
        team=Team.TECHNICAL,
        default="50 transactions/second peak, p95 under 300 ms",
    ),
    # Operations
    FieldSpec(
        key="location_count",
        category="LOCATION_COUNT",
        question="How many branches, stores, or locations participate?",
        team=Team.OPERATIONS,
        default="3",
    ),
    FieldSpec(
        key="training_duration",
        category="TRAINING_DURATION",
        question="How long is staff training, and are there scheduling constraints?",
        team=Team.OPERATIONS,
        default="2 days",
    ),
    FieldSpec(
        key="go_live_support",
        category="GO_LIVE_SUPPORT",
        question="How long is post-go-live support or hypercare?",
        team=Team.OPERATIONS,
        default="30 days hypercare after go-live",
    ),
    # Project-level
    FieldSpec(
        key="team_size",
        category="TEAM_SIZE",
        question="How many people are assigned, and in which roles?",
        team=None,
        default="8 (2 commercial, 3 technical, 3 operations)",
    ),
    FieldSpec(
        key="project_start_date",
        category="SCHEDULE",
        question="When does the project start (kickoff or contract signature)?",
        team=None,
        default="immediately on contract signature",
    ),
    FieldSpec(
        key="go_live_date",
        category="SCHEDULE",
        question="What is the target go-live date?",
        team=None,
        default="not stated",
    ),
    FieldSpec(
        key="intermediate_milestones",
        category="INTERMEDIATE_MILESTONES",
        question="What dated milestones exist between start and go-live?",
        team=None,
        default="derived from task dependencies; not stated in the SOW",
    ),
]

FIELDS_BY_KEY = {f.key: f for f in PLANNING_FIELDS}


def fields_for_team(team: Team | None) -> list[FieldSpec]:
    return [f for f in PLANNING_FIELDS if f.team == team]
