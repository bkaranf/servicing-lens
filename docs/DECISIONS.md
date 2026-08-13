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
| D-004 | ACCEPTED | Make the application independently installable from released dependencies |
| D-005 | ACCEPTED | Deterministic services and exact arithmetic are authoritative; models never publish financial values |
| D-006 | ACCEPTED | Preserve immutable evidence, reporting entity/scope, bitemporal history, revisions, and pairwise comparability |
| D-007 | ACCEPTED | Use controlled SEC/IR/regulatory boundaries and filed-evidence precedence; Stage A publishes only retained SEC exhibits |
| D-008 | ACCEPTED | Keep LangChain, LangGraph, and Deep Agents responsibilities and switches separate |
| D-009 | ACCEPTED | Use explicit public-document classifications and typed public identifiers |
| D-010 | ACCEPTED | Public routes are read-only; Stage A candidate review is an audited CLI boundary |
| D-011 | ACCEPTED | Default tests and CI are deterministic, offline, socket-blocked, strict, and branch-covered at 90% or more |
| D-012 | ACCEPTED | Do not start controlled expansion until Stage A and the intervening phase gates pass |
| D-013 | ACCEPTED | Extract the application from its former source repository into a standalone repository after Stage A |
| D-014 | DEFERRED | Select model/provider, production graph persistence, tracing, hosting, authentication, and production retention only through separate approval |
| D-015 | ACCEPTED | Stage A passed its closure audit on 2026-08-12; proceed to D-013 extraction before later pipeline phases |
| D-016 | ACCEPTED | Phase 2 live acquisition, structured adapters, regulatory scopes, and calendar passed its exit gate on 2026-08-12 |
| D-017 | ACCEPTED | Phase 3 TFC/PFSI profitability and expense metric deepening passed its exit gate on 2026-08-12 |
| D-018 | ACCEPTED | Make the hosted EdgarTools REST API the sole active external SEC-data provider, subject to an exact parity gate before legacy removal |

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

Do not use path or editable dependency overrides for application behavior.

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

