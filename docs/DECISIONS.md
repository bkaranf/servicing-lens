# Decisions

Statuses are \`ACCEPTED\`, \`SUPERSEDED\`, \`REJECTED\`, or \`DEFERRED\`.
Accepted decisions bind Stage A until a later entry explicitly supersedes them.
Acceptance here is an implementation decision, not production, accounting,
investment, legal, security, or regulatory approval.

## Decision summary

| ID | Status | Decision |
| --- | --- | --- |
| D-001 | ACCEPTED | Reset the product from synthetic internal loan operations to public mortgage-servicing intelligence and archive the predecessor unchanged |
| D-002 | ACCEPTED | Stage A covers TFC and PFSI for Q3 2025 through Q2 2026 only |
| D-003 | ACCEPTED | Use FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL, server-rendered Jinja2/HTMX, and locally hosted chart assets |
| D-004 | ACCEPTED | Make the application independently installable from released dependencies; do not modify upstream \`libs/\` |
| D-005 | ACCEPTED | Deterministic services and exact arithmetic are authoritative; models never publish financial values |
| D-006 | ACCEPTED | Preserve immutable evidence, reporting entity/scope, bitemporal history, revisions, and pairwise comparability |
| D-007 | ACCEPTED | Use controlled SEC/IR/regulatory adapters and filed-evidence precedence |
| D-008 | ACCEPTED | Keep LangChain, LangGraph, and Deep Agents responsibilities and switches separate |
| D-009 | ACCEPTED | Use explicit public-document classifications and typed public identifiers |
| D-010 | ACCEPTED | Public routes are read-only; Stage A candidate review is an audited CLI boundary |
| D-011 | ACCEPTED | Default tests and CI are deterministic, offline, socket-blocked, strict, and branch-covered at 90% or more |
| D-012 | ACCEPTED | Do not start Stage B until all Stage A acceptance gates pass |
| D-013 | DEFERRED | Extract the application from the LangChain monorepo into a standalone repository after Stage A |
| D-014 | DEFERRED | Select model/provider, production graph persistence, tracing, hosting, authentication, and production retention only through separate approval |

## D-001 — Public-product scope reset

The committed predecessor described an internal operational dashboard over
synthetic borrower/loan records. That scope is superseded. Its documents remain
byte-preserved under
\`docs/archive/internal_servicing_operations_foundation/\` and are historical only.

The authoritative product uses public SEC filings, filed earnings materials,
limited issuer IR material under the source policy, and official regulatory data.
No borrower, customer, loan-level servicing-system, payment, account,
authentication, credential, or employer-internal data belongs in the product.

Useful predecessor principles are reaffirmed:

- deterministic before generative;
- immutable raw evidence and complete lineage;
- exact \`Decimal\`/database \`NUMERIC\`;
- fail closed on missing or ambiguous semantics;
- no model numeric authority;
- no silent guessing or scope collapse;
- no history destruction;
- separate LangChain, LangGraph, and Deep Agents responsibilities;
- independent kill switches; and
- network-free deterministic tests.

## D-002 — Stage A selection

Stage A implements one verified bank and one verified nonbank:

- Truist Financial Corporation (TFC); and
- PennyMac Financial Services, Inc. (PFSI).

The selected fiscal periods are Q3 2025, Q4 2025, Q1 2026, and Q2 2026 under each
issuer's verified fiscal calendar. Runtime identities, CIKs, regimes, scopes, and
sources live in versioned configuration backed by official evidence.

At least five useful metrics must complete the source-to-UI path. The full initial
catalog is defined even when an issuer does not disclose a metric. No third issuer
or broader period is added before Stage A exits.

## D-003 — Application stack

Use:

- FastAPI and Pydantic for typed HTTP boundaries;
- SQLAlchemy 2 and Alembic;
- PostgreSQL with exact \`NUMERIC\`;
- server-rendered Jinja2 and HTMX;
- a locally hosted chart library; and
- a replaceable immutable evidence-retention interface.

No external CDN asset is permitted. Charts have accessible table equivalents. The
first UI does not introduce React/Vite or another independent frontend runtime.
A later alternative requires an accepted decision demonstrating stronger
repository-compatible value.

## D-004 — Self-containment and upstream boundary

Remove editable local overrides for \`langchain\` and \`langchain-core\`. Resolve
compatible released packages with \`uv\` and deliberately update the lockfile.
\`mortgage_servicing_dashboard/\` must install independently from its own
\`pyproject.toml\` and \`uv.lock\`.

Do not edit, delete, or rewrite upstream LangChain \`libs/\` for application
behavior.

## D-005 — Financial and publication authority

Pure deterministic services own parsing, exact normalization, formulas,
validation, reconciliation, revision identity, and comparability. LLM output can
only propose a quarantined candidate or explain approved tool results.

Missing disclosure is not zero. A model cannot estimate, choose a conflict,
approve a candidate, publish an observation, or decide comparability.

## D-006 — Evidence, semantics, and history

Original public bytes are immutable and content-addressed. Every published value
has evidence/observation IDs and an exact locator. Entity, scope, period,
instant/duration, methodology, accounting regime, unit, scale, precision, valid
time, and knowledge time are first-class semantics.

Corrections and later knowledge create superseding revisions. They never destroy
evidence or rewrite what the system previously knew. Comparability is stored
pairwise against exact observation revisions and a policy version.

## D-007 — Source boundaries

SEC access occurs only through the controlled adapter under fair-access behavior.
Filed materials take precedence over unfiled issuer copies. FFIEC Call Report,
FR Y-9C, and NIC records remain attached to their actual regulatory reporting
entities.

Search snippets, screenshots, aggregators, transcripts, and model recollection are
not evidence for financial values.

## D-008 — Framework responsibilities

LangGraph coordinates 16 small typed ingestion stages. Raw documents do not enter
state. Persistence is independently disabled unless a backend is injected and
configured.

LangChain exposes only typed bounded read tools over application services. Deep
Agents are optional, independently disabled, analyst-initiated, and limited to the
same tools. Neither framework is required for the dashboard or API to function.

## D-009 — Public-document classification

The controlled classifications are:

- \`PUBLIC_CORPORATE_DOCUMENT\`;
- \`PUBLIC_STRUCTURED_FACT\`;
- \`SYNTHETIC_TEST_DATA\`;
- \`RESTRICTED_INTERNAL_DATA\`; and
- \`PROHIBITED_CUSTOMER_DATA\`.

Typed public CIKs, accession IDs, RSSDs, tickers, and hashes are structured
metadata rather than arbitrary prompt prose. Restricted/prohibited data is rejected.
Corporate contact blocks are removed from bounded model excerpts.

## D-010 — Read and review boundaries

Public HTTP routes are GET-only analytical reads. Stage A uses an audited CLI for
candidate review. Review records identity, role, evidence snapshot, decision,
reason, thread/run, and resulting revision/config version.

There is no direct database edit, generic admin SQL, model approval, or public
candidate mutation route.

## D-011 — Quality policy

Normal CI is network-free and socket-blocked using deterministic public/recorded
or synthetic fixtures. Live source smoke tests are opt-in. Ruff, Ruff format,
strict Mypy, and at least 90% branch coverage remain mandatory.

Dependency, secret, migration, schema-contract, generated-artifact, route/tool
inventory, provenance, accessibility, and restricted-data checks are release
gates.

## D-012 — Stage B hold

Stage B targets at least ten issuers and eight quarters where evidence permits,
but is not authorized until Stage A passes provenance, reconciliation,
reproducibility, quarantine/review, comparability, model-disabled operation,
accessibility, and full-quality gates. Candidate identities are reverified live
before expansion.

## D-013 — Standalone repository follow-up

The application remains inside the LangChain monorepo for Stage A to avoid
rewriting or deleting upstream history in this task. After Stage A, create a
separate owner decision and migration plan to extract
\`mortgage_servicing_dashboard/\` into a standalone repository.

The follow-up must preserve Git history, dependency locks, evidence/fixture
provenance, CI controls, issue/PR traceability, release artifacts, and documented
rollback. It must not be combined with Stage A product behavior.

## D-014 — Production and model choices deferred

Stage A requires no live model provider, Deep Agent, remote tracing, production
checkpointer, production hosting, or public authentication provider. Those choices
affect security, privacy, retention, region, cost, support, and governance and
require separate accepted decisions.

This deferral does not block deterministic ingestion, the controlled review CLI,
the read API, or the dashboard.
