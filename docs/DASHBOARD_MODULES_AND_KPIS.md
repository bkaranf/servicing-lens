# Dashboard modules and KPI definitions

## Metric contract

A number is not an approved KPI until its registry entry contains all of the following:

- stable metric ID, display name, business owner, data owner, and approval status;
- business question and prohibited interpretations;
- grain, population, numerator, denominator, exclusions, null behavior, and unit;
- event time, effective/as-of time, reporting timezone, calendar, and comparison window;
- authoritative inputs, lineage, freshness objective, quality tests, and reconciliation source;
- definition version, effective period, change log, and expected tolerance; and
- privacy class, minimum aggregation threshold, permitted dimensions, and role restrictions.

The UI and API must return metric ID and definition version with every metric. Filters must apply identically to numerator and denominator unless the definition explicitly says otherwise. A zero means a measured zero; missing, stale, excluded, not applicable, and unavailable are distinct states.

All example values used during development are **SYNTHETIC** and must be labeled as such in the application, screenshots, tests, and documentation.

## Shared population and time conventions

These are proposed MVP conventions and require owner approval before production:

- `reporting_as_of`: explicit timezone-aware instant attached to a snapshot.
- `business_date`: source-approved servicing business date; never inferred from a user's browser timezone.
- `active_servicing_population`: first-lien residential loans actively serviced at the snapshot, excluding transferred-out, paid-off, liquidated, or test records. Product/investor exceptions remain explicit.
- `unpaid_principal_balance` (`UPB`): authoritative principal balance as of the snapshot; negative or null values fail quality checks rather than being coerced.
- `days_past_due` (`DPD`): authoritative source value when available. Any calculated fallback must use the approved earliest unpaid contractual due date, business date, calendar rule, and definition version. DPD is an operational classification, not a legal conclusion.
- delinquency buckets: `current` (DPD <= 0), `1–29`, `30–59`, `60–89`, and `90+`; unknown is a separate bucket and is excluded only where the metric says so.
- monetary values: exact decimal plus ISO 4217 currency code; no binary floating-point arithmetic for authoritative calculations.
- rates: retain full precision in calculation and round only for display. The UI shows numerator and denominator.
- month-end comparisons: use approved servicing month-end snapshots, not arbitrary equal-duration subtraction.
- small cohorts: suppress or coarsen values below the privacy/fair-lending threshold approved for the dimension.

## Module 1: Portfolio overview

### Purpose

Answer “What is the current servicing portfolio condition, what changed, and where should an authorized team inspect evidence?” without implying a treatment decision.

### Views

- Context bar: **SYNTHETIC/PRODUCTION classification**, as-of time, business date, timezone, population, freshness, quality status, metric-definition version, and active filters.
- KPI cards: active loans, UPB, weighted average note rate, weighted average remaining term, current rate, 30+ and 90+ delinquency by count and UPB, bankruptcy/foreclosure inventory from source status, open operational exceptions, and unresolved data-quality issues.
- Delinquency distribution by count and UPB.
- Twelve approved snapshot periods of portfolio and delinquency trend.
- Inflow/outflow summary: boarding, servicing transfers, payoff/liquidation, and other source-coded changes.
- Exception preview with age, severity rule, owner queue, source, and last update.

### Required behavior

Every card links to its definition and contributing population. Comparisons state both dates and whether population composition changed. The module never labels a loan “high risk” based only on a model score.

## Module 2: Payments and cash exceptions

### Purpose

Monitor imported payment-processing events and exceptions while preserving accounting and source-system authority.

### Views

- Received, accepted, rejected, returned, posted, reversed, unapplied, and suspense event trends.
- Exception queue by source reason, age, payment channel category, portfolio, and authoritative status.
- Received date, effective date, posted date, reversal date, and return date displayed separately.
- Aggregate unapplied/suspense amount and aging distribution.
- Source reconciliation status; duplicate, orphan, out-of-order, and amount-mismatch quality flags.

