# Servicing Lens documentation

Use the [repository README](../README.md) for product scope, setup, CLI usage,
and the current governed-universe summary. Use the root [AGENTS.md](../AGENTS.md)
for the short working rules that apply to every change.

When guidance conflicts, `AGENTS.md` governs agent work and the narrowest
applicable governing document below owns its domain.

## Governing documents

| Document | Use for |
| --- | --- |
| [Source and evidence policy](SOURCE_AND_EVIDENCE_POLICY.md) | SEC source eligibility, `edgartools` acquisition, identity, rate limits, and evidence retention |
| [Reporting entity and scope model](REPORTING_ENTITY_AND_SCOPE_MODEL.md) | Legal entities, CIKs, reporting scopes, periods, and corporate relationships |
| [Data model](DATA_MODEL.md) | Exact values, observation states, revisions, lineage, and persistence |
| [Metric catalog](METRIC_CATALOG.md) | Metric meanings, evidence requirements, and reconciliation boundaries |
| [Comparability policy](COMPARABILITY_POLICY.md) | Pairwise comparability decisions and calculation restrictions |
| [Orchestration](ORCHESTRATION.md) | Current implementation inventory; source eligibility remains owned by the source policy |
| [Decisions](DECISIONS.md) | Binding architecture and product decisions |
| [Quality guide](../QUALITY_GUIDE.md) | Repository quality gate and release checks |

Historical plans and migration reports remain available for audit context but
are not active implementation guidance: [implementation plan](IMPLEMENTATION_PLAN.md)
and [hosted EdgarTools migration report](EDGAR_TOOLS_MIGRATION.md).

The former synthetic internal loan-operations documentation is preserved as
historical context under
[docs/archive/internal_servicing_operations_foundation](archive/internal_servicing_operations_foundation/ARCHIVE_MANIFEST.md).
It is not authoritative for Servicing Lens.
