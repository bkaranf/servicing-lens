# Orchestration

## Authority boundary

Deterministic application services own acquisition, hashing, parsing,
normalization, validation, reconciliation, publication, derivation, and pairwise
comparability. LangGraph coordinates those services but has no authority to
invent, approve, or alter a financial value.

LangChain exposes bounded read-only repository tools. Deep Agents may prepare a
public-data analyst draft only when its independent feature switch and model-call
switch are enabled. Neither framework receives borrower, customer, loan-level,
credential, or private servicing data. Neither receives arbitrary SQL, shell,
filesystem, network, mutation, or publication tools.

## Stage A ingestion graph

The compiled `public_servicing_ingestion_v1` graph has these explicit stages:

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

The graph owns ordering, checkpoint-compatible state, interruption, and same-thread
resume. The recorded-data seeder and deterministic repository/domain services own
the Stage A values. Phase 2 structured adapters run behind the same governed
acquisition/repository boundaries rather than adding placeholder graph nodes.
The graph therefore remains 16 substantive stages; XBRL and regulatory facts are
parsed and persisted within the applicable parse/map/reconcile services.

## State boundary

Graph state contains only bounded orchestration metadata:

- opaque run and thread identifiers;
- configured source keys;
- completed stage names;
- bounded candidate identifiers;
- `approve`, `reject`, or `pending` review disposition;
- published count; and
- bounded audit event strings.

Raw bytes, unbounded extracted text, credentials, prompts, unpublished financial
tables, customer identifiers, and model response bodies do not enter graph state
or checkpoints.

## Idempotency and failures

Database uniqueness covers evidence hashes, filing accessions, pipeline run keys,
and observation semantic/knowledge keys. The recorded Stage A seed operation is
idempotent: replay inserts no duplicate company, metric, evidence, filing, event,
or observation rows.

The supported opt-in acquisition path is `msi sync`, which requires
`EDGAR_IDENTITY` and uses public core `edgartools` interfaces behind the central
acquisition boundary. The legacy direct SEC client and bank-regulatory adapters
remain pre-cleanup implementation inventory only; they are outside the active
source policy and are not eligible for new product observations.

## Human review

`request_human_review` calls a real LangGraph `interrupt` only when quarantine
contains an ambiguous candidate. The interrupt payload contains candidate IDs and
the two permitted decisions. `resume_review` requires the original thread ID and
an `approve` or `reject` decision.

Approval does not directly edit a published observation. The CLI records a
`human_review_decisions` row, retains reviewer/rationale/thread metadata, and marks
the candidate `APPROVED_PENDING_REVALIDATION`. Rejection marks it `REJECTED`.
Publication remains a deterministic post-review stage.

## Model and tracing controls

The application renders, queries, compares, and serves Stage A with all model
features disabled. The independent controls are:

- `MSD_ENABLE_MODEL_CALLS=false`;
- `MSD_ENABLE_DEEP_AGENT=false`;
- `MSD_ENABLE_LANGGRAPH_PERSISTENCE=false`; and
- all LangChain/LangSmith remote-tracing variables false.

Remote tracing is checked immediately before any framework invocation. Provider
credentials are never part of `AppSettings`, CLI output, graph state, evidence,
observations, prompts, or logs.
