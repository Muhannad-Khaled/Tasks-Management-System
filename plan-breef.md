AI-Powered SOW-to-Project Execution Platform
1. Project Vision

The goal is to build an AI-powered platform that transforms a company's Scope of Work (SOW) into an executable, traceable, and validated project plan.

The system is designed around a realistic workflow for a Loyalty Points company.

After the Business Development / Commercial team closes a deal with a merchant, the company produces an SOW.

The SOW then becomes the Single Source of Truth for the entire project.

Business Development
        ↓
     Closed Deal
        ↓
       SOW
        ↓
┌─────────────────────────────┐
│ AI SOW Processing Platform  │
└─────────────────────────────┘
        ↓
Commercial + Technical + Operations
        ↓
Requirements → Tasks → Dependencies
        ↓
Timeline → Validation → PM Approval
        ↓
Trello / Plane
        ↓
Project Execution

The platform should not simply "read a PDF and generate tasks."

Instead, it should provide a complete chain:

SOW
 ↓
Requirements
 ↓
Team Responsibilities
 ↓
Assumptions
 ↓
User Stories
 ↓
Tasks
 ↓
Dependencies
 ↓
Timeline
 ↓
Testing
 ↓
Validation
 ↓
PM Approval
 ↓
Task Management
 ↓
Execution Tracking
 ↓
SOW Copilot
2. Core Principle: SOW as the Source of Truth

Every generated requirement, task, dependency, test case, or recommendation should be traceable back to the SOW whenever possible.

For example:

SOW
 └── Section 4.2
      └── Offer Requirements
           └── Requirement
                └── User Story
                     └── Technical Task
                          └── Test Case
                               └── Validation

The system should distinguish between information that is:

Explicit

Directly stated in the SOW.

Source = SOW
Status = EXPLICIT
Inferred

Derived logically from information in the SOW.

Source = SOW
Status = INFERRED
Assumed

Not present in the SOW and introduced by the system because information is missing.

Source = SYSTEM
Status = ASSUMED

This distinction is essential for preventing hallucination.

3. End-to-End Architecture
                         SOW
                          │
                          ▼
                     FastAPI API
                          │
                          ▼
                 Document Processing
                          │
                          ▼
                      LangGraph
                          │
                          ▼
                 Gemini 3.7 Flash
                          │
                          ▼
              Structured SOW Extraction
                          │
             ┌────────────┼────────────┐
             ▼            ▼            ▼
        Commercial    Technical    Operations
             │            │            │
             └────────────┼────────────┘
                          ▼
                  Assumption Engine
                          │
                          ▼
                 Task Decomposition
                          │
                          ▼
                  Dependency Graph
                          │
                          ▼
               Timeline / Critical Path
                          │
                          ▼
              Grounding & Validation
                          │
                          ▼
                  Audit & Logging
                          │
                          ▼
                      HITL / PM
                          │
                     ┌────┴────┐
                     ▼         ▼
                  APPROVE    REJECT
                     │         │
                     │      Regenerate
                     ▼
                  PostgreSQL
                     │
                     ▼
             Task Management Layer
                /             \
               ▼               ▼
            Trello            Plane
                \             /
                 ▼           ▼
                 Project Execution

                          +

                       ChromaDB
                          │
                          ▼
                       RAG Layer
                          │
                          ▼
                  SOW Project Copilot
4. Technology Stack
Layer	Technology
Backend	FastAPI
Agent Workflow	LangGraph
LLM	Gemini 3.7 Flash API
Structured Output	Pydantic
Database	PostgreSQL
Vector Database	ChromaDB
Embeddings	Sentence Transformers
UI	Streamlit
Task Management	Trello / Plane
Notifications	Discord Webhooks
Testing	Pytest
Containerization	Docker
Version Control	GitHub

The architecture should avoid unnecessary paid services.

The Gemini API should use its available free tier, while PostgreSQL, ChromaDB, Streamlit, LangGraph, Pydantic, Docker, and the other open-source components can run locally.

