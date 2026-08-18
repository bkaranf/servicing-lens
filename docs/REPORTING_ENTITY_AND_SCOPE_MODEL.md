# Reporting entity and scope model

## Why entity and scope are separate

A corporate family is not one interchangeable reporting boundary. The product
separately models the public company, its securities, SEC registrant, bank holding
company, insured depository institution, reportable segment, servicing subsidiary,
and portfolio population. A fact belongs to the entity and scope named by its
source.

Ownership, a shared brand, or a common ticker does not authorize combining facts.
The system fails closed when it cannot resolve entity or scope.

## Core concepts

| Concept | Meaning | Identity rule |
| --- | --- | --- |
| Company | Stable corporate subject followed across names, tickers, and actions | Surrogate company ID; never ticker alone |
| Security | Effective-dated traded instrument | Security ID plus exchange/ticker history |
| Reporting entity | Legal entity, regulator reporter, segment, or disclosed operating unit to which a fact applies | Stable entity ID plus typed identifiers |
| Reporting scope | Population and business boundary represented by one observation | Versioned scope ID; never inferred from a metric label alone |
| Entity identifier | CIK, RSSD, LEI, FDIC certificate, regulator ID, ticker, or other namespace value | Namespace, normalized value, valid time, source evidence |
| Entity relationship | Effective-dated ownership, predecessor, successor, consolidation, segment, or servicing relationship | Typed directed edge with evidence |
| Fiscal-calendar regime | Rules mapping dates to fiscal years and quarters | Effective-dated regime; exact start/end dates remain on observations |
| Accounting-policy regime | Relevant measurement and presentation policy | Effective-dated policy with source evidence |
| Corporate action | Acquisition, divestiture, rename, ticker change, reorganization, or material portfolio transfer | Announcement, close/effective dates, parties, and continuity effect |

## Reporting-entity types

The controlled type set includes:

