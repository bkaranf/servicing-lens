# Mortgage Servicing Dashboard foundation

Status: product and domain baseline for implementation  
Baseline date: 2026-08-11  
Intended audience: product, servicing operations, compliance, data, security, design, and engineering

This directory defines the product and control foundation for an internal mortgage servicing dashboard. Its agent foundation has three deliberately separate parts: LangChain for model/tool abstractions, LangGraph for explicit stateful orchestration and review pauses, and Deep Agents for optional bounded research/analysis. It is intentionally implementation-oriented, but it does not authorize production use, connection to borrower data, servicing actions, or automated decision-making.

## Non-negotiable safety contract

1. All sample, fixture, screenshot, seed, and demo data must be conspicuously labeled **SYNTHETIC**. It must not be derived from real borrowers or production records.
2. The initial product is read-only decision support. It must not move money, post or reverse payments, change escrow, alter loan terms, report to a consumer reporting agency, advance legal action, or send borrower communications.
3. An authorized human must review and approve every payment, escrow, default or loss-mitigation, bankruptcy, foreclosure, and borrower-communication decision outside this application and in the applicable system of record.
4. A model response is an unverified explanation or draft, never a servicing record, legal conclusion, compliance determination, eligibility decision, or source of truth.
5. Deterministic services calculate balances, dates, status, eligibility inputs, and KPIs. LangChain may expose approved model/tool components; LangGraph may orchestrate explicit read-only steps and technical HITL pauses; Deep Agents may later perform bounded, cited research. None may invent authoritative values or make regulated decisions.
6. A LangGraph or Deep Agents HITL resume is a technical event, not sufficient approval for an operational action or borrower communication.
7. Production data access remains blocked until security, privacy, compliance, legal, model-risk, and data-owner approvals are documented.

If an implementation conflicts with this contract, the contract wins until an accountable owner records and approves a replacement architecture decision.

## Document map

| Document | Purpose |
| --- | --- |
| [Product scope](PRODUCT_SCOPE.md) | Vision, boundaries, requirements, success measures, and assumptions |
| [Personas and journeys](PERSONAS_AND_JOURNEYS.md) | Users, permissions, jobs, and end-to-end tasks |
| [Modules and KPIs](DASHBOARD_MODULES_AND_KPIS.md) | Dashboard information architecture and metric definitions |
| [Domain glossary](DOMAIN_GLOSSARY.md) | Shared servicing language and status semantics |
| [Data and interfaces](DATA_AND_INTERFACES.md) | Canonical model, sources, adapters, quality, and LangChain/LangGraph/Deep Agents boundaries |
| [Security and compliance](SECURITY_PRIVACY_AND_COMPLIANCE.md) | PII controls, human-review gates, regulatory issue map, and AI risks |
| [Implementation backlog](IMPLEMENTATION_BACKLOG.md) | Phases, priorities, dependencies, acceptance criteria, and release gates |
| [Architecture decisions](ARCHITECTURE_DECISIONS.md) | Accepted constraints and explicitly deferred choices |
| [`NEXT_PROMPT.md`](../NEXT_PROMPT.md) | Ready-to-use prompt to begin the first implementation slice |

## Authority and change control

The servicing system of record and approved legal/compliance procedures are authoritative for borrower and loan facts. This baseline controls application behavior only after those authorities. KPI definitions require joint sign-off from product, data, servicing operations, and compliance before production use.

Any material change to scope, metric meaning, data classification, automated capability, human-review gates, or regulatory assumptions requires:

- an architecture decision update;
- named product, servicing, compliance, security, and data owners as applicable;
- testable acceptance criteria and rollback behavior; and
- review of downstream reports, prompts, evaluations, and audit events.

## Assumptions to validate before production

- The first release serves internal users, not borrowers, vendors, investors, regulators, or counsel.
- The first build uses only synthetic data and read-only local interfaces.
- Conventional first-lien residential mortgages are the initial analytical population. HELOCs, reverse mortgages, construction loans, subordinate liens, commercial loans, and real-estate-owned assets are excluded unless explicitly added.
- Investor, insurer, guarantor, state, bankruptcy-court, consent-order, and contractual requirements vary and are not encoded by this baseline.
- The application may display imported deadline facts, but it is not the authoritative legal or regulatory deadline engine.
- Organization-specific roles, retention periods, recovery objectives, deployment target, and approved model/provider remain open decisions.

## Definition of foundation complete

This documentation foundation is complete when product, servicing operations, compliance/legal, security/privacy, data governance, and engineering have reviewed it; open decisions have owners and due dates; and the first implementation slice is restricted to synthetic, read-only behavior. Documentation completion is not production approval.
