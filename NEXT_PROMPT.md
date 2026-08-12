# Next prompt: finish the Stage A public-data vertical slice

Work only in \`mortgage_servicing_dashboard/\` on
\`codex/public-servicing-intelligence-v0\`. Do not modify upstream \`libs/\`, do
not merge master, and do not expand to Stage B.

Read, in order:

1. \`AGENTS.md\` files applicable to the worktree and application;
2. \`docs/PRODUCT_SCOPE.md\`;
3. \`docs/SOURCE_AND_EVIDENCE_POLICY.md\`;
4. \`docs/REPORTING_ENTITY_AND_SCOPE_MODEL.md\`;
5. \`docs/METRIC_CATALOG.md\`;
6. \`docs/DATA_MODEL.md\`;
7. \`docs/ORCHESTRATION.md\`;
8. \`docs/COMPARABILITY_POLICY.md\`;
9. \`docs/IMPLEMENTATION_PLAN.md\`;
10. \`docs/DECISIONS.md\`; and
11. \`QUALITY_GUIDE.md\`.

Inspect the current branch, dirty worktree, complete application source, tests,
migrations, fixtures, dependencies, CI, and documentation before editing. Existing
changes belong to other workers unless ownership says otherwise; do not revert
them.

## Outcome

Complete one depth-first, reviewable path for:

- Truist Financial Corporation (TFC), bank;
- PennyMac Financial Services, Inc. (PFSI), nonbank;
- Q3 2025, Q4 2025, Q1 2026, and Q2 2026; and
- at least five useful servicing metrics supported by public evidence.

The path is:

public source → immutable evidence → deterministic extraction → entity/period/scope
resolution → exact normalization → validation/reconciliation → observation →
versioned read API → accessible dashboard → evidence drawer.

Do not add a third issuer or additional periods until every Stage A gate passes.
Do not fabricate a value, source, locator, reconciliation, test result, or source
coverage. A missing metric remains \`NOT_DISCLOSED\`.

## Required implementation behavior

- Verify legal names, tickers, CIKs, fiscal calendars, reporting entities/scopes,
  accounting regimes, and document availability from official sources.
- Keep the company universe versioned and configuration-driven.
- Remove editable local LangChain overrides and keep the package independently
  installable from released dependencies with a deliberate \`uv.lock\`.
- Implement the required SQLAlchemy/Alembic data model with exact PostgreSQL
  \`NUMERIC\`, bitemporal history, revisions, and semantic uniqueness.
- Retain immutable source bytes and complete evidence metadata before parsing.
- Implement controlled SEC, issuer IR, FFIEC Call Report, FR Y-9C, and NIC adapter
  boundaries.
- Implement all 16 typed ingestion nodes with idempotent run keys, explicit
  terminal states, bounded transient retry, quarantine, and no silent partial
  success.
- Route one deliberate ambiguity to quarantine and support audited CLI
  approve/reject with same-thread resume.
- Implement deterministic pairwise comparability and retain one explicit
  not-comparable case.
- Implement the eight required GET API resources and server-rendered Jinja2/HTMX
  pages with locally hosted charts and accessible tables.
- Replace static agent tools with the eleven typed read-only tools only after the
  service layer is authoritative.
- Keep model calls, Deep Agents, tracing, and optional graph persistence
  independently disabled by default. The application must work with all of them
  off.

## Before handoff

1. Reconcile documentation with behavior; planned text must not be presented as
   complete.
2. Inspect every dependency and the entire diff.
3. Run the locked, lint, format, strict typing, socket-blocked coverage, migration,
   schema, generated-artifact, secret, route/tool inventory, and doctor gates.
4. Verify every displayed value has observation/evidence IDs and a working
   locator.
5. Verify no borrower, customer, private servicing, payment, account, credential,
   generic agent tool, external CDN, or public mutation route exists.
6. Record exact commands and verbatim results.
7. Create a draft pull request against the intended repository; do not merge.

Report outcome, Stage A source coverage, architecture, files, migrations, exact
commands/results, provenance/reconciliation evidence, agent/tool boundaries,
routes/screenshots, quarantined/missing metrics, and remaining owner decisions.
Do not call the result production-ready, comprehensive, investment advice, or an
industry-wide ranking.