- \`SEC_REGISTRANT\`;
- \`CONSOLIDATED_PARENT\`;
- \`BANK_HOLDING_COMPANY\`;
- \`INSURED_DEPOSITORY_INSTITUTION\`;
- \`REGULATORY_REPORTER\`;
- \`REPORTABLE_SEGMENT\`;
- \`SERVICING_SUBSIDIARY\`; and
- \`DISCLOSED_OPERATING_UNIT\`.

One record can have multiple compatible roles only when evidence establishes that
the reporting boundary is the same. Similar names do not merge records.

## Reporting-scope dimensions

A scope records:

- reporting entity;
- legal consolidation boundary;
- business or segment boundary;
- portfolio population;
- rights-ownership basis;
- product or investor population when disclosed;
- geography and currency when relevant;
- included and excluded components;
- source label;
- valid-time interval; and
- source evidence.

Controlled Stage A scope categories include:

- \`CONSOLIDATED_COMPANY\`;
- \`SERVICING_SEGMENT\`;
- \`COMBINED_MORTGAGE_BANKING_SEGMENT\`;
- \`OWNED_MSR_PORTFOLIO\`;
- \`SERVICING_FOR_OTHERS\`;
- \`TOTAL_SERVICING_PORTFOLIO\`;
- \`SUBSERVICING_PORTFOLIO\`;
- \`BANK_OWNED_LOANS_SERVICED\`;
- \`INTERIM_SERVICING_PORTFOLIO\`;
- \`BANK_HOLDING_COMPANY_REGULATORY\`; and
- \`DEPOSITORY_INSTITUTION_REGULATORY\`.

\`COMBINED_MORTGAGE_BANKING_SEGMENT\` is retained as disclosed but is excluded
from a servicing-only comparison. Portfolio scopes are not assumed additive or
nested unless the issuer explicitly reconciles them.

## Stage A subjects

| Company | Class | Required identity and scope checks |
| --- | --- | --- |
| Truist Financial Corporation (TFC) | Bank | Verify SEC registrant and CIK; bank holding company and applicable RSSD; depository subsidiaries; fiscal calendar; disclosed mortgage/servicing presentation; FR Y-9C or Call Report reporter; accounting and segment regime for each selected quarter |
| PennyMac Financial Services, Inc. (PFSI) | Nonbank | Verify SEC registrant and CIK; traded security history; consolidated and disclosed servicing/mortgage-banking entities or segments; fiscal calendar; servicing portfolio populations; MSR accounting regime for each selected quarter |

The versioned universe configuration contains the verified identifiers and exact
source references. Documents and application code refer to stable IDs after
resolution. The selected observation periods are Q3 2025, Q4 2025, Q1 2026, and
Q2 2026 for each issuer's verified fiscal calendar.

Phase 2 records TFC's SEC registrant separately from the holding-company
regulatory reporter `tfc_bhc_regulatory_reporter` (RSSD 1074156) and the owned
depository reporter `truist_bank_regulatory_reporter` (RSSD 852320). Their scopes
are `tfc_bhc_regulatory` and `truist_bank_regulatory`. Neither is interchangeable
with `tfc_consolidated_residential_mortgage_servicing`; the comparability engine
returns `reporting scopes differ` before allowing arithmetic across those scopes.

## Fiscal-calendar model

A fiscal-calendar regime stores:

- reporting entity;
- regime version and effective dates;
- fiscal-year start convention;
- quarter mapping;
- 52/53-week or calendar-year policy when applicable;
- transition periods; and
- evidence.

Every observation stores actual period start and end dates, fiscal year, fiscal
quarter, and instant/duration classification. A label such as “Q1 2026” cannot
align two observations without the dates and regime.

## Accounting-policy model

An accounting-policy regime stores:

- reporting entity and applicable scope;
- policy type and version;
- effective dates;
- issuer description and evidence locator;
- GAAP, regulatory, non-GAAP reported, or platform-derived basis;
- MSR measurement method where applicable;
- relevant presentation, netting, sign, and classification rules; and
- supersession relationship.

MSR fair value, amortization, impairment, realization of expected cash flows,
assumption changes, and hedge results remain distinct. Servicing operations and
valuation effects are not collapsed into one profitability measure.

## Identifier rules

- CIKs are stored as normalized typed values while preserving the source form.
- RSSD, FDIC, LEI, accession, ticker, and security identifiers retain their
  namespace.
- Ticker lookup is an effective-dated convenience, not identity.
- Reuse or change of a ticker does not rewrite company history.
- Identifiers can be numeric without being sensitive data.
- Every identifier has valid-time and knowledge-time fields and source evidence.

## Relationship and corporate-action rules

Entity relationships record type, source and target, valid dates, knowledge dates,
ownership or consolidation attributes where disclosed, and evidence. Supported
relationships include:

- \`OWNS\`;
- \`CONTROLLED_BY\`;
- \`CONSOLIDATES\`;
- \`REPORTS_SEGMENT\`;
- \`REGULATED_AS\`;
- \`SERVICES_FOR\`;
- \`PREDECESSOR_OF\`;
- \`SUCCESSOR_OF\`; and
- \`RENAMED_TO\`.

A corporate action creates a potential time-series and comparability boundary.
The product does not bridge predecessor/successor values, portfolio transfers, or
scope changes without a versioned, evidence-backed continuity decision.

## Deterministic resolution

Resolution proceeds in this order:

1. Match a native typed identifier to an effective entity record.
2. Validate the filing or regulatory reporter against the source.
3. Resolve fiscal regime from report dates and context.
4. Resolve legal/business/portfolio scope from structured dimensions or explicit
   document headers and labels.
5. Resolve accounting-policy regime and methodology.
6. Compare the result with the metric definition's permitted scopes.
7. Publish only if the mapping is unique and supported.

Multiple possible entities, inherited scope from a nearby table, missing period
dates, or an unsupported crosswalk creates a quarantine candidate. A proposed
mapping requires evidence and deterministic validation before publication.

## Prohibited scope collapse

The following are never treated as equivalent without an explicit effective-dated
crosswalk:

- SEC registrant and bank holding company;
- bank holding company and depository institution;
- consolidated company and reportable segment;
- servicing segment and combined origination/mortgage-banking segment;
- total servicing and servicing for others;
- total servicing and owned-MSR UPB;
- servicing for others and subservicing;
- regulatory and SEC values; or
- legal entity and similarly branded subsidiary.