5. SOW Ingestion

The user uploads an SOW through the FastAPI application.

Supported initial formats:

PDF
DOCX
TXT

Workflow:

SOW File
   ↓
FastAPI
   ↓
File Validation
   ↓
Document Parser
   ↓
Text + Tables + Sections
   ↓
Chunking
   ↓
Document Representation

Each piece of information should have a traceable identifier.

Example:

SOW-001
 ├── Section-01
 │    ├── Chunk-01
 │    ├── Chunk-02
 │    └── Chunk-03
 │
 ├── Section-02
 │    ├── Chunk-01
 │    └── Chunk-02

Where possible, we retain:

Document ID
Section
Page
Paragraph
Table
Chunk ID
Original text

This will later power the grounding and citation system.

6. Document Parsing Validation

Before the AI starts reasoning about the SOW, the system validates the parsing result.

Checks include:

Was text extracted?
Are important sections present?
Are tables readable?
Are pages missing?
Is the extracted text suspiciously short?
Are headings detected correctly?
Are duplicate chunks present?

Example:

SOW Parsing Validation

✓ Text extracted
✓ 12 sections detected
✓ 8 tables detected
✓ Page references available
✓ No empty pages detected

Parsing Status:
VALID

If the parser detects a problem:

PARSING_FAILED
       ↓
Human Review / Retry

The LangGraph workflow should not continue blindly.

7. Gemini 3.7 Flash Integration

Gemini 3.7 Flash will be the primary LLM.

It will be used for:

Structured extraction
Classification
Requirement interpretation
Task decomposition
Dependency reasoning
Assumption generation
Timeline reasoning
RAG answer generation
Validation assistance

However, the LLM is not the source of truth.

The source of truth remains:

SOW
+
Validated Structured Data
+
Retrieved Evidence

This is an important architectural principle.

8. Structured SOW Extraction

Gemini receives the processed SOW and produces structured data according to nested Pydantic schemas.

High-level model:

Project
│
├── Project Information
│
├── Commercial
│   ├── Merchants
│   ├── Offers
│   ├── Questions
│   ├── Contracts
│   └── SLAs
│
├── Technical
│   ├── Requirements
│   ├── User Stories
│   ├── Acceptance Criteria
│   ├── Test Cases
│   └── Validation Tests
│
├── Operations
│   ├── System Operations
│   ├── Configurations
│   ├── Training
│   ├── Merchant Onboarding
│   └── Merchant Trades
│
├── Milestones
├── Dependencies
├── Risks
├── Constraints
└── Assumptions

Pydantic validates the structure before anything is persisted.

9. Commercial Team Model

The Commercial team represents the business and client-facing side of the project.

We can explicitly assume team members when the SOW doesn't provide them.

Example:

Role	Example
Account Manager	Mohamed Adel
Business Development Manager	Sara Ahmed
Commercial Manager	Ahmed Mostafa
Legal Advisor	Mariam Ali

The system can extract or assume:

Number of merchants
Number of offers
Number of merchant questions
Number of offer questions
Pricing requirements
Contract requirements
SLA requirements
Commercial milestones
Legal dependencies
Merchant requirements

Example:

Merchants:
5

Offers:
20

Merchant Questions:
15

Offer Questions:
10

If these numbers aren't in the SOW:

Value: 5
Status: ASSUMED
Reason: Not specified in SOW
10. Technical Team Model

Example team:

Role	Example
Technical Lead	Ahmed Hassan
Backend Engineer	Omar Ali
Frontend Engineer	Youssef Samir
AI Engineer	Muhannad Khaled
QA Engineer	Karim Mohamed
DevOps Engineer	Mostafa Ahmed

Technical information includes:

Technical requirements
User stories
Acceptance criteria
APIs
Integrations
Authentication
Data requirements
Performance requirements
Security requirements
Test cases
Technical validation tests

The important chain is:

Technical Requirement
        ↓
User Story
        ↓
Acceptance Criteria
        ↓
