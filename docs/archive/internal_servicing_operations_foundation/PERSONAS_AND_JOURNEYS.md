# Personas and journeys

## Access model

Personas describe work, not entitlements. Production permissions must come from centrally governed groups and portfolio/tenant attributes, be deny-by-default, and be reviewed periodically. A single employee may hold more than one approved role, but separation-of-duties rules still apply.

The first synthetic build may simulate roles for testing. Every simulated identity must display a prominent **DEVELOPMENT / SYNTHETIC** marker and must be impossible to enable in a production configuration.

## Primary personas

| Persona | Primary jobs | Minimum information | Must not do in this dashboard |
| --- | --- | --- | --- |
| Servicing operations manager | Monitor portfolio health; identify workload changes; route exceptions to existing systems; reconcile operational reports | Aggregates, trend and cohort filters, exception metadata, source lineage | Approve or execute loan-level financial, default, legal, or communication actions |
| Payment operations specialist | Find rejected, returned, unapplied, suspense, and aging posting exceptions; compare source events | Payment event tokens, status, amount, effective/received/posting dates, reason, source | Initiate payment, change application, reverse, waive fee, quote amounts, or resolve suspense |
| Escrow specialist | Monitor analyses and disbursements due; investigate projected shortage/surplus and vendor exceptions | Escrow status, analysis/disbursement dates, aggregate components, vendor/source status | Change escrow terms, disburse funds, order insurance, decide waiver, or communicate shortage/surplus |
| Customer-care supervisor | View contact demand, complaints, requests for information, notices of error, and service levels | Case metadata, categories, aging, channel/consent indicators, linked facts | Contact a borrower, promise an outcome, resolve a dispute, or send model-generated language |
| Default or loss-mitigation manager | Monitor delinquency migration, early-intervention workload, application pipeline, review aging, and imported milestones | Delinquency facts, submitted/received event status, authoritative milestone tokens, exception flags | Decide completeness, eligibility, offer, denial, appeal, foreclosure referral, or legal deadline |
| Bankruptcy or foreclosure specialist | View externally managed case status, holds, counsel feeds, and inconsistencies | Case identifiers, court/counsel source facts, hold status, source timestamps | Interpret a stay, calculate/commit a deadline, file documents, instruct counsel, or advance foreclosure |
| Compliance or quality reviewer | Sample activity; examine definitions, overrides, communication history metadata, and audit evidence; detect control gaps | Cross-module read access where approved, policy version, lineage, audit events | Treat model output as legal analysis, edit source records, or close findings without the established CMS |
| Portfolio executive | Understand aggregated performance, risk, and operational capacity | Masked portfolio summaries and approved cohorts | Browse unnecessary loan-level PII or use preliminary metrics as regulatory/investor reporting |
| Data steward or analyst | Reconcile feeds, profile quality, approve definitions, trace lineage, investigate data drift | Canonical fields, source mappings, quality results, definition versions | Bypass source corrections, infer missing borrower facts with a model, or publish unapproved KPIs |
| Platform/security administrator | Operate configuration, identity mapping, secrets, audit export, and incident controls | Technical metadata; content only when break-glass is authorized | Receive routine borrower-content access merely because of administrator status |

Borrowers, applicants, vendors, legal counsel, investors, regulators, and consumer reporting agencies are external stakeholders, not initial application users.

## Information-access tiers

| Tier | Examples | Default access |
| --- | --- | --- |
| A — portfolio aggregate | Counts, rates, sums, cohorts with re-identification controls | Authorized business viewers |
| B — pseudonymous loan detail | Internal loan token, status, amounts, dates, source lineage | Operational roles with portfolio entitlement |
| C — restricted borrower data | Name, address, contact data, full loan/account numbers, bank data, tax/insurance documents, case narratives | Excluded from MVP; later field-specific authorization only |
| D — specially handled data | SSN/TIN, credentials, authentication factors, payment instrument data, full credit report, sensitive legal/medical/military indicators | Do not ingest unless expressly required and approved; never expose to a general-purpose model |

Masking is not authorization. The service layer must enforce tenant, portfolio, role, purpose, and field-level policy before data reaches the UI, export, cache, log, trace, or model.

## Core journeys

### J-01: Portfolio signal to contributing records

1. An operations manager opens the overview and confirms **SYNTHETIC**, as-of time, population, timezone, freshness, and quality status.
2. The manager inspects an approved KPI definition and trend.
3. The manager applies portfolio, investor, geography, product, and delinquency filters; each active filter remains visible.
4. The manager drills into the contributing synthetic loan tokens.
5. The manager opens a record, verifies source lineage and known data-quality flags, then records no servicing action in this dashboard.

