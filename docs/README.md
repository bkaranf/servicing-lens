# Servicing Lens documentation

Use the [repository README](../README.md) for product scope, setup, CLI usage,
and the current governed-universe summary. Use the root [AGENTS.md](../AGENTS.md)
for the short working rules that apply to every change.

When guidance conflicts, `AGENTS.md` governs agent work and the narrowest
applicable governing document below owns its domain.

The current implementation boundary is the Phase 5 published five-bank/five-
nonbank cohort, its deterministic checkout replay and public-edgartools live lane,
and the Phase 6 GET-only application. The separate supported-universe registry
adds five banks and five nonbanks as evidence-vetted, not-published candidates;
it is not a ranking or a coverage-completeness claim.

## Governing documents

| Document | Use for |
| --- | --- |
| [Product scope](PRODUCT_SCOPE.md) | Current published and supported registries, user outcomes, read surfaces, and explicit exclusions |
| [Source and evidence policy](SOURCE_AND_EVIDENCE_POLICY.md) | SEC source eligibility, `edgartools` acquisition, identity, rate limits, and evidence retention |
| [Reporting entity and scope model](REPORTING_ENTITY_AND_SCOPE_MODEL.md) | Legal entities, CIKs, reporting scopes, periods, and corporate relationships |
| [Data model](DATA_MODEL.md) | Exact values, observation states, revisions, lineage, and persistence |
| [Metric catalog](METRIC_CATALOG.md) | Metric meanings, evidence requirements, and reconciliation boundaries |
| [Comparability policy](COMPARABILITY_POLICY.md) | Pairwise comparability decisions and calculation restrictions |
| [Orchestration](ORCHESTRATION.md) | Current Phase 5 live/replay sequencing, GET-only application boundary, and historical review runtime |
| [Decisions](DECISIONS.md) | Binding architecture and product decisions |
| [Quality guide](../QUALITY_GUIDE.md) | Repository quality gate and release checks |

Historical plans and migration reports remain available for audit context but
are not active implementation guidance: [implementation plan](IMPLEMENTATION_PLAN.md)
and [hosted EdgarTools migration report](EDGAR_TOOLS_MIGRATION.md).

The completed [TFC](../proposals/disclosure_map/TFC.md) and
[PFSI](../proposals/disclosure_map/PFSI.md) Phase 3 disclosure maps are retained
audit records. They do not define the current Phase 5 field registry or expand the
published cohort.

The former synthetic internal loan-operations documentation is preserved as
historical context under
[docs/archive/internal_servicing_operations_foundation](archive/internal_servicing_operations_foundation/ARCHIVE_MANIFEST.md).
It is not authoritative for Servicing Lens.
