"""Synthetic SOW content for the three test documents.

Each SOW is a list of sections; a section is a dict with:
  title: str
  paragraphs: list[str]
  table: optional list[list[str]] (first row = header)

SOW_A  — clean, complete, well-structured (happy path).
SOW_B  — realistic but gappy: missing counts, durations, team sizes
         (exercises the assumption engine).
SOW_C  — messy/adversarial: inconsistent numbering, contradictions,
         duplicated text, pasted email, vague boilerplate
         (exercises parsing validation and grounding).
SOW_D  — full-length and contract-shaped: twenty sections of real SOW,
         most of it not extractable (see content_d.py).
"""

from content_d import SOW_D

SOW_A = {
    "doc_title": "Statement of Work — Loyalty Program Implementation for CairoMart Retail Group",
    "sections": [
        {
            "title": "1. Introduction and Background",
            "paragraphs": [
                "This Statement of Work (SOW) is entered into between LoyaltyCo (the Provider) "
                "and CairoMart Retail Group (the Merchant) for the implementation of a "
                "points-based customer loyalty program across the Merchant's retail network.",
                "CairoMart operates 5 retail branches in Greater Cairo and seeks to launch a "
                "unified loyalty program to increase repeat purchases and customer retention.",
            ],
        },
        {
            "title": "2. Objectives",
            "paragraphs": [
                "Launch a fully operational loyalty points program across all 5 CairoMart branches.",
                "Enable customers to earn 10 points for every 1 USD spent in-store.",
                "Provide the Merchant with 20 configurable promotional offers at launch.",
                "Achieve go-live no later than 1 December 2026.",
            ],
        },
        {
            "title": "3. Scope of Work",
            "paragraphs": [
                "The Provider will deliver: merchant onboarding, loyalty platform configuration, "
                "API integration with the Merchant's point-of-sale (POS) system, offer setup, "
                "staff training, and go-live support.",
                "Out of scope: hardware procurement, POS replacement, and marketing campaign design.",
            ],
        },
        {
            "title": "4. Commercial Requirements",
            "paragraphs": [
                "The Merchant will launch 20 loyalty offers at go-live, distributed across "
                "product categories as agreed with the Account Manager.",
                "Pricing follows the standard LoyaltyCo revenue-share model: 2.5% of the value "
                "of each redeemed offer.",
                "The commercial contract must be countersigned before any technical integration "
                "work begins.",
            ],
            "table": [
                ["Item", "Quantity", "Notes"],
                ["Participating branches", "5", "All Greater Cairo locations"],
                ["Launch offers", "20", "Configured before go-live"],
                ["Merchant onboarding questionnaires", "15", "Commercial team to collect"],
                ["Offer configuration questionnaires", "10", "One per offer family"],
            ],
        },
        {
            "title": "5. Technical Requirements",
            "paragraphs": [
                "The Provider will implement a REST API integration between the LoyaltyCo "
                "platform and the Merchant's POS system for real-time points accrual.",
                "Points calculation rule: customers earn 10 points for every 1 USD spent. "
                "Points expire 12 months after accrual.",
                "Authentication between systems uses OAuth 2.0 client-credentials flow.",
                "The API must sustain 50 transactions per second at peak with p95 latency "
                "under 300 ms.",
                "All API integration work, including end-to-end testing, must be completed "
                "before the go-live date.",
            ],
        },
        {
            "title": "6. Operations Requirements",
            "paragraphs": [
                "Operations will configure the merchant account, the 5 branches, and all 20 "
                "offers in the production environment after technical validation passes.",
                "Merchant onboarding includes collection of branch data, staff rosters, and "
                "offer approval sign-off from the Merchant's commercial representative.",
            ],
        },
        {
            "title": "7. Training",
            "paragraphs": [
                "On-ground training is required for cashier and floor staff at each of the 5 "
                "branches. Training duration is 2 days in total, delivered by the Operations "
                "team in the week before go-live.",
            ],
        },
        {
            "title": "8. Milestones and Timeline",
            "paragraphs": [
                "The project runs from contract signature to go-live per the schedule below.",
            ],
            "table": [
                ["Milestone", "Date", "Owner"],
                ["Contract countersigned", "2026-10-01", "Commercial"],
                ["Technical integration complete", "2026-11-10", "Technical"],
                ["End-to-end testing complete", "2026-11-20", "Technical"],
                ["Merchant configuration complete", "2026-11-24", "Operations"],
                ["Staff training complete", "2026-11-27", "Operations"],
                ["Go-live", "2026-12-01", "All teams"],
            ],
        },
        {
            "title": "9. Team and Responsibilities",
            "paragraphs": [
                "The Provider assigns a cross-functional team of 8 people to this project.",
            ],
            "table": [
                ["Role", "Team", "Responsibility"],
                ["Account Manager", "Commercial", "Merchant relationship, offer sign-off"],
                ["Commercial Manager", "Commercial", "Contract and pricing"],
                ["Technical Lead", "Technical", "Integration architecture"],
                ["Backend Engineer", "Technical", "API development"],
                ["QA Engineer", "Technical", "Testing and validation"],
                ["Operations Manager", "Operations", "Configuration and go-live"],
                ["Merchant Success Specialist", "Operations", "Onboarding and training"],
                ["Support Agent", "Operations", "Post-go-live support"],
            ],
        },
        {
            "title": "10. Service Level Agreements",
            "paragraphs": [
                "Platform availability: 99.5% monthly uptime.",
                "Support response time: 4 business hours for priority incidents during the "
                "first 30 days after go-live.",
            ],
        },
        {
            "title": "11. Acceptance Criteria",
            "paragraphs": [
                "The project is accepted when: all 20 offers are live in production, points "
                "accrue correctly at 10 points per 1 USD in all 5 branches, end-to-end tests "
                "pass, staff training is complete, and the Merchant signs the go-live checklist.",
            ],
        },
    ],
}

