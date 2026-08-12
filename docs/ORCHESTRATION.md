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
5. `extract_xbrl_facts`
6. `extract_bank_regulatory_facts`
7. `resolve_entity_and_scope`
8. `resolve_fiscal_period`
9. `map_metric`
10. `normalize_value_and_units`
11. `apply_effective_dated_rules`
12. `reconcile_and_validate`
13. `deduplicate_and_supersede`
14. `quarantine_ambiguous_candidates`
15. `request_human_review`
16. `publish_approved_observations`
17. `refresh_comparability_and_materializations`
18. `emit_audit_events`

The graph owns ordering, checkpoint-compatible state, interruption, and same-thread
resume. The recorded-data seeder and deterministic repository/domain services own
the Stage A values. The SEC client owns only governed acquisition. The bank
regulatory adapter is fail-closed until an approved endpoint implementation is
configured.

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

The live SEC client uses an explicit identifying User-Agent, official HTTPS SEC
hosts only, a minimum request interval, bounded retry with backoff, and a local
content cache. Non-SEC hosts and exhausted retries fail closed. The disabled bank
adapter raises a typed acquisition error rather than silently substituting an
issuer or source.

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
