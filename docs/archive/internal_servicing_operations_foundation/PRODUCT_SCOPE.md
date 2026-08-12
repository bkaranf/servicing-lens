# Product scope

## Product statement

The Mortgage Servicing Dashboard is an internal, read-only decision-support workspace that helps authorized servicing teams understand portfolio health, locate operational exceptions, inspect a single loan's servicing context, and receive source-grounded explanations. It shortens the path from a portfolio signal to the authoritative records a trained employee needs for follow-up.

The product supports human judgment; it does not replace a servicing platform, accounting ledger, payment processor, legal case-management system, compliance-management system, or borrower-communication platform.

## Target outcomes

- Give an operations manager one consistent view of portfolio status, delinquency migration, cash-posting exceptions, escrow workload, default activity, complaints, and data freshness.
- Let trained specialists move from an aggregate metric to the synthetic contributing records and source lineage without changing those records.
- Make metric definitions, population rules, freshness, and limitations visible at the point of use.
- Establish the three-part agent foundation: LangChain for model/tool abstractions, LangGraph for explicit read-only orchestration/state/HITL, and Deep Agents only for bounded, cited research that cannot make regulated decisions.
- Offer an assisted explanation layer that cites approved source data and documents, states uncertainty, and refuses unsupported or action-taking requests.
- Create evidence for access, query, export, model, and human-review controls before any production-data pilot.

## In scope by release horizon

### Foundation and first vertical slice

- Internal dashboard shell and overview built from a versioned **SYNTHETIC** dataset.
- Portfolio-as-of date, freshness indicator, filters, KPI cards, delinquency distribution, trend visualization, exception queue preview, and read-only loan detail.
- Deterministic metric library with definition metadata and tests.
- Read-only application interfaces, typed schemas, clear empty/error/loading states, and accessibility baseline.
- Development-mode identity and role fixtures that cannot be confused with production authentication.

### Later controlled increments

- Payments and cash exceptions, escrow monitoring, delinquency/default and loss-mitigation monitoring, bankruptcy/foreclosure visibility, customer-care/compliance queues, and data-quality operations.
- Approved warehouse or system-of-record adapters with lineage, reconciliation, and freshness service-level objectives.
- LangChain-based natural-language inquiry over allowlisted read-only tools and approved knowledge, with citations, structured outputs, safety evaluation, and trace redaction.
- Explicit LangGraph workflows where durable state, resumability, or technical human review is justified; optional constrained Deep Agent analysis only for approved complex research.
- Approved exports that retain classification markings and audit context.

## Explicitly out of scope

- A borrower portal, chatbot, voice agent, dialer, email/SMS service, document delivery service, or payment experience.
- Payment initiation, ACH/card capture, cash posting, reversals, fee assessment/waiver, suspense resolution, payoff or reinstatement quotation, or accounting entries.
- Escrow disbursement, analysis, waiver, shortage/surplus disposition, force-placed insurance action, or tax/insurance vendor instruction.
- Automated delinquency treatment, collection strategy, loss-mitigation solicitation, document-completeness determination, eligibility evaluation, underwriting, offer/denial, adverse action, or appeal decision.
- Bankruptcy-stay interpretation, proof-of-claim preparation/filing, legal deadline calculation, attorney instruction, foreclosure referral, first notice or filing, sale scheduling, bidding, eviction, or real-estate-owned management.
- Generation or transmission of borrower-facing communications without approved templates, deterministic data validation, and authorized human review.
- Credit bureau furnishing, dispute resolution, regulatory filing, investor reporting, remittance, custodial accounting, or general-ledger reconciliation.
- Autonomous agents, write-enabled tools, user-authored SQL, arbitrary file/network access, or model access to unrestricted production data.
- Legal advice or a complete codification of federal, state, local, investor, insurer, guarantor, court, consent-order, or contractual requirements.

## Core capabilities and requirements