SOW_B = {
    "doc_title": "Statement of Work — QuickBite Restaurants Loyalty Launch",
    "sections": [
        {
            "title": "1. Overview",
            "paragraphs": [
                "LoyaltyCo will implement its loyalty points platform for QuickBite Restaurants, "
                "a fast-casual restaurant chain. The engagement covers platform setup, POS "
                "integration, offer configuration, and staff enablement.",
            ],
        },
        {
            "title": "2. Commercial Terms",
            "paragraphs": [
                "QuickBite will launch with a set of promotional offers to be finalized with "
                "the Account Manager during onboarding.",
                "Customers earn points on every purchase; the earn rate will follow the "
                "standard LoyaltyCo scheme unless otherwise agreed in the pricing annex.",
                "A signed contract is a precondition for integration work.",
            ],
        },
        {
            "title": "3. Technical Scope",
            "paragraphs": [
                "Integrate the LoyaltyCo platform with QuickBite's POS system via API.",
                "Points must accrue in near real time and be redeemable in-store.",
                "The integration must be tested end to end before launch.",
            ],
        },
        {
            "title": "4. Operations Scope",
            "paragraphs": [
                "Operations will onboard the merchant, configure participating locations and "
                "offers, and train restaurant staff before launch.",
                "On-ground training is required for all participating locations.",
            ],
        },
        {
            "title": "5. Timeline",
            "paragraphs": [
                "Go-live is targeted for 15 February 2027.",
                "Intermediate milestone dates will be agreed during project kickoff.",
            ],
        },
        {
            "title": "6. Service Levels",
            "paragraphs": [
                "Platform availability and support response times will follow industry-standard "
                "service levels as documented in the master service agreement.",
            ],
        },
    ],
}

SOW_C = {
    "doc_title": "GlowBeauty loyalty project - scope doc v3 FINAL (2)",
    "sections": [
        {
            "title": "1. BACKGROUND",
            "paragraphs": [
                "GlowBeauty is a cosmetics retailer with stores in Cairo, Alexandria and "
                "New Cairo. they want a loyalty program. This document captures the scope as "
                "discussed in the meetings of August 12 and August 19.",
                "THIS DOCUMENT SUPERSEDES ALL PREVIOUS VERSIONS INCLUDING v2 AND THE DRAFT "
                "SHARED BY EMAIL ON AUGUST 14.",
            ],
        },
        {
            "title": "2. What we agreed",
            "paragraphs": [
                "Customers get points when they buy stuff. The earn rate is 5 points per 1 USD "
                "(see pricing annex — TBD).",
                "GlowBeauty wants 12 offers for launch, possibly 15 if the marketing team "
                "approves the extra budget. For planning purposes assume 12.",
                "Go-live is expected in Q1 2027.",
            ],
        },
        {
            "title": "2.1 Offers",
            "paragraphs": [
                "Offer list to be provided by GlowBeauty marketing. Each offer needs: name, "
                "discount value, eligible products, validity window.",
                "Offer list to be provided by GlowBeauty marketing. Each offer needs: name, "
                "discount value, eligible products, validity window.",
            ],
        },
        {
            "title": "4. Technical stuff",
            "paragraphs": [
                "POS integration via API. GlowBeauty uses RetailSoft POS v11 in most stores; "
                "the New Cairo store still runs v9 which may not support the webhook module.",
                "Auth: to be confirmed with GlowBeauty IT. Probably API keys.",
            ],
            "table": [
                ["Store", "POS version", "Integration ready?"],
                ["Cairo Downtown", "v11", "yes"],
                ["Alexandria Corniche", "v11", "yes"],
                ["New Cairo", "v9", "NO - needs upgrade or workaround"],
            ],
        },
        {
            "title": "FWD: RE: RE: training dates",
            "paragraphs": [
                "From: Dina (GlowBeauty HR) — 'we can only release store staff for training on "
                "Mondays, and not during the December sale season. please plan accordingly.' "
                "Sent from my iPhone.",
            ],
        },
        {
            "title": "5. Timeline",
            "paragraphs": [
                "Kickoff: November 2026. Go-live: 15 January 2027.",
                "Note: the commercial team believes the January date is aggressive given the "
                "POS upgrade situation in New Cairo.",
            ],
        },
        {
            "title": "6. Legal boilerplate",
            "paragraphs": [
                "This document is provided for discussion purposes and does not constitute a "
                "binding commitment. All deliverables are subject to the master services "
                "agreement between the parties, as amended from time to time. Nothing in this "
                "document shall be construed to limit either party's rights under applicable law.",
            ],
        },
    ],
}

ALL_SOWS = {
    "sow_a_cairomart": SOW_A,
    "sow_b_quickbite": SOW_B,
    "sow_c_glowbeauty": SOW_C,
    "sow_d_nilebank": SOW_D,
}
