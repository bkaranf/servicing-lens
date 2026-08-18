# Product scope

## Product identity

Display name: **Public Mortgage Servicing Intelligence**

Required universe statement: **Selected publicly traded U.S. mortgage servicers**

Until a complete ranking methodology is approved, no screen, API description, or
document may call the selected companies the definitive industry leaders, the
largest servicers, or an industry top ten.

The product is a read-only intelligence dashboard for mortgage-servicing
executives, FP&A professionals, investors, and industry analysts. It organizes
authoritative public evidence into reproducible observations of servicing
portfolio size, revenue, expense, profitability, MSR positions, and selected
operating measures.

The primary objective is correct, reproducible financial and servicing
observations with complete provenance. Comparability, updateability,
accessibility, transparent methodology, and source-grounded analytical
explanations are secondary objectives.

## Current repository state

Phase 5 cohort B is the default end-to-end published registry: five banks and
five nonbanks over the bounded Q3 2024 through Q2 2026 filing window. The
checkout replay publishes only the compact configured financial fields supported
by its tracked evidence cases, through the same deterministic parser, validation,
exact-`Decimal`, and atomic persistence path used by explicit live acquisition.
Every published observation retains exact filing and evidence provenance.

Phase 6 exposes populated data through a non-mutating FastAPI application with
GET-only routes, server-rendered Jinja2 pages, local vanilla JavaScript, inline SVG
charts, accessible tables, and bounded evidence drill-through. The sole live SEC
lane is the public core `edgartools` adapter and always requires explicit opt-in.
The socket-blocked checkout replay remains the normal deterministic verification
path; replay assets are not installed as wheel runtime data.

The evidence-vetted supported-universe registry contains ten additional current
registrants (five banks and five nonbanks). Each expansion entry is explicitly
registry-only and not published. It is not an ingest selector, a prior-seven-filing
coverage claim, or a ranking. None of these states claims production readiness,
comprehensive issuer or metric coverage, or industry leadership.

## Current published universe

| Class | Published cohort B issuers | Bounded filing periods |
| --- | --- | --- |
| Banks | TFC, WFC, JPM, BAC, USB | Q3 2024 through Q2 2026 |
| Nonbanks | PFSI, RKT, UWMC, RITM, LDI | Q3 2024 through Q2 2026 |

The versioned cohort registry, not this prose or a ticker literal in code, is the
runtime authority. Cohort A remains an explicit smaller selector for TFC, WFC,
PFSI, and RKT. Selecting either cohort does not assert that every issuer discloses
every field or that different issuer-defined servicing populations are comparable.
The word “published” describes the governed pipeline state, not catalog
completeness.

## Historical Stage A selection (superseded)

Stage A was deliberately limited to:

| Class | Selected issuer | Ticker | Fiscal periods |
| --- | --- | --- | --- |
| Bank | Truist Financial Corporation | TFC | Q3 2025, Q4 2025, Q1 2026, Q2 2026 |
| Nonbank | PennyMac Financial Services, Inc. | PFSI | Q3 2025, Q4 2025, Q1 2026, Q2 2026 |

