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
| D-003 | ACCEPTED | Use FastAPI, Pydantic, SQLAlchemy 2, Alembic, local SQLite with PostgreSQL-compatible schemas, server-rendered Jinja2/HTMX, and locally hosted chart assets |
| D-004 | ACCEPTED | Make the application independently installable from released dependencies; do not modify upstream \`libs/\` |
| D-005 | ACCEPTED | Deterministic services and exact arithmetic are authoritative; models never publish financial values |
| D-006 | ACCEPTED | Preserve immutable evidence, reporting entity/scope, bitemporal history, revisions, and pairwise comparability |
| D-007 | ACCEPTED | Use controlled SEC/IR/regulatory boundaries and filed-evidence precedence; Stage A publishes only retained SEC exhibits |
| D-008 | ACCEPTED | Keep LangChain, LangGraph, and Deep Agents responsibilities and switches separate |
| D-009 | ACCEPTED | Use explicit public-document classifications and typed public identifiers |
| D-010 | ACCEPTED | Public routes are read-only; Stage A candidate review is an audited CLI boundary |
| D-011 | ACCEPTED | Default tests and CI are deterministic, offline, socket-blocked, strict, and branch-covered at 90% or more |
| D-012 | ACCEPTED | Do not start controlled expansion until Stage A and the intervening phase gates pass |
| D-013 | ACCEPTED | Extract the application from the LangChain monorepo into a standalone repository after Stage A |
| D-014 | DEFERRED | Select model/provider, production graph persistence, tracing, hosting, authentication, and production retention only through separate approval |
| D-015 | ACCEPTED | Stage A passed its closure audit on 2026-08-12; proceed to D-013 extraction before later pipeline phases |

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
- SQLite as the default local engine and PostgreSQL-compatible exact \`NUMERIC\`;
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

## D-012 — Controlled-expansion hold

The earlier broad target of ten issuers and eight quarters is superseded by the
governing 2026-08-12 objective. Controlled expansion adds exactly two banks and
two nonbanks over Q3 2025 through Q2 2026, one issuer at a time, only after Stage
A, standalone extraction, acquisition-adapter work, and TFC/PFSI metric deepening
pass their gates. Candidate identities are reverified from official sources
before expansion.

## D-013 — Standalone repository extraction

The application remains inside the LangChain monorepo through Stage A to avoid
rewriting or deleting upstream history. The governing 2026-08-12 objective now
authorizes history-preserving extraction of \`mortgage_servicing_dashboard/\`
immediately after the Stage A exit gate.

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

## D-015 — Stage A exit

Stage A exited on 2026-08-12 after the complete local gate and independent
closure audits passed together. The exit evidence is:

- 36 observations parsed from two hash-verified retained SEC DOM
  serializations, with no configured authoritative numeric observation;
- 220 catalog cells retained as `SOURCE_NOT_CHECKED`, zero as
  `NOT_DISCLOSED`, and one deliberate ambiguous candidate retained in quarantine;
- 16 substantive ingestion nodes with content/config/parser-derived idempotent
  run keys, explicit terminal states, bounded retry, fail-closed errors, and
  same-thread CLI approve/reject through deterministic revalidation;
- four retained pairwise comparison assessments, including a Q2 2026
  `total_servicing_upb` `not_comparable` assessment with no permitted arithmetic;
- explicit migration 0001 operations with matching 27-table ORM/migration
  contracts and no metadata-wide create/drop call; and
- locked sync, Ruff, Ruff format, strict Mypy, socket-blocked Pytest above 90%
  branch coverage, deterministic doctor, and clean diff checks.

The TFC retained bytes are 1,697,426 bytes with SHA-256
`7353334b2f40cb48d0ed6dc6756378e93260d2e2b6541ea37d800790057a7883`.
The PFSI retained bytes are 741,531 bytes with SHA-256
`db128f08fa4fff4835e13467e6dc18f081983b64618ada3e6a7ee7097ade78cf`.

This exit does not claim complete catalog coverage. The linked quarterly
periodic filings remain unretained and unchecked at the catalog-cell level, so
their gaps are not converted to `NOT_DISCLOSED`. The SEC client remains unwired,
and XBRL, FFIEC/FR Y-9C/NIC, and calendar behavior remain Phase 2 work.

The only remaining owner decisions are those already deferred by D-014:
deployment/hosting, authentication, production retention and persistence,
tracing, and any model-provider decision. D-012 is satisfied for Stage A; the
governing phased objective permits Phase 1 extraction now and permits universe
expansion only after Phases 2 and 3 pass their gates.