The history-preserving extraction completed on 2026-08-12 after the Stage A exit
gate. The source was frozen at commit
\`6f32c801431dd626c3a95dcd54b137341e1efd9e\` with subtree tree
\`f9130db83bdae5acb592776cb6653c766b745263\`. A disposable, no-tag,
single-branch clone was filtered with pinned \`git-filter-repo==2.47.0\` using
\`--subdirectory-filter mortgage_servicing_dashboard\`. No source repository ref,
object, or remote was rewritten; the old public repository remains untouched.

The filtered Stage A tip is
\`ec758b6be35cd8363fcac7db94ba665d60b58b05\`. The mapping below preserves the
review lineage for every application-touching source commit:

| Source commit | Standalone commit |
| --- | --- |
| \`22ba71d6600bd857bc2004e3b882f78391e6a302\` | \`747a45cadd2e8506d652a6fcce2771151addedbb\` |
| \`b606160e15b5a3c68bdcb6dafb1248ce2a8f008a\` | pruned merge-only commit; content retained by its child |
| \`cd81fd585eb0f2b71e2956e85869b797c042cc0a\` | \`db14c6b36fb218c2c8e9dc830f7b5c59cd94085f\` |
| \`3090246c01f12bb67175c70eceb01a04aa2eec00\` | \`5bfd075229fc52b0f72d37e600307d47b0b458a1\` |
| \`2e81f9dc1ea19f7b3276072ee4c75c50c2d7979d\` | \`5c39d3f352105488175e699ba7c66c2e578b2c78\` |
| \`fedc0e98c7738a65c770a6788a3a65c823f72b6f\` | \`94a7094006e3757f31a95d0eb420b558512add42\` |
| \`09b6552e1d2ec94ce572d533b77d08f68b7b4c93\` | \`43f28eeb2ff673b73aba6cb2b326f8cea406df48\` |
| \`3139ad7c10b484e424af23744f9e5269de5bb4e4\` | \`7e1c5566321455d153bb528dcb106c3c3f815ac8\` |
| \`6f32c801431dd626c3a95dcd54b137341e1efd9e\` | \`ec758b6be35cd8363fcac7db94ba665d60b58b05\` |

Verification requires the released-only locked install, the complete quality
gate, a fresh clone with Windows automatic line-ending conversion enabled, exact
retained-evidence byte counts and SHA-256 values, and an empty search for former
repository build/tooling references. The standalone root recreates CI, scoped
Git attributes, ignore rules, licensing, and consolidated contributor instructions.

That verification passed on 2026-08-12 in a fresh GitHub clone at standalone
commit `74d23d5c90811962c49b4f3e7828d2054b4c06ac`: locked sync and lock check,
Ruff, Ruff format, strict Mypy, 84 socket-blocked tests at 91.33% branch
coverage, deterministic doctor, provenance parsing, and the Alembic
upgrade/check/downgrade/upgrade round trip all exited zero. Windows checkout
preserved the two D-015 byte counts and SHA-256 values exactly.

Rollback is non-destructive: discard the standalone clone or its new remote and
repeat from the frozen source commit. The source repository is never a rollback
target because extraction did not modify it.

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
their gaps are not converted to `NOT_DISCLOSED`. At the Stage A exit, the SEC
client was unwired and XBRL, FFIEC/FR Y-9C/NIC, and calendar behavior remained
Phase 2 work; D-016 records their later implementation.

The only remaining owner decisions are those already deferred by D-014:
deployment/hosting, authentication, production retention and persistence,
tracing, and any model-provider decision. D-012 is satisfied for Stage A; the
governing phased objective permits Phase 1 extraction now and permits universe
expansion only after Phases 2 and 3 pass their gates.

## D-016 — Phase 2 acquisition and structured-data exit

Phase 2 exited on 2026-08-12 after the standalone repository gate. Recorded
evidence remains the offline default and no normal test opens a socket. The exit
evidence is:

- `MSD_SEC_USER_AGENT` is optional at rest and mandatory for explicit live access;
  `msi discover --live` and `msi ingest --live` use official SEC HTTPS hosts,
  bounded recent-index discovery, serial rate limiting, validator-aware caching,
  bounded retry/backoff and response sizes, redirect rejection, and immutable
  content-addressed retention;
- a real live run acquired both configured Q2 2026 exhibits and published 36
  deterministically parsed observations with 36 original-response lineage links,
  zero live quarantines, and an idempotent replay with zero inserts;
- PFSI original bytes are 741,162 bytes with SHA-256
  `55bbe562574a015979ce480aa794d0a8ff7b09e05972d7adfd49b351dd932bd2`;
  TFC original bytes are 1,697,274 bytes with SHA-256
  `7a01b5c9ffc091aba54090357f372d971ea75bc42efde53e675260639ecf109e`;
- SEC company-facts and filing-level/inline-XBRL adapters retain exact raw text,
  Decimal scale/value, decimals, period, context, and dimensions; issuer mappings
  distinguish company-facts, filing-XBRL, and exhibit methodologies, and exact or
  semantic disagreement produces a quarantine decision with no preferred value;
- the concrete regulatory adapter parses FFIEC CDR tab rows and FR Y-9C caret
  rows by RSSD, with an effective NIC-style identity crosswalk. TFC's BHC reporter
  (RSSD 1074156) and Truist Bank (RSSD 852320) persist under distinct native
  scopes, and the comparability engine explicitly returns `reporting scopes differ`;
- `msi calendar` and `GET /api/v1/calendar` expose each issuer's actual Q2 2026
  Item 2.02/EX-99 filing separately from an inferred Q3 window. TFC's window is
  2026-10-16 through 2026-10-20; PFSI's is 2026-10-20 through 2026-10-27. Every
  inference lists its official SEC event IDs and can enter
  `AWAITING_EXPECTED_FILING`; it is never presented as issuer guidance; and
- migration `0002_phase2_structured_acquisition` explicitly adds structured fact,
  native regulatory scope, and calendar semantics and passes fresh upgrade,
  autogenerate check, downgrade-to-base, and re-upgrade.

The XBRL, regulatory, and normalized calendar fixtures are conspicuously marked
`SYNTHETIC_TEST_DATA` and are never public observations. Stage A missingness is
unchanged: zero `NOT_DISCLOSED` claims and 220 cells remain `SOURCE_NOT_CHECKED`.
The pre-existing deliberate Stage A ambiguity remains quarantined; deterministic
Phase 2 fixture tests additionally prove XBRL disagreement and live revision
conflicts route to quarantine.

The controlled live discovery currently reads bounded `filings.recent`; it does
not paginate older SEC submissions files. Regulatory adapters accept governed
bulk bytes but no scheduled daemon exists. These are explicit limitations, not
failed source checks. D-014 production/model decisions remain deferred. Phase 3
may now deepen TFC/PFSI metrics; issuer expansion remains blocked until Phase 3
passes.

## D-017 — Phase 3 profitability and expense metric-deepening exit

Phase 3 exited on 2026-08-12 after the final standalone-repository gate and an
independent semantic audit. It deepens TFC and PFSI only, for Q3 2025 through Q2
2026, and does not begin the controlled issuer expansion. The exit evidence is:

- a composed 53-metric catalog and 424-cell disclosure assessment, split into
  120 reported grid observations, 40 exact supporting observations, 43 governed
  derived observations, zero blocked derivations in the retained dataset, and
  222 `NOT_DISCLOSED` cells after complete eligible-source checks;
- 21 immutable `ORIGINAL_HTTP_RESPONSE` bodies totaling 64,382,045 bytes, each
  retained only at its SHA-256 path and verified against versioned TFC/PFSI
  manifests, with no friendly duplicate or unmanifested evidence body;
- exact `Decimal` calculations whose 43 outputs were independently recomputed
  from their published input revisions with no mismatch, including annualized
  cost-to-service, MSR fair-value multiples/basis points, portfolio mix, and
  UPB-weighted delinquency and foreclosure rates;
- ordered derived-input lineage, plural evidence links with source locators,
  controlled dimensions, explicit fiscal/accounting regimes, and complete
  semantic digests persisted by migration `0003_phase3_derived_lineage`;
- a populated-0002 migration exercise that backfills legacy observation regimes
  and semantic identity before enforcing non-null foreign keys, followed by an
  idempotent Phase 3 seed whose second run inserts nothing;
- bitemporal supersession of exact Stage A overlaps while retaining historical
  observations and evidence; 243 retained pairwise comparison assessments and
  no duplicate active company/metric/period cells in the Phase 3 dataset; and
- a deterministic TFC SEC/FR Y-9C reconciliation path whose fixture mismatch
  creates one audited `CROSS_SOURCE_NO_PREFERENCE` quarantine, publishes no raw
  synthetic regulatory fact, and does not prefer either reporting scope.

TFC publishes no servicing-population delinquency or cost-to-service values
because the complete retained source sets do not disclose compatible inputs.
Its annual and YTD MSR roll-forward rows remain source evidence and never become
standalone-quarter facts. PFSI's MSR-only related UPB remains distinct from its
broader owned-MSR-and-MSL delinquency population; component-only MSR sales are
not summed and mislabeled as reported. The issuer disclosure maps record every
reported, derived, and `NOT_DISCLOSED` cell and its exact source-check basis.

The final local gate passed locked sync, Ruff, Ruff format, strict Mypy, 227
socket-blocked tests with one explicit live-test skip, 90.05% branch coverage,
deterministic doctor, migration/provenance checks, and clean diff checks. The
current dashboard is not expanded for the new domains here because UI alignment
is explicitly Phase 5. D-014 production/model decisions remain deferred. D-012
is now satisfied through Phase 3, so Phase 4 may begin with the first governed
issuer only; later issuers and Phase 5 remain blocked by their preceding gates.

## D-018 — Hosted EdgarTools REST source boundary

The hosted REST API at `https://api.edgar.tools/v1` becomes the sole active
external provider for SEC filing discovery, filing metadata, document listing,
document retrieval, and structured financial data. One small typed `httpx`
client owns bearer authentication and permits only that HTTPS host and `/v1/`
path. SEC URLs returned by the provider remain provenance/display metadata and
are never fetched. Provider payloads and document bytes are labeled as
EdgarTools proxy evidence, not original SEC HTTP responses.

This decision supersedes D-007's direct SEC/IR/regulatory acquisition boundary
and the direct-SEC acquisition portion of D-016. It does not transfer numeric or
publication authority to the provider: deterministic local code continues to
own metric semantics, entity/scope/period resolution, exact `Decimal`
normalization, validation, reconciliation, revisions, persistence, publication,
API, and presentation. No LLM extraction fallback is permitted.

Legacy acquisition remains in place only during the non-destructive migration.
It may be removed only after a shadow run reproduces every currently published
TFC/PFSI observation with exact value, semantic, period, scope, revision, and
presentation parity and complete resolvable provenance. A tier, endpoint,
document, parser, or semantic gap blocks destructive cleanup and release; it
does not authorize a hidden fallback or a weaker missing-data state.