Test Case
        ↓
Technical Validation Test
11. Operations Team Model

Example team:

Role	Example
Operations Manager	Hany Mahmoud
Operations Specialist	Mostafa Ahmed
Merchant Success Specialist	Salma Ali
Support Agent	Nour Hassan
Deployment Specialist	Ahmed Samir

Operations responsibilities include:

System operations
System configurations
Merchant configurations
Merchant onboarding
On-ground training
Merchant trades
Go-live support
Post-go-live support

Example:

Technical Release
       ↓
Operations Configuration
       ↓
Merchant Training
       ↓
Merchant Trade
       ↓
Go-Live
12. Assumption Engine

The Assumption Engine is an explicit component rather than allowing the LLM to silently fill gaps.

Every assumption should contain:

assumption_id
category
value
reason
created_by
confidence
status

Example:

{
  "assumption_id": "A-001",
  "category": "TEAM_SIZE",
  "value": "5 Technical Team Members",
  "reason": "SOW does not specify team size",
  "status": "ASSUMED",
  "confidence": 0.82
}

The PM can later approve or modify assumptions.

13. Cross-Functional Task Decomposition

After extraction, LangGraph decomposes the project into tasks.

The three teams can be processed in parallel:

                  Structured SOW
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
     Commercial     Technical     Operations
       Tasks          Tasks          Tasks
          └─────────────┼─────────────┘
                        ▼
                 Dependency Engine

Every task contains:

Task ID
Title
Description
Team
Assignee
Priority
Estimated effort
Start date
Due date
Dependencies
Source SOW section
Grounding status
Assumption references
14. Cross-Team Dependency Graph

One of the most important components is the dependency engine.

Example:

Commercial
Finalize Merchant Contract
        ↓
Technical
Start Merchant Integration
        ↓
Technical
Development
        ↓
Technical
Validation Testing
        ↓
Operations
Merchant Configuration
        ↓
Operations
On-ground Training
        ↓
Operations
Go-Live

Another example:

Commercial
Finalize Offer Rules
        ↓
Technical
Implement Loyalty Rules
        ↓
Technical
Test Points Calculation
        ↓
Operations
Configure Offers
        ↓
Merchant
Start Trading

The system should detect invalid dependency graphs, including circular dependencies.

A → B
B → C
C → A

INVALID
15. Timeline & Critical Path

The system generates a unified project schedule.

Inputs:

SOW dates
Milestones
Task durations
Dependencies
Team capacity
Assignees
Constraints

Then it calculates:

Start dates
Due dates
Critical Path
Slack
Potential delays

The critical path is calculated across the entire project, not independently for each team.

Example:

Contract
   ↓
Requirements
   ↓
Development
   ↓
Testing
   ↓
Configuration
   ↓
Training
   ↓
Go-Live

Validation rule:

Project End Date <= SOW End Date

If the timeline violates the SOW deadline, the system flags it for PM review.

16. Grounding & Evidence System

This is one of the project's major differentiators.

We don't want the AI to simply generate an answer.

We want the AI to prove where the answer came from.

Architecture:

LLM Output
     ↓
Claim Extraction
     ↓
Evidence Retrieval
     ↓
Evidence Verification
     ↓
Grounding Score
     ↓
Accept / Review / Reject

Every claim should have:

Claim
Source
Evidence
Confidence
Validation Status

Example:

Claim:
The merchant will launch 20 offers.

Source:
SOW-001

Section:
Commercial Requirements

Page:
7

Evidence:
"...20 offers..."

Status:
GROUNDED
17. Claim-Level Grounding

Instead of saying:

"The entire answer is grounded."

we validate each claim independently.

Example:

Claim 1:
Merchant configuration is required.
✓ GROUNDED
Source: Section 6.2

Claim 2:
On-ground training is required.
✓ GROUNDED
Source: Section 7.1

Claim 3:
Training will take 3 days.
✗ UNSUPPORTED

The third claim should not be presented as a fact.

The system can either:

