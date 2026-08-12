# Implementation plan

## Execution rule

Work depth-first. Stage A is one bank, one nonbank, four selected fiscal quarters,
and at least five useful metrics. Do not add a third issuer, extend the historical
window, or build Stage B scaffolding while a Stage A provenance, reconciliation,
reproducibility, review, API, UI, or quality gate is open.

Stage A subjects are TFC and PFSI for Q3 2025, Q4 2025, Q1 2026, and Q2 2026.
Missing public disclosure remains missing.

The repository now implements the Stage A recorded-data slice described below.
Each item remains release-ready only while code, migrations, deterministic
fixtures, tests, and documentation continue to agree.

## A0 — Preserve history and make the package self-contained

Deliver:

- byte-preserved archive of the internal servicing-operations foundation;
- the nine authoritative public-product documents and new handoff;
- package/display/CLI naming for Public Mortgage Servicing Intelligence;
- removal of editable \`../libs/langchain_v1\` and \`../libs/core\` overrides;
- deliberately regenerated \`uv.lock\` using released packages;
- FastAPI, Pydantic, SQLAlchemy 2, Alembic, PostgreSQL driver, Jinja2/HTMX, and
  locally hosted chart dependencies/assets; and
- a decision recording future extraction into a standalone repository.

Gate:

- installation succeeds from \`mortgage_servicing_dashboard/\` without local
  \`libs/\`;
- lockfile review finds no unintended provider SDK, execution sandbox, unrestricted
  MCP, telemetry, or persistence dependency; and
- upstream \`libs/\` remains unchanged.

## A1 — Verify source discovery and freeze the Stage A universe

Deliver:

- documented discovery assessment for TFC and PFSI;
- verified legal names, tickers, CIKs, fiscal calendars, reporting segments,
  reporting scopes, accounting policies, and source availability;
- exact Q3 2025–Q2 2026 filing/exhibit/regulatory inventory;
- versioned universe configuration with effective identities and source classes;
- at least five metric paths supported by eligible evidence; and
- explicit missing/ambiguous coverage map.

Gate:

- every identity and source assertion has an official locator;
- filed materials are preferred over unfiled IR copies;
- bank holding company, depository, SEC registrant, segment, and subsidiary remain
  separate; and
- no observation value is entered manually in configuration.

## A2 — Implement exact domain and persistence spine

Deliver:

- pure domain types/functions for Decimal values, periods, units, observation
  state, entity/scope resolution, revision identity, roll-forward reconciliation,
  and pairwise comparability;
- SQLAlchemy models for all required tables;
- clean-database Alembic migrations and constraints;
- immutable metric-definition versions and aliases;
- bitemporal and supersession behavior;
- idempotent semantic keys; and
- repositories/services that expose typed operations rather than arbitrary SQL.

Gate:

- clean PostgreSQL upgrade succeeds;
- ORM/migration/schema contracts agree;
- float inputs fail;
- same semantic input is idempotent;
- amendments and review decisions preserve prior history; and
- as-known-at queries return the correct revision.

## A3 — Acquire and retain evidence

Deliver:

- SEC discovery/acquisition adapter with descriptive configured User-Agent,
  conservative rate limit, bounded concurrency, backoff, caching, and conditional
  requests;
- filed-exhibit-first issuer IR adapter;
- FFIEC Call Report, FR Y-9C, and NIC adapter boundaries;
- content-addressed immutable evidence storage;
- deterministic filing HTML/inline XBRL, table, PDF-text, and regulatory parsers;
- source-contact removal for bounded model excerpts; and
- offline recorded fixtures for every Stage A source path.

Gate:

- raw hashes are stable;
- replay requires no sockets;
- different bytes from one URL create a new evidence record;
- source IDs/locators resolve;
- no raw body enters graph state, logs, or model context; and
- no screenshot, snippet, transcript, aggregator, or model memory supports a
  published value.

## A4 — Complete the ingestion, quarantine, and review path

Deliver:

- all 16 typed LangGraph nodes;
- idempotent/resumable run keys, explicit terminal states, structured errors, and
  bounded transient retries;
- deterministic extraction before optional model proposals;
- entity/period/scope resolution, exact normalization, validation, duplicate
  handling, MSR/total reconciliation, and publication;
- explicit \`NOT_DISCLOSED\` handling;
- at least one deliberately ambiguous fixture routed to quarantine; and
- audited \`msi review\` approve/reject flow with same-thread resume.

Gate:

- reingestion creates no duplicate publication;
- deterministic failures do not retry indefinitely;
- missing remains missing;
- ambiguous candidates never appear on public reads;
- approval revalidates before publication;
- rejection preserves the candidate/evidence; and
- graph persistence remains independently disabled unless an injected saver is
  configured.

## A5 — Publish the read service and accessible dashboard

Deliver:

- the eight required versioned JSON GET routes;
- coverage/freshness, company comparison, company detail, methodology, and
  evidence-drawer pages;
- server-rendered Jinja2 and HTMX;
- a locally hosted chart library with no CDN requests;
- equivalent accessible tables;
- data-as-of time and observation-state labels on every page;
- exact values serialized without float conversion;
- deterministic pairwise comparability with at least one explicit
  \`NOT_COMPARABLE\` example; and
- empty, stale, partial, conflicted, quarantined, and unavailable UI states.

Gate:

- every displayed number exposes observation/evidence IDs and a resolvable
  locator;
- API and UI values reconcile exactly;
- public route inventory contains no mutation;
- chart alternatives contain identical semantics;
- keyboard/focus/navigation tests pass; and
- dashboard works with all model capabilities off.

## A6 — Replace static model tools with bounded analytical reads

Deliver:

- eleven required typed LangChain read tools over services;
- structured answer schema with observations, evidence, exact calculations,
  comparability warnings, missing disclosures, limitations, and citations;
- negative tests for SQL, HTTP, filesystem, shell, mutation, approval, and
  publication tools; and
- optional bounded Deep Agent tasks using only the same read tools.

Gate:

- disabling model calls and Deep Agents changes no data/API/UI behavior;
- models cannot introduce a number absent from tool results;
- comparability comes only from the deterministic service;
- Deep Agents remain independently disabled and bounded; and
- no arbitrary network, MCP, shell, filesystem mutation, memory, or publication
  capability is visible.

## A7 — Quality, security, and release audit

Deliver:

- deterministic source and parser fixtures;
- dependency, secret, migration, schema-contract, generated-artifact, and local
  static-asset checks;
- credential/restricted-data scan;
- route and agent capability inventories;
- documentation/behavior reconciliation;
- screenshots of the required pages for the draft PR; and
- final diff/dependency review.

Gate:

- locked sync, Ruff, Ruff format, strict Mypy, socket-blocked Pytest, branch
  coverage of at least 90%, migration checks, and deterministic doctor pass;
- all source/evidence/calculation/reconciliation acceptance evidence is retained;
- no borrower/customer/private servicing data or credential exists anywhere in the
  repository;
- no upstream \`libs/\` change exists; and
- the draft PR is opened against the intended repository without merging master.

## Required deterministic test inventory

Fixtures cover SEC submissions, company facts, filing HTML, inline XBRL,
earnings-release tables, investor-presentation PDF text, FFIEC/regulatory records,
and corporate-action/name-change scenarios.

Tests cover:

- idempotent ingestion and raw hash stability;
- filing amendment and supersession;
- fiscal-period and instant/duration resolution;
- Decimal precision, scale, negative, and parenthetical normalization;
- table-parser qualification and metric aliases;
- reporting entity/scope and bank subsidiary/holding-company separation;
- duplicate resolution and bitemporal as-known-at queries;
- missing disclosure and quarantine;
- same-thread review resume;
- MSR roll-forward reconciliation;
- pairwise comparability and corporate actions;
- source citation integrity;
- model-disabled dashboard operation;
- forbidden agent tools and public read-only route inventory;
- accessible chart tables and keyboard navigation; and
- empty, stale, partial, conflicted, quarantined, and unavailable states.

Live source smoke tests are opt-in and never required for normal CI.

## Ownership and sequencing

Single-owner shared surfaces:

- \`alembic/\`, \`core/\`, database models/repositories, graph/state, universe,
  metric registry, \`pyproject.toml\`, and \`uv.lock\`;
- source/evidence adapters and shared parsers;
- API/services/templates/static/CLI;
- model tools/privacy/Deep Agents boundaries; and
- documentation and CI contracts.

After shared contracts stabilize, issuer-specific disclosure maps, recipes, and
fixtures can split by TFC and PFSI. Issuer owners do not edit shared metrics,
universe identity, migrations, core logic, graph, or dependencies concurrently.

## Stage A exit and Stage B hold

Stage A exits only when all 15 product-scope acceptance outcomes pass together.
Passing unit tests while provenance, source coverage, UI, review, or
reconciliation is incomplete is not exit.

Stage B remains a future expansion target of at least five banks, five nonbanks,
and eight quarters where available. Issuers and metrics must be reassessed live;
the Stage A configuration is not silently generalized.
