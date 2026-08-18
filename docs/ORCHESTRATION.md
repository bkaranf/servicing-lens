# Deterministic ingestion and review

## Authority boundary

Deterministic application services own acquisition, hashing, parsing,
normalization, validation, reconciliation, publication, derivation, and pairwise
comparability. The runtime only sequences those services; it cannot invent,
approve, or alter a financial value.

## Current Phase 5 acquisition and replay

The current acquisition boundary is one centralized public-core `edgartools`
lane. `msi discover --live`, `msi ingest --live`, and `msi sync` require a valid
environment-held `EDGAR_IDENTITY`; missing or invalid identity fails before a
socket is opened. There is no custom SEC client or fallback provider. Local
discovery without `--live` reads the declarative registry and never contacts the
SEC.

For each selected filing, deterministic code discovers filing-specific facts or
document fields, resolves entity/scope/period semantics, preserves exact raw fact
text and context, validates the configured field, and persists the complete batch
atomically. First publication reports `PUBLISHED`; repeating the same verified
input reports `UNCHANGED`. A conflict in identity, scope, mapping, retained bytes,
or semantic metadata fails closed rather than mutating the existing record.

From a checkout, `msi ingest --phase5-cohort-b` replays the published five-bank,
five-nonbank cohort over the tracked Q3 2024 through Q2 2026 cases. Cohort A is the
explicit four-company subset. Replay uses hash-verified bounded derived excerpts
through the same parser and atomic persistence path as live acquisition, with
sockets disabled and an explicit isolated database/runtime root. A bare wheel has
the live runtime registries but not replay excerpts and returns
`phase5_replay_unavailable` without creating replay state.

Every published value retains CIK, accession, form, filing date, report period,
document name, SEC URL, exact locator, retrieval time, edgartools version, and
byte length/SHA-256 for the applicable representations. The additional supported
5+5 registry is evidence-vetted but not published and is not an acquisition or
replay selector.

## GET-only application boundary

The Phase 6 FastAPI factory opens an existing current-revision database without
migrating, seeding, or creating it. Every application and API route is GET-only.
Jinja2 templates, local vanilla JavaScript, inline SVG charts, accessible tables,
and bounded evidence views consume deterministic repository reads; no client or
HTTP route can publish, review, or mutate financial data.

## Historical staged review runtime

The completed Stage A compatibility workflow uses
`DeterministicIngestionRuntime`, which advances the exact 16 stages in this order:

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
workflow, tracing, or checkpoint framework dependency. Its Stage A values remain
in the deterministic services and repository functions. This retained workflow is
historical compatibility behavior, not the current Phase 5 acquisition path.

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

The Phase 5 path keys evidence and observations from immutable filing,
representation, mapping, and semantic identity and persists each prepared batch
transactionally. Verified reruns are order-independent and insert no duplicates.

The runtime creates the deterministic `PipelineRun` row at discovery, before
acquisition or parsing. Repeating the same source/configuration identity reuses
the same run key and does not duplicate repository records. A stage failure sets
the run to `FAILED`, writes a safe `IngestionError`, and always appends the audit
stage after the failed-stage prefix.

For backward compatibility, `msi ingest` without a mode still selects the direct,
idempotent `seed_stage_a` path; new callers use `--stage-a` explicitly. The
historical staged runtime owns its persisted review-resume flow and does not wrap
the already-atomic seed merely to simulate orchestration.

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

The public API remains GET-only. Candidate review is a local historical CLI
operation, not a public HTTP mutation route.