remove it
mark it as an assumption
ask for clarification
regenerate the answer
18. Grounding Score

We can introduce a measurable grounding score.

Grounding Score =
Supported Claims / Total Claims

Example:

8 supported
2 unsupported

Grounding Score = 80%

Example policy:

90%+      → ACCEPT
70–89%    → REVIEW
<70%      → REJECT / REGENERATE

The exact thresholds can be tuned during evaluation.

This gives us a measurable way to evaluate hallucination.

19. Source Citation & Traceability

Every important AI-generated object should be linked to evidence.

For example:

Task:
Implement Offer Expiration Rules

Source:
SOW-001

Section:
Offer Requirements

Page:
12

Chunk:
technical_12_03

Grounding:
VERIFIED

This allows the UI to show:

Implement Offer Expiration Rules

Source:
SOW → Offer Requirements → Page 12

✓ Grounded

The user should be able to inspect the underlying evidence.

20. Validation Pipeline

Before generated data enters PostgreSQL:

Gemini
  ↓
Pydantic Validation
  ↓
Grounding Validation
  ↓
Evidence Validation
  ↓
Business Rule Validation
  ↓
Dependency Validation
  ↓
Timeline Validation
  ↓
Persistence
Schema Validation

Is the generated JSON structurally correct?

Grounding Validation

Is the requirement supported by the SOW?

Evidence Validation

Does the cited chunk actually support the claim?

Business Validation

Does the task belong to the correct team?

Dependency Validation

Are dependencies valid?

Timeline Validation

Does the schedule satisfy project constraints?

21. Hallucination Prevention Strategy

The system should use multiple layers rather than relying on the prompt alone.

Layer 1 — Structured Output

Pydantic schemas.

Layer 2 — Retrieval

Only retrieve relevant SOW evidence.

Layer 3 — Source Attribution

Every claim should have source metadata.

Layer 4 — Claim Validation

Verify claims against retrieved evidence.

Layer 5 — Business Rules

Apply deterministic rules.

Layer 6 — Human Review

PM approves important generated information.

Layer 7 — Audit Logs

Keep a history of what the AI generated and why.

This creates a much stronger grounding architecture than standard RAG.

22. AI Audit & Logging

Every LLM interaction should be logged.

Example:

LLMRequest
 ├── request_id
 ├── project_id
 ├── graph_node
 ├── model
 ├── prompt_version
 ├── input_hash
 ├── output
 ├── latency
 ├── token_usage
 └── timestamp

Validation logs:

ValidationLog
 ├── request_id
 ├── claim_id
 ├── source_id
 ├── grounding_score
 ├── validation_result
 ├── rejection_reason
 └── validator_version

This gives us:

Debugging
Reproducibility
Hallucination analysis
Prompt evaluation
Model evaluation
Auditability
23. Human-in-the-Loop

After task generation, LangGraph pauses execution.

Generated Project Plan
        ↓
     INTERRUPT
        ↓
      PM Review

The PM sees:

Teams
Employees
Roles
Tasks
Dependencies
Timeline
Critical Path
Assumptions
Risks
Grounding Scores

The PM can:

APPROVE
EDIT
REJECT
REGENERATE

Importantly, if the PM rejects one task, the system should regenerate that part, not the entire project.

24. Trello / Plane Integration Layer

We will support both:

              Task Manager Interface
                     /       \
                    /         \
               Trello         Plane

The core application should not depend directly on either platform.

Instead:

TaskManagerInterface
       │
       ├── TrelloAdapter
       │
       └── PlaneAdapter

This gives us flexibility to evaluate both.

25. Trello Option

Trello can represent the project as a Board.

Example:

Board:
Loyalty Program - Merchant XYZ

Lists:

Backlog
Commercial
Technical
Operations
Blocked
Testing
Ready for Go-Live
Done

Tasks become Cards.

Example:

Implement Offer Rules

Team:
Technical

Assignee:
Backend Engineer

Priority:
High

Due Date:
2026-10-12