### Guardrail

No payment initiation, routing, application, reapplication, reversal, refund, fee, quote, payoff, reinstatement, or ledger mutation. Every related decision requires authorized human review in the system of record.

## Module 3: Escrow monitoring

### Purpose

Surface sourced escrow workload, upcoming analyses/disbursements, and data exceptions without making an escrow decision.

### Views

- Escrowed loan count and balance distribution.
- Imported analysis due/completed status and projected shortage/surplus status.
- Tax and insurance disbursements due within approved horizons, by sourced status.
- Aging vendor exceptions, missing bills/policies, force-placed-insurance source status, and reconciliation flags.
- Loan drill-down separating posted balance, sourced projection, and deterministic calculated explanation.

### Guardrail

Missing tax, insurance, or vendor data is unknown, never zero. All analysis, disbursement, waiver, shortage/surplus, force-placed-insurance, and borrower-communication decisions require authorized human review outside this dashboard.

## Module 4: Delinquency and default monitoring

### Purpose

Monitor operational delinquency migration and sourced default milestones while avoiding automated collection or legal treatment.

### Views

- DPD bucket distribution and changes between approved snapshots.
- Roll-forward/roll-back matrix, cure cohort, first-payment-default indicator if approved, and redefault cohorts with explicit windows.
- Imported early-intervention, continuity-of-contact, loss-mitigation, foreclosure, and hold statuses with source and update time.
- Workload by approved source stage and aging; contradictory or missing milestone queue.
- Geographic or demographic analysis only in an approved compliance/fair-lending workspace with minimum cohort protections, not operational treatment queues.

### Guardrail

No collection priority, application completeness, option eligibility, offer, denial, appeal, referral, filing, sale, or legal deadline decision. Authorized humans must review all default/loss-mitigation, bankruptcy, foreclosure, and borrower-communication decisions.

## Module 5: Loss-mitigation pipeline

### Purpose

Measure sourced application and review workflow status, timeliness, handoffs, and inconsistencies.

### Views

- Source stage inventory: inquiry/solicitation, received, source-designated complete/incomplete, evaluation, decision, offer, trial, permanent outcome, appeal, withdrawn, and closed.
- Stage entry date, age, assigned team, source SLA/deadline status, and next source milestone.
- Document/event reconciliation and duplicate application/case flags.
- Outcome and redefault cohorts only after compliance/fair-lending review of definitions and dimensions.

### Guardrail

The dashboard may repeat a source-system status, clearly attributed; it must not independently declare completeness, eligibility, or outcome. A human authorized under current policies and applicable requirements reviews every decision.

## Module 6: Bankruptcy and foreclosure visibility

### Purpose

Provide a reconciled view of imported case facts and holds for trained specialists without acting as a legal system or deadline engine.

### Views

- Bankruptcy case inventory by source chapter/status, petition/discharge/dismissal source dates, counsel feed freshness, and contradiction flags.
- Foreclosure inventory by source stage, referral/sale source dates, holds, counsel/vendor status, and state/process category.
- Cross-source discrepancy queue that preserves competing facts rather than silently selecting one.

### Guardrail

The module gives no legal advice, stay interpretation, court deadline, filing instruction, foreclosure advancement, sale scheduling, or borrower communication. Missing, stale, or conflicting data fails closed and requires authorized human/counsel review.

## Module 7: Customer care and compliance operations

### Purpose

Monitor case demand, aging, source deadlines, consent/preferences, and complaint themes without resolving a case or composing unsupervised communications.

### Views

- Complaints, notices of error (`NOE`), requests for information (`RFI`), disputes, escalations, and successor-in-interest cases by sourced status and aging.
- Imported acknowledgement/response due status from the authoritative case platform.
- Contact preference, consent, cease-communication, represented-by-counsel, language, accessibility, bankruptcy, and SCRA indicators only where authorized and minimized.
- Template/version metadata and delivery outcome for already-sent communications; content access separately controlled.

### Guardrail

