---
name: financial-validation
description: Validate exact financial observations, periods, reconciliations, lineage, and comparability in the Servicing Lens data model.
---

# Financial validation

Use this skill when a metric, parser, derived value, or observation revision needs correctness review.

- Trace the value through `src/mortgage_servicing_dashboard/domain.py`,
  `metric_engine.py`, `phase3.py`, `repository.py`, and the Alembic versions;
  use `rg -n 'Decimal|NUMERIC|NOT_DISCLOSED|QUARANTIN'` to find the existing
  invariants. Authoritative money, balances, rates, and UPB use `Decimal` in
  Python and SQL `NUMERIC` in persistence.
- Validate instant versus duration, fiscal quarter versus annual/YTD period,
  units, scales, currency, methodology, and reporting scope before comparing or
  deriving. Keep duplicate facts and amendments as evidence to resolve, not as
  interchangeable inputs.
- Derive Q4 only as exact annual-minus-nine-month values when concept, entity,
  scope, unit, scale, methodology, and matching accounting periods all agree;
  retain every input observation ID. Otherwise leave the cell `NOT_DISCLOSED`.
- Reconcile with zero tolerance using `Decimal`; distinguish an explicit measured
  zero from missing disclosure, quarantine ambiguous or conflicting candidates,
  and reject blends across parent, subsidiary, segment, predecessor, successor,
  or unlike portfolio populations.
- Run focused coverage in `tests/unit_tests/test_metric_engine.py`,
  `test_phase3_lineage.py`, `test_phase3_persistence.py`, and the relevant
  disclosure tests. Confirm formula version, input lineage, supersession, and
  API serialization before accepting a changed observation.