Source:
SOW Section 4.3

Grounding:
Verified

Dependency:
Blocked by Final Offer Rules

Labels can represent:

TEAM-COMMERCIAL
TEAM-TECHNICAL
TEAM-OPERATIONS

PRIORITY-HIGH
PRIORITY-MEDIUM
PRIORITY-LOW

GROUNDED
ASSUMED
REVIEW-REQUIRED

The detailed metadata remains in PostgreSQL.

26. Plane Option

Plane will be the second candidate.

The conceptual structure can be:

Workspace
   ↓
Project
   ↓
Modules / Cycles
   ↓
Issues
   ↓
Sub-Issues

Example:

Project:
Loyalty Program - Merchant XYZ

Issues:
├── Commercial
├── Technical
└── Operations

Plane is particularly interesting because it is open source and supports a more structured project-management workflow.

27. Unified Task Model

Regardless of whether we use Trello or Plane, our internal model remains the same.

ProjectTask
│
├── task_id
├── title
├── description
├── team
├── assignee
├── priority
├── status
├── start_date
├── due_date
├── estimated_hours
├── dependencies
├── source_sow_section
├── source_chunk_ids
├── grounding_score
├── assumption_ids
└── validation_status

Then:

ProjectTask
      ↓
TaskManagerInterface
      ↓
 ┌───────────┐
 ↓           ↓
Trello      Plane
28. Trello vs Plane Evaluation

We will not make the final decision theoretically.

We will implement both adapters and run the same project through both.

Evaluation criteria:

Criteria	Trello	Plane
Free availability	Evaluate	Evaluate
Open Source	No	Yes
Self-hosting	Limited	Yes
API	Yes	Yes
Webhooks	Yes	Yes
Tasks	Yes	Yes
Dependencies	Evaluate	Evaluate
Customization	Good	Strong
Project Structure	Simple	More structured
Team Workflows	Good	Strong
Ease of Setup	Very easy	Moderate
Local/Docker	Limited	Strong
Enterprise-like workflow	Good	Strong

The actual final score should come from our implementation experience.

29. PostgreSQL Persistence Layer

PostgreSQL will store the application's structured project data.

Core entities:

Project
SOW
SOWSection
SOWChunk
Team
Employee
Role
Merchant
Offer
Requirement
UserStory
TestCase
ValidationTest
Milestone
Task
SubTask
Dependency
Assumption
Risk
SLA
AuditLog
LLMRequest
ValidationLog
Evidence

Each task should contain:

team_id
assignee_id
source_sow_section_id
status
priority
start_date
due_date
estimated_hours
grounding_score
validation_status
30. ChromaDB RAG Layer

The SOW will also be embedded into ChromaDB.

Each chunk will contain metadata such as:

{
  "chunk_id": "technical_12_03",
  "document_id": "SOW-001",
  "team": "technical",
  "section": "acceptance_criteria",
  "page": 12,
  "source_type": "sow"
}

This enables team-specific retrieval.

31. Team-Specific SOW Copilot

The system can provide one AI assistant with different retrieval contexts.

Technical User

What are the API acceptance criteria?

Retrieve:

team = technical
section = acceptance_criteria
Commercial User

How many offers are required?

Retrieve:

team = commercial
Operations User

What are the merchant training requirements?

Retrieve:

team = operations

Every answer should contain:

Answer
+
Evidence
+
Source
+
Grounding Score
32. Project Intelligence

The Copilot should eventually understand both the SOW and the project's current execution state.

For example:

Why is the Go-Live delayed?

The system can combine:

SOW
+
PostgreSQL
+
Dependencies
+
Trello / Plane
+
Validation Logs

and produce:

Go-Live
   ↓
Merchant Configuration
   ↓
Technical Validation
   ↓
API Development
   ↓
Blocked

The answer should then be supported by the relevant evidence.

33. Task Manager Synchronization

The application database remains the internal source of truth for structured project data.

The task-management platform represents execution.

PostgreSQL
     ↕
