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

The Stage A recorded-data vertical slice is implemented. It provides versioned
TFC/PFSI configuration, hash-verified retained SEC DOM serializations, exact observations and
explicit missingness, SQLAlchemy/Alembic persistence, typed read tools, a
read-only API, server-rendered dashboard pages, a provenance dialog, and an
interruptible review graph. The SEC client and bank regulatory boundary are
implemented but unwired and fail closed; Phase 2 adds governed live acquisition
and structured adapters. This is not
a claim of production readiness or broad market coverage.

## Stage A selection

Stage A is deliberately limited to:

| Class | Selected issuer | Ticker | Fiscal periods |
| --- | --- | --- | --- |
| Bank | Truist Financial Corporation | TFC | Q3 2025, Q4 2025, Q1 2026, Q2 2026 |
| Nonbank | PennyMac Financial Services, Inc. | PFSI | Q3 2025, Q4 2025, Q1 2026, Q2 2026 |

The versioned company-universe configuration, not this prose or a ticker literal
in code, is the runtime authority. It must record verified legal identities,
CIKs, fiscal calendars, reporting entities and scopes, accounting-policy regimes,
and effective dates. Source-discovery evidence must remain reviewable.

At least five useful servicing metrics must complete the path from source to
dashboard. A metric can remain \`NOT_DISCLOSED\` for either issuer. Stage A does not
force symmetric coverage and never substitutes \`total_servicing_upb\` for
\`servicing_for_others_upb\` or \`owned_msr_upb\`.

Controlled expansion is blocked until every Stage A acceptance gate and the
intervening standalone, acquisition, and metric-deepening phase gates pass. The
governing objective permits exactly two additional banks and two additional
nonbanks over the same four-quarter window before UI alignment.

## In-scope user outcomes

Users can:

- see tracked-company coverage, source freshness, latest filings, published
  quarters, pipeline status, missing metrics, and quarantined candidates;
- compare TFC and PFSI over the selected periods and see units, reported or
  derived state, accessible trends, and pairwise comparability results;
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
- \`GET /api/v1/pipeline/freshness\`

The dashboard is server-rendered with Jinja2 and HTMX. Chart assets are hosted
locally and every chart has an equivalent accessible table. Public routes are
read-only.

The controlled CLI surface is:

- \`msi doctor\`
- \`msi discover\`
- \`msi ingest\` (the complete governed Stage A source set)
- \`msi validate\`
- \`msi review list\`
- \`msi review approve\`
- \`msi review reject\`
- \`msi serve\`

Review commands create attributable audit decisions and revisions. They do not
edit published observations directly.

## Explicit exclusions

The product is not and must not become under Stage A:

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

## Stage A acceptance outcomes

Stage A is complete only when all of the following are demonstrated:

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
12. Dashboard and API work with model calls, Deep Agents, tracing, and optional
    LangGraph persistence disabled.
13. The complete local quality suite passes.
14. No private servicing, borrower, customer, payment, account, or credential
    data exists in code, fixtures, graph state, prompts, logs, or UI.
15. Documentation describes only behavior actually present or clearly labeled as
    planned Stage A behavior.