Acceptance: browser back/forward and shared application state do not lose filter meaning; aggregate and result counts reconcile; restricted fields remain absent; access and export attempts are audited.

### J-02: Payment exception triage

1. A payment specialist opens the exception queue and sorts by age and exception type.
2. The specialist sees received, effective, and posted dates as distinct fields, with timezone and source.
3. The specialist compares the event with related suspense/unapplied status and source-quality flags.
4. The dashboard links to the approved system-of-record workflow when configured; otherwise it provides copy-safe identifiers only.
5. Any decision to post, reverse, reapply, refund, waive, or communicate occurs through an authorized human in an approved external system.

Acceptance: the dashboard contains no action control; amounts use exact decimal/currency semantics; stale or incomplete feeds block definitive wording.

### J-03: Escrow workload review

1. An escrow specialist filters analyses or disbursements due within a selected horizon.
2. The dashboard explains whether a projected value is sourced or calculated and shows its definition version.
3. The specialist sees missing tax/insurance/vendor data as an explicit exception, never as zero.
4. The specialist follows the external case reference for investigation.
5. An authorized human reviews every escrow decision and borrower communication outside the dashboard.

Acceptance: projections are labeled and cannot be confused with posted balances; missing inputs do not generate a model estimate.

### J-04: Delinquency and loss-mitigation monitoring

1. A default manager reviews delinquency buckets, roll/cure movement, and application-stage inventory.
2. The manager narrows to aging or inconsistent records and sees imported milestone facts with their authoritative source.
3. The system does not declare an application complete/incomplete or a borrower eligible/ineligible unless displaying a verbatim approved source-system status with timestamp and lineage.
4. The manager routes follow-up through the existing case-management process.
5. An authorized human performs all default, loss-mitigation, and borrower-communication decisions.

Acceptance: model-generated recommendations cannot alter queue priority, suppress protections, or determine outcomes; protected-class data is not used for operational prioritization.

### J-05: Bankruptcy or foreclosure inconsistency

1. A specialist views a discrepancy between servicing, legal/counsel, and case-management feeds.
2. The dashboard displays each source fact separately instead of choosing a legal truth.
3. A prominent hold/review state prevents the UI or assistant from suggesting advancement.
4. The specialist consults authorized counsel or the authoritative workflow.
5. An authorized human resolves the source record and documents review outside the dashboard.

Acceptance: no legal advice, stay interpretation, deadline commitment, referral, filing, or sale action is available; missing/contradictory legal data fails closed.

### J-06: Source-grounded assistant question (later phase)

1. An entitled user asks a portfolio or loan-context question.
2. The policy layer determines allowed scope before retrieval or tool use.
3. The assistant calls only typed, read-only, allowlisted tools; deterministic services return metrics and facts.
4. The response separates sourced facts from synthesis, cites records/definitions, states as-of time and limitations, and refuses action-taking or legal requests.
5. The user verifies source evidence and performs any follow-up in an approved process.

Acceptance: unsupported claims, cross-tenant requests, prompt-injection attempts, restricted-field requests, and high-impact action requests are refused and safely audited.

## Permission baseline

| Capability | Executive | Operations | Specialist | Compliance/QA | Data steward | Platform admin |
| --- | --- | --- | --- | --- | --- | --- |
| View approved aggregates | Allow | Allow | Role-scoped | Allow | Allow | No content by default |
| View pseudonymous loan detail | Deny by default | Portfolio-scoped | Work-queue scoped | Approved review scope | Approved investigation scope | Deny by default |
| View restricted borrower fields | Deny | Exceptional | Field/purpose scoped | Case-approved | Exceptional | Break-glass only |
| Use read-only assistant | Aggregate only | Portfolio-scoped | Work-scope scoped | Review scope | Definition/quality scope | Operational diagnostics only |
| Export | Deny by default | Separate grant | Separate grant | Separate grant | Separate grant | Administer policy, not content entitlement |
| Change source data or servicing state | Deny | Deny | Deny | Deny | Deny | Deny |

## Human-review record for future integrations

If a later version proposes a handoff, each payment, escrow, default/loss-mitigation, bankruptcy, foreclosure, or borrower-communication decision must capture outside the model:

- unique decision and loan/case tokens;
- decision category and exact proposed action;
- authoritative input references and as-of times;
- applicable policy/rule/template versions;
- model involvement, if any, clearly labeled as advisory;
- authorized reviewer's identity, role, timestamp, and explicit disposition;
- second approval where separation of duties requires it;
- target system result, idempotency key, and reconciliation outcome; and
- correction, appeal, override, and incident link where applicable.

No future implementation should infer approval from page views, elapsed time, lack of response, bulk selection, or a generic acknowledgement.
