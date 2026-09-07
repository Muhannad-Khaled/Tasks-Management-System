"""SOW D — a full-length, contract-shaped Statement of Work.

The other three documents in this corpus are deliberately small: each one
isolates a single behaviour (the happy path, the gaps, the mess). None of them
is the length or shape of a document a company would actually sign, so none of
them tests what happens when the extraction has to hold its accuracy across
thirty pages of boilerplate, legal clauses, and prose that says nothing.

This one is written the way a real SOW is written — parties and recitals,
in-scope against out-of-scope, obligations on both sides, non-functional
requirements, a payment schedule, change control, and signature blocks. Most
of it is not extractable. That is the point: the plan it produces should
contain the work and not the paperwork.

Two things are left out on purpose, because a real SOW always leaves something
out and the assumption engine has to notice rather than invent:
  - the number of training sessions (section 12 requires training, never sizes it)
  - the data retention period (section 9 requires a policy, never states one)
"""

SOW_D = {
    "doc_title": (
        "Statement of Work — Rewards and Loyalty Platform Implementation "
        "for NileBank Consumer Banking"
    ),
    "sections": [
        {
            "title": "1. Parties and Effective Date",
            "paragraphs": [
                "This Statement of Work (the SOW) is made under and governed by the Master "
                "Services Agreement dated 14 August 2026 (the MSA) between Meridian Loyalty "
                "Systems FZ-LLC, a company incorporated in the United Arab Emirates (the "
                "Provider), and NileBank S.A.E., a public joint stock company incorporated in "
                "the Arab Republic of Egypt (the Client).",
                "The Effective Date of this SOW is 11 January 2027. Where any term of this SOW "
                "conflicts with the MSA, the MSA prevails except in respect of scope, "
                "milestones and fees, which are governed by this SOW.",
                "Capitalised terms not defined in this SOW have the meaning given to them in "
                "the MSA. References to Business Days mean Sunday to Thursday inclusive, "
                "excluding public holidays in the Arab Republic of Egypt.",
            ],
        },
        {
            "title": "2. Background",
            "paragraphs": [
                "The Client operates a consumer banking business serving approximately 1.2 "
                "million active cardholders through a network of 42 branches and a mobile "
                "application. The Client currently operates a points scheme administered "
                "manually by its Cards division on spreadsheets maintained per branch.",
                "The manual scheme has reached the limit of what it can support. Points are "
                "reconciled monthly in arrears, redemption requires a branch visit, and the "
                "Client has no ability to run targeted campaigns. The Client has resolved to "
                "replace it with the Provider's Rewards Platform.",
                "This SOW covers the implementation, integration, migration and go-live of "
                "that platform for the Client's consumer card portfolio. Corporate and "
                "commercial card portfolios are addressed under a separate SOW and are "
                "expressly excluded here.",
            ],
        },
        {
            "title": "3. Objectives",
            "paragraphs": [
                "Replace the manual points scheme with an automated rewards platform "
                "processing card transactions in near real time across all 42 branches and "
                "the Client's mobile application.",
                "Award cardholders 2 points for every 10 EGP of qualifying spend on consumer "
                "credit and debit cards.",
                "Deliver 25 configurable reward offers available to cardholders at launch.",
                "Migrate the existing points balances of 1.2 million cardholders with zero "
                "loss of accrued value.",
                "Achieve production go-live no later than 30 July 2027.",
            ],
        },
        {
            "title": "4. Scope of Work",
            "paragraphs": [
                "The Provider shall deliver the Rewards Platform configured for the Client's "
                "consumer card portfolio, integrated with the Client's core banking system, "
                "card switch, customer relationship management system and notification "
                "gateway, together with the migration of existing balances and the training "
                "of the Client's staff.",
                "Delivery is organised into three phases: Phase 1 Foundation (integration and "
                "core accrual), Phase 2 Rewards (offers, redemption and campaigns), and Phase "
                "3 Launch (migration, training, pilot and go-live). Each phase is subject to "
                "written acceptance by the Client before the next phase commences.",
            ],
        },
        {
            "title": "5. Out of Scope",
            "paragraphs": [
                "The following are expressly excluded from this SOW and shall be the subject "
                "of a separate agreement if required: corporate and commercial card "
                "portfolios; any merchant-funded offer settlement; replacement or upgrade of "
                "the Client's card switch; migration of transaction history older than 24 "
                "months; development of a standalone rewards mobile application; and any "
                "physical rewards fulfilment, warehousing or logistics.",
                "The Provider is not responsible for the accuracy of data supplied by the "
                "Client, for the availability of Client systems outside the Provider's "
                "control, or for delays arising from the Client's regulator.",
            ],
        },
        {
            "title": "6. Commercial Requirements",
            "paragraphs": [
                "The Client shall countersign this SOW and the associated data processing "
                "addendum before any work commences. The Provider shall not begin "
                "implementation against an unsigned SOW.",
                "The Client's Cards division shall complete and return the Provider's "
                "programme questionnaire, covering portfolio segmentation, existing point "
                "balances, campaign history and branch hierarchy, within 15 Business Days of "
                "the Effective Date.",
                "Each of the 25 launch offers requires a completed offer specification signed "
                "off by the Client's Head of Cards. The Provider shall not configure an offer "
                "against an unsigned specification.",
            ],
            "table": [
                ["Item", "Quantity", "Notes"],
                ["Branches in scope", "42", "All consumer branches"],
                ["Active cardholders", "1,200,000", "Consumer credit and debit"],
                ["Launch reward offers", "25", "Signed off before configuration"],
                ["Offer specifications", "25", "One per launch offer"],
                ["Portfolio questionnaires", "6", "One per card product"],
                ["Branch data packs", "42", "Client to supply"],
            ],
        },
        {
            "title": "7. Technical Requirements",
            "paragraphs": [
                "The Provider shall expose a REST API over HTTPS for points accrual, balance "
                "enquiry, redemption and reversal. All endpoints shall accept and return "
                "JSON and shall be versioned in the URL path.",
                "Authentication between the Client's systems and the Rewards Platform shall "
                "use OAuth 2.0 client credentials with mutual TLS. Bearer tokens shall have a "
                "maximum lifetime of 15 minutes. API keys are not acceptable for "
                "server-to-server authentication.",
                "The Rewards Platform shall consume the Client's card authorisation feed in "
                "ISO 8583 format via the Client's card switch, and shall post accrual entries "
                "to the Client's core banking system through its published ISO 20022 "
                "messaging interface.",
                "Every accrual shall be idempotent on the card scheme's Retrieval Reference "
                "Number. A replayed authorisation message shall not award points twice. The "
                "Provider shall demonstrate this behaviour during integration testing.",
                "The Rewards Platform shall reverse points automatically on receipt of a "
                "refund or chargeback message referencing an earlier qualifying transaction, "
                "including where the reversal arrives in a later statement period.",
            ],
            "table": [
                ["Integration", "Direction", "Protocol", "Owner"],
                ["Card switch", "Inbound", "ISO 8583 over MQ", "Client"],
                ["Core banking", "Outbound", "ISO 20022 REST", "Client"],
                ["CRM", "Bidirectional", "REST/JSON", "Provider"],
                ["Notification gateway", "Outbound", "SMPP and REST", "Provider"],
            ],
        },
        {
            "title": "8. Non-Functional Requirements",
            "paragraphs": [
                "The Rewards Platform shall sustain a peak throughput of 450 authorisation "
                "messages per second with a 95th percentile accrual latency not exceeding 400 "
                "milliseconds measured at the platform boundary.",
                "The platform shall provide 99.9% availability measured monthly, excluding "
                "agreed maintenance windows notified at least 5 Business Days in advance.",
                "The platform shall be deployed across two availability zones with automated "
                "failover. Recovery Time Objective is 30 minutes and Recovery Point Objective "
                "is 5 minutes.",
                "The Provider shall conduct load testing at 150% of the stated peak throughput "
                "prior to go-live and shall supply the results to the Client.",
            ],
        },
        {
            "title": "9. Data, Security and Compliance",
            "paragraphs": [
                "All cardholder data in transit shall be protected by TLS 1.3 or later. All "
                "cardholder data at rest shall be encrypted using AES-256 with keys held in a "
                "hardware security module under the Client's control.",
                "The Rewards Platform shall not store primary account numbers. Cards shall be "
                "referenced by a surrogate token issued by the Client's tokenisation service.",
                "The Provider shall comply with PCI DSS v4.0 as a service provider and shall "
                "supply a current Attestation of Compliance before go-live.",
                "The Provider shall maintain an immutable audit log of every points movement, "
                "recording the actor, the originating transaction reference and the "
                "timestamp. The Client shall define a data retention policy for the audit log "
                "and the Provider shall implement it.",
                "Personal data shall be processed in accordance with Egyptian Law No. 151 of "
                "2020 on the Protection of Personal Data and shall not be transferred outside "
                "the Arab Republic of Egypt without the Client's prior written consent.",
            ],
        },
        {
            "title": "10. Client Obligations",
            "paragraphs": [
                "The Client shall provide a non-production environment of its card switch and "
                "core banking system, available to the Provider for integration testing, no "
                "later than 8 February 2027.",
                "The Client shall nominate a single Programme Owner empowered to accept "
                "deliverables and resolve internal disagreement, and shall respond to any "
                "Provider request for a decision within 5 Business Days.",
                "The Client shall supply the existing points balances as a reconciled extract "
                "signed off by its Finance function. The Provider shall not migrate an "
                "unreconciled balance file.",
                "Delay by the Client in meeting an obligation in this section shall extend the "
                "affected milestone dates by the period of the delay, and the Provider shall "
                "notify the Client in writing when it relies on this clause.",
            ],
        },
        {
            "title": "11. Milestones and Delivery Schedule",
            "paragraphs": [
                "The following milestone dates are agreed. Each milestone is met when the "
                "Client has issued written acceptance in accordance with section 15.",
            ],
            "table": [
                ["Milestone", "Date", "Owner"],
                ["SOW countersigned", "2027-01-11", "Commercial"],
                ["Programme questionnaires returned", "2027-02-01", "Commercial"],
                ["Test environments available", "2027-02-08", "Client"],
                ["Integration design accepted", "2027-02-26", "Technical"],
                ["Phase 1 accrual engine complete", "2027-04-16", "Technical"],
                ["Phase 2 offers and redemption complete", "2027-05-28", "Technical"],
                ["Security assessment passed", "2027-06-11", "Technical"],
                ["Balance migration rehearsal complete", "2027-06-25", "Operations"],
                ["Branch staff training complete", "2027-07-09", "Operations"],
                ["Pilot in 5 branches complete", "2027-07-23", "Operations"],
                ["Production go-live", "2027-07-30", "All teams"],
            ],
        },
        {
            "title": "12. Operational Readiness and Training",
            "paragraphs": [
                "The Provider shall configure the Client's branch hierarchy, card products, "
                "accrual rules, the 25 launch offers and the redemption catalogue in the "
                "production environment following successful completion of the security "
                "assessment.",
                "The Provider shall deliver on-site training for branch staff and for the "
                "Client's contact centre. Training shall cover enrolment, balance enquiry, "
                "redemption, dispute handling and escalation. The Client shall make staff "
                "available at its own cost.",
                "The Provider shall supply a runbook covering monitoring, alert thresholds, "
                "incident escalation and the daily reconciliation procedure, and shall walk "
                "the Client's operations team through it before go-live.",
                "A pilot shall be conducted in 5 nominated branches for a period of one week "
                "prior to full rollout. The Client and the Provider shall jointly review "
                "pilot results and agree in writing to proceed.",
            ],
        },
        {
            "title": "13. Service Levels and Support",
            "paragraphs": [
                "The Provider shall provide hypercare support for 30 calendar days following "
                "go-live, staffed during the Client's branch operating hours, after which the "
                "standard support terms of the MSA apply.",
            ],
            "table": [
                ["Severity", "Definition", "Response", "Resolution"],
                ["P1", "Accrual or redemption unavailable", "30 minutes", "4 hours"],
                ["P2", "Degraded performance, workaround exists", "2 hours", "1 Business Day"],
                ["P3", "Single cardholder affected", "1 Business Day", "5 Business Days"],
                ["P4", "Cosmetic or enquiry", "2 Business Days", "Next release"],
            ],
        },
        {
            "title": "14. Project Team and Responsibilities",
            "paragraphs": [
                "The Provider shall assign the following team for the duration of this SOW. "
                "The Provider may substitute personnel of equivalent seniority on written "
                "notice to the Client.",
            ],
            "table": [
                ["Role", "Team", "Responsibility"],
                ["Commercial Manager", "Commercial", "Contract, fees and change control"],
                ["Account Manager", "Commercial", "Client relationship and offer sign-off"],
                ["Legal Advisor", "Commercial", "Data processing addendum and compliance"],
                ["Technical Lead", "Technical", "Integration architecture and design authority"],
                ["Backend Engineer", "Technical", "Accrual engine and REST API"],
                ["AI Engineer", "Technical", "Offer targeting and segmentation models"],
                ["QA Engineer", "Technical", "Test strategy, integration and load testing"],
                ["DevOps Engineer", "Technical", "Environments, deployment and monitoring"],
                ["Operations Manager", "Operations", "Configuration, migration and go-live"],
                ["Merchant Success Specialist", "Operations", "Training and operational readiness"],
                ["Support Agent", "Operations", "Hypercare and incident handling"],
            ],
        },
        {
            "title": "15. Acceptance Criteria",
            "paragraphs": [
                "The Client shall have 10 Business Days from delivery of each milestone to "
                "issue written acceptance or a written statement of the respects in which the "
                "deliverable fails to meet this SOW. A deliverable not rejected within that "
                "period is deemed accepted.",
                "Final acceptance of the implementation requires all of the following: points "
                "accrue at 2 points per 10 EGP across all 42 branches and the mobile "
                "application; all 25 launch offers are live and redeemable in production; the "
                "migrated balances of 1.2 million cardholders reconcile to the Client's signed "
                "extract with zero variance; load testing at 150% of peak throughput completes "
                "within the stated latency; the security assessment is passed with no open "
                "high or critical findings; branch staff training is complete; and the pilot "
                "review concludes in favour of proceeding.",
            ],
        },
        {
            "title": "16. Assumptions and Dependencies",
            "paragraphs": [
                "This SOW is priced and scheduled on the assumption that the Client's card "
                "switch supports the ISO 8583 fields required for the Retrieval Reference "
                "Number, that the Client's core banking system exposes its ISO 20022 "
                "interface in a non-production environment, and that no change to the Client's "
                "card products is made during the implementation period.",
                "Where an assumption in this section proves incorrect, the affected work "
                "shall be handled under section 17.",
            ],
        },
        {
            "title": "17. Change Control",
            "paragraphs": [
                "Any change to the scope, milestones or fees set out in this SOW shall be "
                "documented in a Change Request describing the change, its impact on the "
                "schedule and its impact on the fees. No Change Request is binding until "
                "signed by an authorised representative of each party.",
                "The Provider shall continue to perform in accordance with this SOW while a "
                "Change Request is under discussion, and shall not suspend work pending "
                "agreement.",
            ],
        },
        {
            "title": "18. Fees and Payment Schedule",
            "paragraphs": [
                "The total fee for the work described in this SOW is 4,750,000 EGP exclusive "
                "of value added tax. Fees are payable against the milestones below within 30 "
                "days of receipt of a valid invoice.",
            ],
            "table": [
                ["Payment milestone", "Percentage", "Amount (EGP)"],
                ["SOW countersigned", "20%", "950,000"],
                ["Integration design accepted", "15%", "712,500"],
                ["Phase 1 accrual engine complete", "20%", "950,000"],
                ["Phase 2 offers and redemption complete", "20%", "950,000"],
                ["Production go-live", "25%", "1,187,500"],
            ],
        },
        {
            "title": "19. Intellectual Property and Confidentiality",
            "paragraphs": [
                "All intellectual property rights in the Rewards Platform, including any "
                "enhancement made in the course of this SOW, remain vested in the Provider. "
                "The Client is granted a non-exclusive, non-transferable licence to use the "
                "platform for the term of the MSA.",
                "All Client data, including cardholder data and transaction data, remains the "
                "property of the Client. Each party shall keep the other's confidential "
                "information confidential in accordance with the MSA.",
            ],
        },
        {
            "title": "20. Signatures",
            "paragraphs": [
                "Signed for and on behalf of Meridian Loyalty Systems FZ-LLC by its "
                "authorised representative.",
                "Signed for and on behalf of NileBank S.A.E. by its authorised "
                "representative.",
                "Name: ____________________  Title: ____________________  Date: ____________",
            ],
        },
    ],
}
