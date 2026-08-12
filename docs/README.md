# Public Mortgage Servicing Intelligence documentation

Status: Stage A closed; standalone extraction and Phase 2 acquisition adapters complete

Baseline date: 2026-08-12

These documents define the first public-data vertical slice of **Public Mortgage
Servicing Intelligence**. The product compares selected publicly traded U.S.
mortgage servicers using public filings; the source policy also governs future
official regulatory evidence. It is
not a borrower-servicing application, an industry ranking, or investment advice.

The repository contains the safety foundation plus the Stage A persistence,
recorded-evidence ingestion, read API, dashboard, provenance, comparability, and
human-review slice. Phase 2 adds opt-in live SEC acquisition, XBRL and native-scope
regulatory adapters, raw-fact persistence, and official-source calendar/freshness
reads. The acceptance gates in the implementation plan define each release.

## Authoritative documents

| Document | Authority |
| --- | --- |
| [Product scope](PRODUCT_SCOPE.md) | Product identity, users, boundaries, Stage A selection, routes, and acceptance outcomes |
| [Source and evidence policy](SOURCE_AND_EVIDENCE_POLICY.md) | Eligible sources, acquisition controls, immutable evidence, and extraction authority |
| [Reporting entity and scope model](REPORTING_ENTITY_AND_SCOPE_MODEL.md) | Legal entities, identifiers, relationships, fiscal regimes, accounting regimes, and reporting scopes |
| [Metric catalog](METRIC_CATALOG.md) | Versioned servicing metrics, semantics, evidence, reconciliation, and prohibited interpretations |
| [Data model](DATA_MODEL.md) | Persistent records, exact numeric rules, observation states, bitemporal history, and revisions |
| [Orchestration](ORCHESTRATION.md) | Deterministic ingestion graph, review flow, LangChain tools, and Deep Agents boundary |
| [Comparability policy](COMPARABILITY_POLICY.md) | Pairwise assessment dimensions, verdicts, reasons, and calculation restrictions |
| [Implementation plan](IMPLEMENTATION_PLAN.md) | Depth-first work sequence, tests, quality gates, and Stage A exit criteria |
| [Decisions](DECISIONS.md) | Binding architecture and product decisions plus deferred owner choices |

When documents conflict, the more specific authoritative document governs. A
material change to product scope, source eligibility, metric semantics,
comparability, publication authority, or agent capabilities requires a recorded
decision and matching tests.

## Archived predecessor

The former synthetic internal loan-operations foundation is preserved unchanged
under
[docs/archive/internal_servicing_operations_foundation/](archive/internal_servicing_operations_foundation/ARCHIVE_MANIFEST.md).
It is historical context only and is not authoritative for this product.

## Non-negotiable contract

1. Deterministic code, not a model, owns financial values and calculations.
2. Retained public evidence is immutable, content-addressed, and truthfully
   labeled by representation and capture method; recorded browser DOM is not
   called an original HTTP response.
3. Every displayed value carries observation and evidence identities plus a
   resolvable source locator.
4. Money, balances, rates, and derived values use exact \`Decimal\`/database
   \`NUMERIC\`, never binary floating point.
5. Missing disclosure remains missing and never becomes zero or an estimate.
6. Reporting entity, scope, period, accounting policy, and methodology are part
   of a value's meaning.
7. Ambiguity fails closed into quarantine or controlled human review.
8. History is superseded, never destroyed.
9. LangChain, LangGraph, and Deep Agents have separate responsibilities and
   independent fail-closed switches.
10. Default tests are deterministic and network-free.