Task Manager

Example:

PostgreSQL
    ↓
Trello Card

If the PM changes the task in Trello:

Trello
   ↓
Webhook
   ↓
FastAPI
   ↓
PostgreSQL
   ↓
Audit Log

The same abstraction can later support Plane.

34. Discord Notifications

Discord Webhooks can provide notifications without introducing another paid service.

Example:

#project-management
#commercial
#technical
#operations

Notifications can include:

New Project Ready for Review

Project:
Merchant XYZ

Tasks:
42

Commercial:
10

Technical:
21

Operations:
11

Critical Path:
7 tasks

Assumptions:
5

Grounding Score:
94%

Status:
Awaiting PM Approval
35. UI

The MVP UI can be built using Streamlit.

Main sections:

Dashboard
│
├── Upload SOW
├── Project Overview
├── Teams
├── Requirements
├── Tasks
├── Dependencies
├── Timeline
├── Critical Path
├── Assumptions
├── Validation
├── Grounding
├── Audit Logs
├── SOW Copilot
└── Task Manager
36. Grounding Dashboard

A dedicated dashboard should visualize AI reliability.

Example:

Grounding Overview

Overall Grounding Score
94%

Commercial
96%

Technical
92%

Operations
95%

Unsupported Claims
3

Assumptions
7

Validation Failures
2

And users can inspect each failure.

Claim:
Training duration = 3 days

Status:
UNSUPPORTED

Reason:
No supporting evidence found in SOW.

Action:
Convert to assumption / regenerate
37. Audit Dashboard

The audit section shows:

Request ID
Model
Graph Node
Timestamp
Prompt Version
Grounding Score
Validation Result
Sources

This becomes extremely useful when evaluating the system.

For example:

Request #1832

Node:
Technical Task Generation

Model:
Gemini 3.7 Flash

Grounding:
91%

Validation:
PASSED

Sources:
SOW Section 4.2
SOW Section 4.3
38. Evaluation Framework

We should treat AI evaluation as a core part of the project.

Create a test dataset of SOW scenarios and measure:

Extraction Accuracy

Did we extract the correct requirements?

Classification Accuracy

Did we assign the requirement to the correct team?

Grounding Accuracy

Does the evidence actually support the claim?

Hallucination Rate

How often did the AI generate unsupported information?

Dependency Accuracy

Are dependencies logically correct?

Timeline Accuracy

Does the generated timeline respect constraints?

Task Coverage

Did every important requirement become a task?

39. End-to-End Example

Suppose the SOW says:

Merchant will launch 20 loyalty offers.

Customers earn 10 points for every $1 spent.

Merchant onboarding and on-ground training are required.

API integration must be completed before launch.

Go-Live:
December 1

The system generates:

Commercial
Confirm 20 offers
Finalize merchant commercial requirements
Technical
Implement points calculation
Implement offer rules
Develop API integration
Create test cases
Run technical validation
Operations
Configure merchant
Configure offers
Conduct on-ground training
Support merchant go-live

Dependencies:

Commercial Offer Confirmation
        ↓
Technical Offer Implementation
        ↓
Technical Testing
        ↓
Operations Configuration
        ↓
Training
        ↓
Go-Live

Then:

Grounding Validation
        ↓
PM Approval
        ↓
Trello / Plane
40. Final Project Architecture