At least five useful servicing metrics were required to complete the path from
source to dashboard. A metric could remain \`NOT_DISCLOSED\` for either issuer.
Stage A did not force symmetric coverage and never substituted
\`total_servicing_upb\` for \`servicing_for_others_upb\` or \`owned_msr_upb\`.

This selection and its controlled-expansion hold are retained as completed audit
history. They no longer define the default runtime universe; D-021 supersedes
their forward-looking scope after the Phase 5 and Phase 6 gates.

## In-scope user outcomes

Users can:

- see tracked-company coverage, source freshness, latest filings, published
  quarters, pipeline status, missing metrics, and quarantined candidates;
- compare two or three active companies present in the populated database and see
  units, reported or derived state, accessible trends, and pairwise comparability
  results;
- inspect one company's reporting structure, latest earnings event, portfolio,
  servicing economics, MSR position, disclosure changes, and quality warnings;
- open an evidence drawer from every value to inspect the metric definition,
  entity, scope, period, source, accession or regulatory identity, locator,
  reported label and value, normalized value, extraction method, validation,
  revision history, and source link; and
- read methodology covering universe selection, source hierarchy, missing data,
  derivations, bank/nonbank scope, comparability, corporate actions, update
  cadence, and limitations.

Every page shows its data-as-of time and distinguishes reported actual,
preliminary reported, pro forma, announced impact, derived, and not disclosed.

## Required read interfaces

The versioned JSON API exposes only bounded read operations:

- \`GET /api/v1/health\`
- \`GET /api/v1/companies\`
- \`GET /api/v1/companies/{company_id}\`
- \`GET /api/v1/metrics\`
- \`GET /api/v1/observations\`
- \`GET /api/v1/observations/{observation_id}\`
- \`GET /api/v1/comparisons\`
- \`GET /api/v1/coverage\`
- \`GET /api/v1/evidence/{evidence_id}\`
- \`GET /api/v1/earnings-events\`
- \`GET /api/v1/calendar\`
- \`GET /api/v1/pipeline/freshness\`

The dashboard is server-rendered with Jinja2 and enhanced by local vanilla
JavaScript. Charts are local inline SVG with equivalent accessible tables. There
is no HTMX or external chart-library runtime. Public routes are GET-only.

The controlled CLI surface is:

- \`msi doctor\`
- \`msi discover\`
- \`msi sync\` (explicit live SEC access; dry run still reads the SEC)
- \`msi ingest --phase5-cohort-a\` and \`msi ingest --phase5-cohort-b\`
- \`msi discover --live\` and \`msi ingest --live\` (explicit live SEC access)
- \`msi ingest --stage-a\` and \`msi ingest --phase3\` (checkout-only historical
  compatibility)
- \`msi calendar\`
- \`msi coverage\`
- \`msi evidence\`
- \`msi validate\`
- \`msi review list\`
- \`msi review approve\`
- \`msi review reject\`
- \`msi serve\`

Review commands retain the historical quarantine workflow: they create
attributable audit decisions and revisions and never edit published observations
directly. Phase 5 replay and non-dry live publication require an explicit isolated
database URL. A bare installed wheel has the live registries but not checkout-only
replay bytes.

## Explicit exclusions

The product is not and must not become:

- a loan-level servicing operations dashboard, borrower portal, collections
  tool, payment system, loss-mitigation workflow, or servicing action agent;
- a store for borrower, customer, private servicing-system, payment, account,
  authentication, credential, or employer-internal data;
- an accounting ledger, valuation service, audit opinion, regulatory submission
  system, forecast, trading tool, or investment recommendation;
- a generic SQL, HTTP, filesystem, shell, browser, or unrestricted retrieval
  interface for a model;
- a mechanism for a model to publish a number, approve a candidate, fill missing
  disclosure, choose between conflicting evidence, or decide comparability; or
- a claim of comprehensive issuer coverage, production readiness, or a complete
  industry ranking.

## Historical Stage A acceptance outcomes

Stage A closed on August 12, 2026 after the following outcomes were demonstrated.
This checklist is retained as audit history and is not the current universe or
release definition:

1. TFC and PFSI identities and source coverage are verified and ingested.
2. Q3 2025 through Q2 2026 are represented where public disclosure exists.
3. At least five useful servicing metrics are displayed.
4. Every displayed value has observation and evidence IDs.
5. Every value links to a precise source locator.
6. Reingestion creates no duplicate published observations.
7. Missing disclosure remains missing.
8. At least one deliberately ambiguous extraction enters quarantine.
9. Controlled review can approve or reject it with an audit record.
10. At least one comparison is \`not_comparable\` with an explicit reason.
11. All calculations use \`Decimal\` and deterministic formulas.
12. Dashboard, API, and ingestion runtime are deterministic and have no model,
    agent, tracing, or workflow-framework runtime dependency.
13. The complete local quality suite passes.
14. No private servicing, borrower, customer, payment, account, or credential
    data exists in code, fixtures, runtime state, logs, or UI.
15. Documentation describes only behavior actually present or clearly labeled as
    planned Stage A behavior.
