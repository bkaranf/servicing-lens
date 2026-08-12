# Quality and integration guide

This guide applies to the repository root. The normal gate is
deterministic, network-free, socket-blocked, credential-free, and fail-closed.
Live source smoke tests are opt-in and never required for a normal local or CI
pass.

Stage A is not complete because a parser returns a number or a page renders. The
gate covers the full evidence-to-UI path for TFC/PFSI, Q3 2025 through Q2 2026.

## Core local gate

Run from this directory:

\`\`\`bash
uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest --cov=mortgage_servicing_dashboard --cov-report=term-missing
uv run msi doctor --json
\`\`\`

The locked sync must not change \`uv.lock\`. Ruff keeps \`select = [\"ALL\"]\`,
Mypy remains strict, Pytest denies sockets, and branch-aware coverage remains at
least 90%. The doctor command is deterministic and emits only allow-listed,
non-secret configuration and readiness data.

\`msi doctor\` is the authoritative Stage A readiness command. The inherited
\`msd-foundation\` entry point remains only as a compatibility alias.

## Database and migration gate

The quality suite must prove:

- Alembic upgrades an empty local SQLite database to head and its explicit schema
  remains PostgreSQL-\`NUMERIC\` compatible under the schema-contract test;
- committed migrations and SQLAlchemy metadata agree;
- required tables, constraints, indexes, enums, exact \`NUMERIC\` columns, and
  foreign keys match the schema contract;
- authoritative value paths reject floats;
- downgrade policy is exercised as documented;
- same semantic input is idempotent;
- revisions and as-known-at history survive amendments/reviews; and
- no migration seeds fabricated financial observations.

Tests use an isolated disposable SQLite database or transaction by default. An
optional PostgreSQL exercise may run separately, but it is not required for the
local gate. Tests never point at a shared or production database.

## Fixture and network policy

Default fixtures are deterministic public/recorded or conspicuously synthetic and
live under \`tests/fixtures/\`. Retain source identity, URL, retrieval/publication
metadata, SHA-256, media type, and locator needed to reproduce expected results.
Do not commit credentials or the real SEC contact string.

Fixture coverage includes:

- SEC submissions and company facts;
- filing HTML and inline XBRL;
- filed earnings-release tables;
- investor-presentation PDF text;
- FFIEC/FR Y-9C/NIC-style regulatory records; and
- amendment, corporate-action, and name-change scenarios.

No unit/default test opens a socket. An opt-in live test uses explicit markers,
bounded source access, no committed secret, and cannot be a prerequisite for the
normal gate.

## Financial correctness and provenance

Required tests cover:

- exact Decimal parsing, negative/parenthetical values, unit/scale normalization,
  and reported precision;
- instant versus duration and fiscal-period resolution;
- metric aliases and deterministic table qualification;
- reporting-entity/scope and bank subsidiary/holding-company separation;
- missing disclosure versus measured zero;
- duplicate resolution, revisions, and bitemporal queries;
- MSR roll-forward and disclosed-total reconciliation;
- pairwise comparability and stable reasons;
- corporate-action boundaries;
- source/locator integrity; and
- derived formulas with exact input observation IDs.

Expected values are authored independently from production extraction/formula
code. Deliberately breaking a formula, locator, scale, scope, or sign must make a
test fail.

## Pipeline and review

Tests prove:

- identical reruns publish no duplicate observation;
- immutable raw hashes are stable;
- transient retry is bounded and deterministic failures do not loop;
- every terminal status reports published, not disclosed, quarantined, and failed
  cells explicitly;
- one deliberate ambiguity enters quarantine;
- quarantined values are absent from public reads;
- CLI approve/reject rebuilds the graph deterministically to its interrupt,
  resumes on the same opaque run thread, records the reviewer decision, and
  revalidates before any publication; and
- rejected and superseded history remains recoverable.

## API, UI, and accessibility

Contract tests cover all required \`/api/v1\` GET resources, strict filters,
bounded pagination/results, exact string serialization, safe errors, evidence
links, and schema stability.

A generated route inventory must prove public routes are read-only. No public
\`PUT\`, \`PATCH\`, \`DELETE\`, observation publication, or candidate-review
operation is allowed.

UI tests cover:

- data-as-of and observation-state labels;
- coverage, comparison, company detail, methodology, and evidence drawer;
- observation/evidence IDs and resolvable locators for every number;
- identical chart and table semantics;
- keyboard navigation, focus, headings, landmarks, labels, announcements,
  reflow, and no color-only meaning;
- no external CDN asset; and
- empty, stale, partial, conflicted, quarantined, and unavailable states.

The UI/API must operate with model calls, Deep Agents, remote tracing, and optional
LangGraph persistence disabled.

## Agent and tool boundary

Construction and negative tests prove:

- LangChain exposes only the eleven typed read tools documented in
  \`docs/ORCHESTRATION.md\`;
- no generic SQL, HTTP, browser, filesystem, shell, execution, unrestricted
  retriever, mutation, publication, or approval tool is visible;
- model-generated values absent from tool results cannot appear as authoritative
  output;
- comparability comes from the deterministic service;
- Deep Agents have bounded tasks, tools, recursion/subagents, tokens, runtime, and
  result size;
- Deep Agents have no network, unrestricted MCP, shell, filesystem mutation,
  persistent memory, publication, or approval capability; and
- all framework switches fail closed independently.

## Dependency, secret, and generated-artifact checks

Every dependency change updates \`pyproject.toml\` and \`uv.lock\` together.
Review the lock diff for unexpected provider SDKs, execution/sandbox packages,
MCP clients, telemetry, persistence backends, or duplicate stacks. Path and
editable dependency overrides are prohibited.

CI scans for credentials, private keys, tokens, real SEC contacts, restricted
data, accidental borrower/customer fixtures, and unsafe logging. Expected
synthetic detector strings require explicit test-only allowlisting.

Generated artifacts—including API/schema snapshots, route/tool inventories,
metric/universe compiled forms, and vendored static-asset manifests—must be
reproducible and clean after regeneration. Local chart assets retain license and
hash metadata.

## Documentation and release audit

Before handoff:

1. Run \`git diff --check\` and inspect the entire diff.
2. Confirm retained evidence bytes and dependency locks are unchanged unless the
   reviewed change explicitly requires them.
3. Verify documentation labels incomplete behavior as planned and uses only TFC,
   PFSI, and Q3 2025–Q2 2026 for Stage A.
4. Verify no production-ready, comprehensive-coverage, investment-advice, or
   industry-ranking claim exists.
5. Retain exact command output, migration results, provenance/reconciliation
   evidence, route/tool inventories, accessibility evidence, and screenshots.
6. Open a draft PR only after every Stage A gate passes; do not merge master.
