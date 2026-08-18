# Servicing Lens

Servicing Lens is a read-only public-data application for
comparing selected publicly traded U.S. mortgage servicers. It turns authoritative
SEC filings and filed earnings materials into reproducible observations with
complete provenance. Opt-in `edgartools` acquisition, filing-specific SEC XBRL,
and earnings-calendar adapters extend the closed Stage A recorded-data slice
without changing its offline default.

The current governed universe remains intentionally narrow:

- bank: Truist Financial Corporation (TFC);
- nonbank: PennyMac Financial Services, Inc. (PFSI);
- fiscal periods: Q3 2025, Q4 2025, Q1 2026, and Q2 2026; and
- 53 Phase 3 metric IDs, populated only where the retained sources support them.

Missing disclosure remains \`NOT_DISCLOSED\`. The application does not estimate a
value to complete a comparison.

## Status

Stage A is closed as a deterministic recorded-data vertical slice, and the
application now lives in this history-preserving standalone repository. It includes
versioned configuration, hash-verified retained SEC DOM serializations,
SQLAlchemy/Alembic persistence,
a read-only API and dashboard, an interruptible LangGraph review workflow, and a
socket-blocked acceptance suite. The public-core `edgartools` qualification path
is exposed as `msi sync`; legacy acquisition implementations remain in the tree
pending bounded cleanup. Live access remains opt-in.
Phase 3 deepens only TFC and PFSI across the same four quarters. Its complete
424-cell disclosure assessment publishes 120 reported grid observations, 40
supporting observations, and 43 exact derived observations; 222 cells remain
`NOT_DISCLOSED` after their complete eligible source sets were checked. No
missing value is estimated or filled.

This is not production-ready, comprehensive issuer coverage, an industry ranking,
an audit product, or investment advice.

The product contains no borrower, customer, loan-level servicing-system, payment,
account, authentication, credential, or employer-internal data.

## Documentation

Start with the [documentation index](docs/README.md). The binding documents cover:

- [product scope](docs/PRODUCT_SCOPE.md);
- [source and evidence policy](docs/SOURCE_AND_EVIDENCE_POLICY.md);
- [reporting entities and scopes](docs/REPORTING_ENTITY_AND_SCOPE_MODEL.md);
- [metric definitions](docs/METRIC_CATALOG.md);
- [data model](docs/DATA_MODEL.md);
- [orchestration and agent boundaries](docs/ORCHESTRATION.md);
- [comparability](docs/COMPARABILITY_POLICY.md);
- [implementation and acceptance gates](docs/IMPLEMENTATION_PLAN.md); and
- [architecture decisions](docs/DECISIONS.md).

The former synthetic internal loan-operations documentation is preserved as
historical context under \`docs/archive/internal_servicing_operations_foundation/\` and is not
authoritative for this product.

## Non-negotiable behavior

- Deterministic code owns numbers, normalization, formulas, validation,
  reconciliation, revisions, and comparability.
- Money, balances, UPB, rates, and derived values use \`Decimal\` and SQL
  \`NUMERIC\`, never binary floating point. SQLite is the default local engine;
  PostgreSQL compatibility remains a tested schema contract.
- Retained evidence is immutable, content-addressed, and labeled by representation
  and capture method. The Stage A browser DOM serializations are not described as
  original HTTP response bytes.
- Every displayed value has observation/evidence IDs and a precise source locator.
- Entity, reporting scope, fiscal period, accounting policy, methodology, unit,
  scale, precision, and time are part of a value.
- Ambiguity enters quarantine. Models never publish or approve values.
- Revisions preserve prior evidence and as-known-at history.
- Public routes are read-only.
- Dashboard and API work with model calls, Deep Agents, tracing, and optional
  LangGraph persistence disabled.
- Default tests are deterministic and network-free.

## Installation

Work from this directory and use \`uv\`:

\`\`\`bash
uv sync --locked --group dev
\`\`\`

The locked install uses released packages only; there are no path or editable
dependency overrides. Do not install dependencies with \`pip\`.

No credential is required for the normal test suite. Explicit live SEC commands
require `EDGAR_IDENTITY`, held only in the local environment and never committed
or copied into logs, fixtures, screenshots, reports, or generated artifacts.

## CLI

The Stage A command is \`msi\`:

\`\`\`bash
uv run msi doctor --json
uv run msi discover --company TFC
uv run msi ingest
uv run msi seed-phase3
uv run msi ingest --phase3
uv run msi sync --all --dry-run
uv run msi calendar
uv run msi validate
uv run msi review list
uv run msi serve
\`\`\`

Phase 3 retained evidence is intentionally not bundled in the wheel. Run
`seed-phase3` and `ingest --phase3` from a repository checkout, or pass
`--config-dir` (or set `MSI_CONFIG_DIR`) to a complete external Phase 3
configuration and evidence root.

All listed commands are implemented. Stage A ingest is atomic across the complete
governed source set; discovery can be filtered by issuer. Review approval and
rejection rebuild the deterministic graph to its interrupt, resume on the
candidate's persisted run thread, create an audited decision, and run
revalidation; they never edit a published observation directly.

`msi sync` uses the public core `edgartools` company, filing, attachment, and XBRL
interfaces for bounded per-CIK qualification, evidence retention, deterministic
parsing, reconciliation, and optional atomic publication. Without
`EDGAR_IDENTITY` it fails before opening a database or socket. The legacy
`discover --live` and `ingest --live` dispatch paths remain pre-cleanup code; they
are not the supported acquisition boundary and must not be used for new evidence.
`msi calendar` keeps the last actual filing separate from its conspicuously
inferred next report window and lists every filing event used in the inference.

## Stage A web surface

The FastAPI application provides a versioned read API and server-rendered
Jinja2/HTMX pages for:

- coverage and freshness;
- company comparison;
- company detail;
- methodology; and
- evidence drill-through.

Chart assets are hosted locally and every chart has an accessible table. Every
page shows data-as-of time and clearly labels reported actual, preliminary,
pro-forma, announced-impact, derived, and not-disclosed states.

The light Servicing Lens interface adds a searchable company universe, four
presentation sorts, a comparison bench with three visual slots and two governed
Stage A issuers, three synchronized KPI selectors, and an event-backed earnings
brief at `GET /earnings`.
These components are Jinja templates with progressively enhanced vanilla
JavaScript; the existing FastAPI routes, repository, evidence views, and public
read API remain authoritative.

Presentation-only normalization lives in `presentation.py` and uses exact
`Decimal` arithmetic. The current governed dataset maps fields as follows:

- Servicing UPB: latest reported `total_servicing_upb`.
- UPB growth: exact quarter-over-quarter change in reported total servicing UPB,
  only when the observations are adjacent fiscal quarters with matching entity,
  scope, methodology, currency, unit, and metric version.
- PFSI owned/MSR mix: `owned_msr_upb / total_servicing_upb`.
- TFC bank-owned share: `bank_owned_loans_serviced_upb / total_servicing_upb`;
  this is explicitly not labeled as owned-MSR mix.
- Customer loans, servicing platform, and earnings sentiment: unavailable because
  the governed Stage A pipeline does not currently publish those fields.

Mix inputs must share the same period and compatible entity, scope, currency,
unit, and period type. Relative portfolio scales render only when the repository's
authoritative comparison result is `comparable`; the interface fails closed and
shows its governed reason otherwise. Every reported or derived presentation value
links to its observation and precise evidence locator, and derived values disclose
all inputs.

The earnings brief uses the latest issuer event returned by
`IntelligenceRepository.earnings_events()`, its official source URL, and only
observations matching that event's fiscal year and quarter. If that period has no
published observations, deterministic summaries and signals render unavailable
rather than silently falling back to a newer or older global observation.
It does not reuse the reference implementation's demonstration companies or
values and does not characterize sentiment when none is produced by the pipeline.

Public JSON resources are:

- \`GET /api/v1/health\`;
- \`GET /api/v1/companies\`;
- \`GET /api/v1/companies/{company_id}\`;
- \`GET /api/v1/metrics\`;
- \`GET /api/v1/observations\`;
- \`GET /api/v1/observations/{observation_id}\`;
- \`GET /api/v1/comparisons\`;
- \`GET /api/v1/coverage\`;
- \`GET /api/v1/evidence/{evidence_id}\`;
- \`GET /api/v1/earnings-events\`;
- \`GET /api/v1/calendar\`; and
- \`GET /api/v1/pipeline/freshness\`.

## Verify

Run the local gate documented in [QUALITY_GUIDE.md](QUALITY_GUIDE.md). At minimum:

\`\`\`bash
uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest --cov=mortgage_servicing_dashboard --cov-report=term-missing
uv run msi doctor --json
\`\`\`

The suite blocks sockets, enforces strict typing and at least 90% branch coverage,
and must also cover migrations, schema contracts, dependencies, secrets,
generated artifacts, provenance, comparability, forbidden tools, read-only routes,
and accessibility before Stage A can exit.
