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

from app.schemas.enums import FieldScope, Team


@dataclass(frozen=True)
class FieldSpec:
    key: str
    category: str
    question: str
    team: Team | None
    default: str
    unit: str = ""
    # Who the unanswered question is really for. A gap in an offer rule is a
    # question for the merchant about that offer; a gap in the go-live date is
    # a question about the project. This is what turns the assumption list into
    # the merchant and offer question sets a PM actually sends out.
    scope: FieldScope = FieldScope.PROJECT

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
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="offer_count",
        category="OFFER_COUNT",
        question="How many promotional offers will launch?",
        team=Team.COMMERCIAL,
        default="10",
        scope=FieldScope.OFFER,
    ),
    FieldSpec(
        key="pricing_terms",
        category="PRICING_TERMS",
        question="What are the pricing or revenue-share terms?",
        team=Team.COMMERCIAL,
        default="standard revenue-share model, terms to be confirmed",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="contract_requirements",
        category="CONTRACT",
        question="What must be signed, and is signature a precondition for other work?",
        team=Team.COMMERCIAL,
        default="signed contract required before integration begins",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="sla_targets",
        category="SLA_TARGETS",
        question="What uptime and support-response service levels are committed?",
        team=Team.COMMERCIAL,
        default="99.5% uptime, 4 business-hour response for priority incidents",
        scope=FieldScope.MERCHANT,
    ),
    # Technical
    FieldSpec(
        key="earn_rate",
        category="EARN_RATE",
        question="How many points do customers earn per unit of currency spent?",
        team=Team.TECHNICAL,
        default="10 points per 1 USD",
        scope=FieldScope.OFFER,
    ),
    FieldSpec(
        key="points_expiry",
        category="POINTS_EXPIRY",
        question="When do accrued points expire?",
        team=Team.TECHNICAL,
        default="12 months after accrual",
        scope=FieldScope.OFFER,
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
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="training_duration",
        category="TRAINING_DURATION",
        question="How long is staff training, and are there scheduling constraints?",
        team=Team.OPERATIONS,
        default="2 days",
        scope=FieldScope.MERCHANT,
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
    # --- Offer mechanics. A SOW rarely settles all of these, and every gap is
    # a question the merchant must answer before an offer can launch.
    FieldSpec(
        key="offer_eligibility",
        category="OFFER_ELIGIBILITY",
        question="Which purchases or transactions qualify for an offer?",
        team=Team.COMMERCIAL,
        default="all in-store purchases qualify",
        scope=FieldScope.OFFER,
    ),
    FieldSpec(
        key="offer_duration",
        category="OFFER_DURATION",
        question="How long does each offer run?",
        team=Team.COMMERCIAL,
        default="30 days per offer",
        scope=FieldScope.OFFER,
    ),
    FieldSpec(
        key="redemption_rules",
        category="REDEMPTION_RULES",
        question="How do customers redeem points, and against what?",
        team=Team.COMMERCIAL,
        default="points redeemable against purchase value at the till",
        scope=FieldScope.OFFER,
    ),
    FieldSpec(
        key="redemption_limits",
        category="REDEMPTION_LIMITS",
        question="What caps apply per customer, per transaction or per period?",
        team=Team.COMMERCIAL,
        default="no cap stated",
        scope=FieldScope.OFFER,
    ),
    FieldSpec(
        key="customer_eligibility",
        category="CUSTOMER_ELIGIBILITY",
        question="Who can join the loyalty programme, and how do they enrol?",
        team=Team.COMMERCIAL,
        default="any customer may enrol at the till",
        scope=FieldScope.OFFER,
    ),
    FieldSpec(
        key="merchant_specific_offer_rules",
        category="MERCHANT_SPECIFIC_RULES",
        question="Do any offers behave differently for particular merchants or branches?",
        team=Team.COMMERCIAL,
        default="all offers behave identically across merchants",
        scope=FieldScope.OFFER,
    ),
    FieldSpec(
        key="offer_approval_process",
        category="OFFER_APPROVAL",
        question="Who signs off an offer before it goes live?",
        team=Team.COMMERCIAL,
        default="merchant account manager approves each offer",
        scope=FieldScope.OFFER,
    ),
    FieldSpec(
        key="offer_launch_schedule",
        category="OFFER_LAUNCH",
        question="When does each offer go live, and are they staggered?",
        team=Team.COMMERCIAL,
        default="all offers launch together at go-live",
        scope=FieldScope.OFFER,
    ),
    # --- Merchant specifics, gathered during onboarding.
    FieldSpec(
        key="merchant_business_rules",
        category="MERCHANT_BUSINESS_RULES",
        question="What merchant-side business rules constrain the integration?",
        team=Team.COMMERCIAL,
        default="none beyond the standard programme rules",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="merchant_contacts",
        category="MERCHANT_CONTACTS",
        question="Who at the merchant owns the project, and who signs off?",
        team=Team.COMMERCIAL,
        default="one merchant project owner, to be nominated",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="merchant_data_requirements",
        category="MERCHANT_DATA",
        question="What merchant data must be collected before configuration?",
        team=Team.OPERATIONS,
        default="branch list, staff roster and POS terminal inventory",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="merchant_acceptance_criteria",
        category="MERCHANT_ACCEPTANCE",
        question="What must be true for the merchant to accept the delivery?",
        team=Team.COMMERCIAL,
        default="offers live and points accruing correctly in every branch",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="merchant_legal_requirements",
        category="MERCHANT_LEGAL",
        question="What legal, privacy or compliance obligations apply?",
        team=Team.COMMERCIAL,
        default="standard data-protection terms, specifics to be confirmed",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="merchant_support_model",
        category="MERCHANT_SUPPORT",
        question="Who supports merchant staff after go-live, and through what channel?",
        team=Team.OPERATIONS,
        default="provider support desk during business hours",
        scope=FieldScope.MERCHANT,
    ),
    # --- On-ground training. A SOW often says training happens and nothing
    # more, so each unanswered part of it is its own question.
    FieldSpec(
        key="training_required",
        category="TRAINING_REQUIRED",
        question="Is on-ground staff training in scope for the provider?",
        team=Team.OPERATIONS,
        default="yes, the provider delivers on-ground training",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="training_sessions",
        category="TRAINING_SESSIONS",
        question="How many training sessions are delivered, and per what?",
        team=Team.OPERATIONS,
        default="2 sessions per location",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="training_audience",
        category="TRAINING_AUDIENCE",
        question="Who attends training, and how many people?",
        team=Team.OPERATIONS,
        default="merchant operations and till staff",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="training_topics",
        category="TRAINING_TOPICS",
        question="What must training cover?",
        team=Team.OPERATIONS,
        default="enrolment, points accrual, redemption and error handling",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="training_location",
        category="TRAINING_LOCATION",
        question="Where does training take place: on site, remote, or both?",
        team=Team.OPERATIONS,
        default="on site at each merchant location",
        scope=FieldScope.MERCHANT,
    ),
    # --- Operational readiness, from first live trade onwards.
    FieldSpec(
        key="expected_trade_volume",
        category="TRADE_VOLUME",
        question="How many live transactions are expected, and over what period?",
        team=Team.OPERATIONS,
        default="not stated",
        scope=FieldScope.MERCHANT,
    ),
    FieldSpec(
        key="monitoring_requirements",
        category="MONITORING",
        question="What must be monitored in production, and who watches it?",
        team=Team.OPERATIONS,
        default="transaction success rate and API availability, watched by the provider",
    ),
    FieldSpec(
        key="security_requirements",
        category="SECURITY",
        question="What security or compliance standards must the integration meet?",
        team=Team.TECHNICAL,
        default="TLS in transit, credentials held in a secret store",
    ),
]

FIELDS_BY_KEY = {f.key: f for f in PLANNING_FIELDS}


def fields_for_team(team: Team | None) -> list[FieldSpec]:
    return [f for f in PLANNING_FIELDS if f.team == team]


def fields_for_scope(scope: FieldScope | str) -> list[FieldSpec]:
    return [f for f in PLANNING_FIELDS if f.scope == FieldScope(scope)]
