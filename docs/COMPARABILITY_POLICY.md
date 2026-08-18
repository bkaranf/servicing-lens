# Comparability policy

## Principle

Comparability is a relationship between two exact observation revisions under a
versioned policy. It is not a universal property stored on one observation.
Matching display names or metric IDs does not make values comparable.

The deterministic comparability service owns every verdict. Explanations are
versioned reason codes and cannot create or override an assessment.

## Verdicts

| Verdict | Meaning | Calculation behavior |
| --- | --- | --- |
| \`COMPARABLE\` | All required semantics align without a material caveat | Permitted metric calculations may run |
| \`COMPARABLE_WITH_CAVEATS\` | A versioned rule permits comparison but material qualifications remain | Permitted calculations run only with caveats attached to every result |
| \`NOT_COMPARABLE\` | A known semantic incompatibility cannot be bridged | No difference, percentage change, connected trend, or ranking |
| \`INSUFFICIENT_INFORMATION\` | Evidence or semantics are missing, ambiguous, conflicted, quarantined, or not yet reviewed | No comparison calculation |

Verdicts are not “better” or “worse” quality scores. They answer whether a specific
analytical operation is supportable.

## Required dimensions

The service evaluates, in stable order:

1. **Publication and observation state** — both exact revisions are published;
   reported/preliminary/pro-forma/announced/derived state is compatible with the
   requested operation.
2. **Metric definition** — metric IDs and semantic versions match or a tested,
   effective compatibility rule exists.
3. **Reporting entity** — legal and reporting boundaries are the same or the
   requested cross-company operation permits distinct but equivalent roles.
4. **Reporting scope** — business, segment, rights-ownership, and portfolio
   populations align.
5. **Period and duration** — instant dates or duration intervals, period length,
   fiscal quarter/year, and standalone-quarter/YTD/year classification align.
6. **Fiscal-calendar regime** — dates and issuer calendars support the requested
   alignment.
7. **Currency, unit, and scale** — exact unit/currency are compatible and scale
   normalization is lossless.
8. **Methodology** — reported/derived method, numerator, denominator, day count,
   sign, netting, allocations, and population rules align.
9. **Accounting-policy regime** — measurement and presentation policies are
   compatible for this metric.
10. **Portfolio population** — included/excluded loans, ownership, agency/product
    mix, delinquency convention, and other controlled dimensions align.
11. **Corporate actions** — no unbridged acquisition, divestiture, material sale,
    rename/scope transformation, or discontinuity invalidates the requested
    relationship.
12. **Source precision and quality** — reported decimals, rounding, extraction,
    validation, reconciliation, revisions, and known exclusions support the
    operation.

All applicable reasons are returned; evaluation does not stop after the first
failure.

## Stable reason codes

The initial reason vocabulary includes:

- \`OBSERVATION_NOT_PUBLISHED\`;
- \`VALUE_NOT_AVAILABLE\`;
- \`PRELIMINARY_VS_ACTUAL\`;
- \`PRO_FORMA_VS_ACTUAL\`;
- \`DERIVED_VS_REPORTED\`;
- \`METRIC_MISMATCH\`;
- \`METRIC_VERSION_MISMATCH\`;
- \`REPORTING_ENTITY_MISMATCH\`;
- \`REPORTING_SCOPE_MISMATCH\`;
- \`COMBINED_SEGMENT\`;
- \`PORTFOLIO_POPULATION_MISMATCH\`;
- \`INSTANT_DATE_MISMATCH\`;
- \`PERIOD_TYPE_MISMATCH\`;
- \`FLOW_INTERVAL_MISMATCH\`;
- \`FISCAL_CALENDAR_MISMATCH\`;
- \`CURRENCY_MISMATCH\`;
- \`UNIT_MISMATCH\`;
- \`METHODOLOGY_MISMATCH\`;
- \`ACCOUNTING_POLICY_MISMATCH\`;
- \`CORPORATE_ACTION_BOUNDARY\`;
- \`SOURCE_PRECISION_CAVEAT\`;
- \`KNOWN_EXCLUSION_CAVEAT\`;
- \`EVIDENCE_INCOMPLETE\`;
- \`RECONCILIATION_FAILED\`; and
- \`REVIEW_PENDING\`.

Each reason includes a safe explanation, affected dimension, observation IDs, and
policy-rule version. Free-form prose is not a reason code.

## Portfolio-metric preference

Default cross-company portfolio comparison prefers:

1. \`servicing_for_others_upb\`; then
2. \`owned_msr_upb\`.

The product does not fall back to \`total_servicing_upb\` automatically. If a
preferred metric is not disclosed, the cell remains not disclosed. A user can
explicitly select total servicing, but its pairwise assessment must evaluate each
issuer's source-defined total population.

No portfolio metric substitutes for another merely to populate a chart.

## Cross-company issuer selection

The public comparison surface accepts two or three ordered, distinct issuer IDs
from active companies that already have published observations. The default pair
remains TFC/PFSI for compatibility. A three-issuer selection expands deterministically
into the three ordered pairwise combinations; every result remains a governed
`ComparisonRecord` evaluated independently by `assess_comparability`.

Every selected pair uses the same validation and strict semantic policy below;
bank/bank, nonbank/nonbank, and mixed pairs receive no class-specific shortcut.

## Cross-company rules

Selected issuers are compared as different corporate subjects. A comparison can still
be \`COMPARABLE\` when both values use the same metric version and equivalent
portfolio/reporting roles. The following remain distinct:

- a bank holding-company regulatory fact and a nonbank SEC consolidated fact;
- a depository-institution fact and an issuer servicing-segment fact;
- a combined mortgage-banking segment and a servicing-only segment;
- servicing for others, owned-MSR, total, interim, and subservicing populations;
  and
- a reported issuer measure and a platform-derived measure.

If scope equivalence cannot be demonstrated, the result is
\`NOT_COMPARABLE\` or \`INSUFFICIENT_INFORMATION\`; the service does not choose the
more convenient scope.

Phase 3 cross-source rules may compare a specifically allowed SEC/regulatory
scope pair only to test an exact economic reconciliation. Matching values validate
the rule; any value or semantic mismatch quarantines the reconciliation. The rule
never promotes a regulatory fact over an SEC fact, or vice versa, and it does not
make the two reporting scopes generally interchangeable for company comparison.

## Time-series rules

- Instant observations compare at verified period ends. A calendar label alone is
  insufficient.
- Duration observations compare over equivalent intervals and period types.
- YTD, annual, and standalone-quarter values remain distinct.
- Stage A does not derive a quarter from YTD values unless a specific metric
  definition, compatible current/prior YTD inputs, and deterministic reconciliation
  explicitly authorize it.
- A gap remains a gap; trend lines do not connect through not disclosed,
  quarantined, or not-comparable points.
- A metric-definition, fiscal-regime, accounting-policy, reporting-scope, or
  corporate-action change creates a visible boundary unless a tested compatibility
  rule bridges it.

## Derived values

A derived observation can be compared only when:

- the same formula and input metric versions apply;
- every input revision is published and compatible;
- periods, scopes, currency, units, and methodologies align;
- exact calculations reproduce; and
- the output remains labeled \`DERIVED\`.

Reported versus derived normally returns \`COMPARABLE_WITH_CAVEATS\` only when the
metric definition explicitly permits that relationship. Otherwise it is
\`NOT_COMPARABLE\`.

## Missing and ambiguous values

\`NOT_DISCLOSED\` is not zero and cannot participate in arithmetic. Two not
disclosed cells do not become comparable numeric observations.

Ambiguous, conflicting, quarantined, or source-not-checked values yield
\`INSUFFICIENT_INFORMATION\`. A later review or evidence revision creates a new
assessment; it does not rewrite the earlier as-known-at result.

## Persistence and invalidation

\`comparability_assessments\` stores:

- assessment ID and policy version;
- exact left/right observation revision IDs;
- requested analytical operation;
- verdict;
- ordered reason codes and caveats;
- permitted calculation set;
- valid/knowledge time; and
- creation pipeline run.

Superseding either observation, metric definition, scope/regime mapping, or policy
invalidates the current assessment and creates a new revision on recomputation.

## API and UI contract

Every cross-company pair and every connected trend segment carries a verdict. The
API returns input observation IDs, assessment ID, verdict, reasons, caveats, and
exact permitted calculations.

The UI:

- shows verdict text and reasons adjacent to values;
- never relies on color alone;
- disables unsupported arithmetic rather than displaying blanks that resemble
  zero;
- carries qualifications into tables, tooltips, evidence drawers, and exports;
  and
- provides the same verdict in chart alternatives and accessible tables.