The model may draft internal notes or suggested language only in a later approved phase. Approved templates, deterministic fact validation, required notices/disclosures, consent/channel rules, and authorized human review must precede every borrower communication.

## Module 8: Loan 360 read-only detail

### Purpose

Show the minimum necessary servicing context behind an aggregate or exception.

### Sections

- Pseudonymous loan header: loan token, portfolio/product/source status, as-of time, and warnings.
- Terms and balances; payment facts; escrow facts; delinquency/default; source loss-mitigation, bankruptcy, foreclosure, complaint/case indicators; documents/events metadata; and audit/lineage.
- Timeline built from immutable canonical events, with event time, effective time, ingestion time, source, and correction/supersession relationship.
- Definition drawer and source links for every material value.

### Guardrail

No editable fields, free-form model updates, or outbound action controls. Restricted identity/contact content is absent from MVP and later disclosed field-by-field only when authorized.

## Module 9: Data quality and reconciliation

### Purpose

Make trustworthiness measurable before users act on displayed information.

### Views

- Feed freshness, completeness, schema drift, volume anomalies, referential integrity, duplicates, late/out-of-order events, currency/date validity, and cross-source contradictions.
- Metric reconciliation results against approved source reports, including absolute/relative differences and tolerance.
- Quarantine inventory and downstream impact map.
- Definition/data-contract version rollout and unresolved owner assignments.

### Guardrail

The dashboard never repairs an authoritative record by model inference. Corrections occur in the owning source or through an approved governed transformation.

## Proposed KPI registry

All entries are `PROPOSED` until named owners approve them. `Snapshot` means an approved as-of snapshot; `event window` means `[start, end)` unless otherwise stated.