| ID | Requirement | Initial evidence |
| --- | --- | --- |
| PR-01 | Show the portfolio's explicit as-of time, reporting timezone, freshness, population, and synthetic/production classification. | Header and API metadata tests |
| PR-02 | Present approved KPIs using deterministic definitions and consistent filters. | Metric registry, unit tests, reconciliation fixture |
| PR-03 | Support drill-through from aggregate values to contributing records without exposing more data than the user's role permits. | Filter provenance, row-level authorization tests |
| PR-04 | Display source system, source record identifier or token, ingestion time, calculation version, and known quality flags for material facts. | Lineage panel and contract tests |
| PR-05 | Keep high-impact servicing functions read-only and make human-review requirements visible near relevant content. | No mutation routes/tools; safety copy tests |
| PR-06 | Provide accessible keyboard, screen-reader, contrast, reflow, table, chart-alternative, loading, error, and empty states. | WCAG 2.2 AA-oriented automated and manual checks |
| PR-07 | Record security-relevant use without storing unnecessary restricted data or model prompt content. | Audit schema and redaction tests |
| PR-08 | Fail closed on missing identity, tenant/portfolio entitlement, source lineage, stale critical data, or malformed model/tool output. | Negative authorization and failure-mode tests |
| PR-09 | Keep LangChain, LangGraph, and Deep Agents separately configured and tested; if assistant capability is enabled, distinguish sourced facts, deterministic calculations, model synthesis, uncertainty, and refusal. | Layer-specific kill switches, golden evaluations, and visible citations |
| PR-10 | Make export disabled by default and separately authorized, minimized, marked, time-limited, and auditable when later enabled. | Policy tests and export manifest |

## Quality attributes

- **Accuracy:** Every displayed aggregate is reproducible from versioned inputs and code. Monetary totals reconcile to the approved comparison source within an owner-approved tolerance.
- **Safety:** No route, UI control, tool, or prompt can execute or approve a servicing action in the initial product.
- **Security and privacy:** Least privilege, deny by default, field minimization, masking, encryption, tenant/portfolio isolation, and redacted observability are design requirements.
- **Explainability:** Metric definition, numerator/denominator, exclusions, as-of time, source, and drill-through are available without asking a model.
- **Reliability:** Stale, partial, contradictory, or unavailable inputs produce explicit degraded states; last-known data is never silently presented as current.
- **Performance targets for synthetic MVP:** overview usable within 2 seconds at the 95th percentile on the documented local reference environment; filtered interaction feedback within 500 milliseconds; exact production SLOs deferred.
- **Accessibility:** Target WCAG 2.2 Level AA for complete user journeys, including nonvisual equivalents for charts.
- **Maintainability:** Domain rules, adapters, metrics, authorization, UI, and model orchestration remain separable and independently testable.

## Success measures

MVP measures are validated with synthetic usability tasks, not claimed as business impact:

- 100% of visible KPIs map to an approved definition and deterministic test.
- 100% of material displayed facts expose as-of time and lineage.
- 0 mutation endpoints, write-capable tools, real borrower records, or unredacted restricted fields in telemetry.
- 100% of high-impact module screens show the human-review notice.
- Representative users can identify a synthetic portfolio exception and its contributing loan record without external instructions.
- Accessibility checks have no known critical or serious defects on supported journeys.

Production outcomes require a measured baseline and accountable targets, such as reduced exception-triage time, lower aged-exception inventory, improved data-quality resolution time, and reduced manual report reconciliation. They must not be optimized in ways that encourage inappropriate collections, unequal treatment, rushed loss-mitigation review, or avoidable foreclosure.

## Product principles

1. **Show the evidence.** Provenance and limitations accompany insight.
2. **Deterministic before generative.** Code computes; the model explains.
3. **Read-only before action.** Observation and reconciliation mature before any transactional proposal.
4. **Minimum necessary data.** The best screen is not the one with the most borrower information.
5. **Human accountability stays visible.** High-impact decisions always have a named authorized reviewer outside the model.
6. **Exceptions are not accusations.** A flag indicates review is needed, not borrower fault or employee error.
7. **Time is part of every fact.** Dates, timezones, business calendars, effective periods, and freshness are explicit.

## Open product decisions

| Decision | Required owners | Needed before |
| --- | --- | --- |
| Exact loan products, investors, insurers/guarantors, portfolios, and jurisdictions | Product, servicing, compliance/legal | Real-data mapping |
| Production user groups and separation-of-duties policy | Business owner, identity, security, compliance | Authentication integration |
| Approved KPI definitions, tolerances, and authoritative comparison reports | Finance, servicing, data governance, compliance | Production pilot |
| Which imported deadlines may be shown and their authoritative engines | Legal/compliance, default operations, data owner | Default/legal modules |
| Approved model/provider, data-use terms, region, retention, and tracing | AI governance, security, privacy, legal | Model-enabled pilot |
| Export population, format, purpose, retention, and approval path | Data owner, privacy, security, compliance | Export enablement |
| Production SLOs, RTO/RPO, support, and incident ownership | Product, platform, operations, security | Production readiness |
