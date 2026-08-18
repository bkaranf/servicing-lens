# Deterministic ingestion and review

## Authority boundary

Deterministic application services own acquisition, hashing, parsing,
normalization, validation, reconciliation, publication, derivation, and pairwise
comparability. The runtime only sequences those services; it cannot invent,
approve, or alter a financial value.

## Stage A runtime

`DeterministicIngestionRuntime` advances the exact 16 stages in this order:

1. `discover_sources`
2. `acquire_source`
3. `hash_and_store`
4. `parse_document`
5. `resolve_entity_and_scope`
6. `resolve_fiscal_period`
7. `map_metric`
8. `normalize_value_and_units`
9. `apply_effective_dated_rules`
10. `reconcile_and_validate`
11. `deduplicate_and_supersede`
12. `quarantine_ambiguous_candidates`
13. `request_human_review`
14. `publish_approved_observations`
15. `refresh_comparability_and_materializations`
16. `emit_audit_events`

The runtime is a small typed Python state transition loop. It has no model,
workflow, tracing, or checkpoint framework dependency. Stage A values remain in
the existing deterministic services and repository functions.

## State boundary

State contains only bounded metadata:

- opaque run and thread identifiers;
- configured source keys;
- completed stage names;
- bounded candidate and evidence identifiers;
- `approve`, `reject`, or `pending` review disposition;
- published, missing, source-not-checked, quarantined, and failed counts; and
- bounded audit event and error-code strings.

Raw bytes, unbounded extracted text, credentials, unpublished financial tables,
and external response bodies never enter runtime state or persistence metadata.

## Idempotency, failures, and replay

The runtime creates the deterministic `PipelineRun` row at discovery, before
acquisition or parsing. Repeating the same source/configuration identity reuses
the same run key and does not duplicate repository records. A stage failure sets
the run to `FAILED`, writes a safe `IngestionError`, and always appends the audit
stage after the failed-stage prefix.

The ordinary offline `msi ingest` command intentionally retains its direct,
idempotent `seed_stage_a` path. The explicit runtime owns staged execution and
the persisted review-resume flow; it does not wrap the already-atomic seed merely
to simulate orchestration.

The run pauses at `AWAITING_REVIEW` when quarantine contains candidates. The CLI
creates a fresh runtime process and reconstructs the run from its persisted
candidate and original thread; it does not depend on an in-memory checkpoint.
Resuming with another thread is rejected before any review or publication write.

## Human review and publication

`request_human_review` persists reviewer, rationale, decision, and thread in
`human_review_decisions`. Repeating the same decision is idempotent. Approval
sets the candidate to `APPROVED_PENDING_REVALIDATION`; rejection sets it to
`REJECTED`.

Before any publication call, approved candidates are replayed against the
freshly retained evidence, exact row text, Decimal normalization, and current
mapping. Ambiguous or changed candidates remain unpublished and are marked
`QUARANTINED_AFTER_REVALIDATION`. Public observations are written only by the
existing deterministic repository publication function after this gate.

The public API remains read-only. Candidate review is a local CLI operation, not
a public HTTP mutation route.