| ID | Name | Grain and proposed definition | Critical caveat |
| --- | --- | --- | --- |
| PF-001 | Active loan count | Snapshot; distinct active loan tokens in `active_servicing_population` | Do not sum across snapshots |
| PF-002 | Active UPB | Snapshot; sum authoritative UPB over active population, by currency | Null/invalid UPB blocks certified total |
| PF-003 | Weighted average note rate | Snapshot; `sum(UPB × note_rate) / sum(UPB)` for valid positive-UPB loans | Show included UPB coverage; no imputation |
| PF-004 | Weighted average remaining term | Snapshot; `sum(UPB × remaining_contractual_months) / sum(UPB)` | Source maturity and modification state must be current |
| DQ-001 | 30+ delinquency rate, count | Snapshot; loans with DPD >= 30 divided by loans with known DPD in approved active population | Unknown DPD shown separately; count and UPB rates are different metrics |
| DQ-002 | 30+ delinquency rate, UPB | Snapshot; UPB of loans with DPD >= 30 divided by UPB of loans with known DPD | Coverage and denominator UPB shown |
| DQ-003 | 90+ delinquency rate, count | Snapshot; loans with DPD >= 90 divided by loans with known DPD | Bankruptcy/foreclosure inclusion must be explicitly approved |
| DQ-004 | Forward roll rate | Two approved snapshots; loans in bucket A at prior snapshot and a worse bucket at current snapshot divided by surviving comparable loans in A | Cohort transfers/payoffs and missing snapshots handled explicitly |
| DQ-005 | Cure rate | Two snapshots; prior 30+ loans now below 30 DPD divided by comparable surviving prior 30+ cohort | Operational cure definition is not legal reinstatement |
| DQ-006 | Redefault rate | Approved resolved/default-treatment cohort that reaches approved DPD threshold within an explicit observation window divided by eligible cohort | Define cohort event, threshold, and seasoning before use |
| PY-001 | Payment acceptance rate | Event window; accepted payment attempts divided by valid payment attempts from the approved processor population | Not a measure of borrower willingness or loan currentness |
| PY-002 | Payment return rate | Event window; returned payment events divided by settled/accepted events eligible to return | Deduplicate lifecycle events; show lag/seasoning |
| PY-003 | Unapplied/suspense amount | Snapshot; sum authoritative unapplied and suspense balances, reported separately and combined only if accounting approves | Never infer from payment/contract difference |
| PY-004 | Aged posting exceptions | Snapshot; open source-defined posting exceptions older than approved threshold | Threshold and pause states must be versioned |
| ES-001 | Escrow penetration | Snapshot; active escrowed loans divided by active loans with known escrow status | Product eligibility is not implied |
| ES-002 | Escrow analyses due | Snapshot; distinct loans with authoritative analysis due status/date within horizon | Dashboard does not calculate legal due date |
| ES-003 | Escrow disbursements due | Snapshot; sourced unpaid disbursements due within horizon | Missing bill/vendor feed is unknown, not no amount due |
| ES-004 | Projected shortage/surplus inventory | Latest approved source analysis; counts and amounts by source classification | Projection is not posted balance or final borrower decision |
| LM-001 | Open loss-mitigation cases | Snapshot; distinct active cases in approved source stages | A loan may have multiple cases; report both case and loan grain |
| LM-002 | Stage age | Snapshot; business-calendar duration from authoritative stage-entry event to as-of time | Pauses and clock rules must come from approved source logic |
| LM-003 | Source overdue inventory | Snapshot; cases flagged overdue by authoritative workflow engine | Not an independently calculated regulatory breach |
| BK-001 | Active bankruptcy inventory | Snapshot; loans/cases in approved active source statuses | Chapter/status facts need source and counsel freshness |
| FC-001 | Active foreclosure inventory | Snapshot; loans/cases in approved active source stages | “Active” and holds vary by jurisdiction/investor |
| CC-001 | Open complaint count | Snapshot; distinct complaint cases in approved open statuses | Complaint definition/source population must be stable |
| CC-002 | NOE/RFI source overdue inventory | Snapshot; cases marked overdue by authoritative case/deadline engine | Dashboard must not independently decide applicability or deadline |
| CC-003 | First-response time | Resolved event window; approved business duration from received to first qualifying response | Define qualifying event, pauses, and open-case censoring |
| DQY-001 | Critical feed freshness compliance | Snapshot; critical feeds within owner-approved freshness objective divided by expected critical feeds | A green aggregate cannot hide a stale legally material feed |
| DQY-002 | Required-field completeness | Snapshot/field; valid non-null values divided by expected records for fields where required | “Required” depends on record state/product; do not reward fabricated defaults |
| DQY-003 | Reconciliation variance | Snapshot/metric; dashboard value minus authoritative comparison value, with absolute and relative variance | Certified only inside owner-approved tolerance |

## Filters and dimensions

MVP filters: snapshot date, portfolio, product, investor category, servicing status, DPD bucket, state, source system, and exception type. Later dimensions require privacy, fair-lending, and re-identification review.

Rules:

- Dimension values come from governed code sets with effective dates.
- “All” is a defined population, not absence of access policy.
- Unknown/other values remain visible; they are not silently dropped.
- Loan-level entitlements are applied before aggregation to avoid inference across portfolios/tenants.
- Protected-class or proxy attributes are excluded from operational treatment and model prompts. Approved compliance analysis uses a segregated purpose, access path, and methodology.
- URLs/bookmarks carry opaque filter identifiers where raw values could expose restricted information.

## Visualization and interaction standards

- Never encode status by color alone. Pair color with text, icon, pattern, and accessible name.
- Provide a table or textual equivalent for every chart and preserve the same filter/population meaning.
- Axes begin at an honest baseline or conspicuously explain truncation; rate and count charts are distinct.
- Tooltips are keyboard accessible and not the sole location of material definitions.
- Comparison arrows state whether an increase is favorable, unfavorable, or neutral only when an approved owner defines that interpretation.
- Loading skeletons cannot display plausible false values. Errors identify impacted facts and last successful freshness without leaking internals.
- CSV or other exports are not an MVP requirement and remain disabled until separately authorized.