The complete system becomes:

                       ┌──────────────┐
                       │     SOW      │
                       └──────┬───────┘
                              ↓
                       ┌──────────────┐
                       │   FastAPI    │
                       └──────┬───────┘
                              ↓
                  ┌──────────────────────┐
                  │ Document Processing  │
                  └──────────┬───────────┘
                             ↓
                      ┌────────────┐
                      │ LangGraph  │
                      └─────┬──────┘
                            ↓
                  ┌───────────────────┐
                  │ Gemini 3.7 Flash  │
                  └─────────┬─────────┘
                            ↓
                 Structured SOW Model
                            ↓
        ┌───────────────────┼───────────────────┐
        ↓                   ↓                   ↓
   Commercial          Technical           Operations
        ↓                   ↓                   ↓
        └───────────────────┼───────────────────┘
                            ↓
                   Assumption Engine
                            ↓
                   Task Decomposition
                            ↓
                  Dependency Graph
                            ↓
                  Timeline / Critical Path
                            ↓
          ┌─────────────────────────────────┐
          │      Grounding & Validation     │
          │                                 │
          │ Schema Validation               │
          │ Evidence Validation             │
          │ Claim Validation                │
          │ Business Rules                  │
          │ Dependency Validation           │
          │ Timeline Validation             │
          └────────────────┬────────────────┘
                           ↓
                     Audit Logging
                           ↓
                      HITL / PM
                           ↓
                       APPROVED
                           ↓
                     PostgreSQL
                           ↓
                Task Management Layer
                    /             \
                   ↓               ↓
                Trello           Plane
                   \               /
                    └──────┬──────┘
                           ↓
                  Project Execution

                           +

                      ┌──────────┐
                      │ ChromaDB │
                      └────┬─────┘
                           ↓
                         RAG
                           ↓
                  SOW Project Copilot
                           ↓
            Answer + Evidence + Source
                  + Grounding Score
41. Final Development Phases
Phase 1 — Foundation
Repository structure
Docker environment
FastAPI
PostgreSQL
Pydantic models
Basic Streamlit UI
Phase 2 — SOW Processing
PDF/DOCX ingestion
Document parsing
Chunking
Section detection
Parsing validation
Phase 3 — Gemini Integration
Gemini 3.7 Flash integration
Structured extraction
Pydantic validation
Prompt versioning
Phase 4 — Multi-Team Intelligence
Commercial extraction
Technical extraction
Operations extraction
Employee/role assumptions
Assumption engine
Phase 5 — Project Planning
Task decomposition
User stories
Test cases
Validation tests
Dependencies
Critical path
Timeline generation
Phase 6 — Grounding & Validation
Evidence retrieval
Claim extraction
Claim-level grounding
Source attribution
Grounding scoring
Hallucination prevention
Business-rule validation
Phase 7 — Audit & Observability
LLM logging
Validation logging
Prompt versions
Evidence logs
Audit trail
Grounding dashboard
Phase 8 — HITL
LangGraph interrupts
PM dashboard
Approve
Edit
Reject
Partial regeneration
Phase 9 — Task Management

Implement:

TrelloAdapter
PlaneAdapter

Run the same project through both.

Phase 10 — RAG Copilot
ChromaDB
Embeddings
Team-based retrieval
Source citations
Grounding-aware answers
Project intelligence
Phase 11 — Evaluation

Measure:

Extraction accuracy
Grounding accuracy
Hallucination rate
Task coverage
Dependency accuracy
Timeline accuracy
Validation accuracy
Phase 12 — Final Selection

Compare:

Trello vs Plane

based on actual implementation and usability, then select the better option as the project's primary task-management platform.

Final Product Definition

The final product is not just an AI document parser.

It is an:

AI-Powered SOW-to-Project Execution and Intelligence Platform for Loyalty Points Companies

with this core pipeline:

SOW
 ↓
Document Understanding
 ↓
Structured Requirements
 ↓
Commercial / Technical / Operations Classification
 ↓
Assumptions
 ↓
User Stories & Test Cases
 ↓
Task Decomposition
 ↓
Cross-Team Dependencies
 ↓
Critical Path & Timeline
 ↓
Grounding & Evidence Validation
 ↓
Hallucination Prevention
 ↓
Audit Logging
 ↓
Human Approval
 ↓
Trello / Plane
 ↓
Project Execution
 ↓
Continuous Tracking
 ↓
Grounded SOW & Project Copilot

The strongest differentiator should be the combination of SOW traceability + claim-level grounding + deterministic validation + cross-team dependency reasoning + HITL, rather than presenting it as another generic RAG or "PDF-to-Trello" application.