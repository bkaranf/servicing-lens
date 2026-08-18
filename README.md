# Servicing Lens

Servicing Lens is a read-only public-data application for
comparing selected publicly traded U.S. mortgage servicers. It turns authoritative
SEC filings and filed earnings materials into reproducible observations with
complete provenance. Opt-in `edgartools` acquisition, filing-specific SEC XBRL,
and earnings-calendar adapters extend retained local data without changing the
network-free default.

The default registered Phase 5 scope is cohort B: five banks and five nonbanks.
The governed CLI order is stable:

- banks: TFC, WFC, JPM, BAC, and USB;
- nonbanks: PFSI, RKT, UWMC, RITM, and LDI;
- bounded filing periods: Q3 2024 through Q2 2026; and
- a compact configured financial metric subset, populated only where the
  retained sources support it. The catalog may retain immutable historical
  semantic versions, but it has one current definition per metric.

The smaller Phase 5 cohort A selector contains TFC, WFC, PFSI, and RKT. The
legacy retained Stage A and Phase 3 compatibility datasets remain limited to TFC
and PFSI for Q3 2025 through Q2 2026. Scope labels are deliberate: selecting a
Phase 5 registry does not claim that every configured metric is disclosed for
every issuer.

Missing disclosure remains `NOT_DISCLOSED`. The application does not estimate a
value to complete a comparison.

## Status

The Phase 5 cohort A and cohort B universe registries, live-sync source manifests,
and financial-field registry are installed with the wheel. Cohort B is the default
for company discovery, live filing discovery, live ingestion, and sync. Cohort A
remains an explicit bounded selector. Both use the same public-core `edgartools`
adapter, deterministic validation, and atomic persistence path.

Large retained filing bytes, bounded replay excerpts, and generated evidence-case
outputs are not wheel runtime data. They remain checkout-only verification assets.
The tracked Phase 5 replay can be verified or ingested offline from a checkout, but
it cannot be replayed from a bare installed wheel. Stage A and Phase 3 remain
documented compatibility workflows rather than the default registered scope.

The application includes SQLAlchemy/Alembic persistence, a read-only API and
dashboard, deterministic ingestion and review, exact provenance, and a
socket-blocked acceptance suite. Live access is always explicit.

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
- [deterministic ingestion and review](docs/ORCHESTRATION.md);
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
- Dashboard and API use only deterministic local application services.
- Default tests are deterministic and network-free.

## Installation

Work from this directory and use `uv`:

```bash
uv sync --locked --group dev
```

The locked install uses released packages only; there are no path or editable
dependency overrides. Do not install dependencies with `pip`.

No credential is required for the normal test suite. Explicit live SEC commands
require `EDGAR_IDENTITY`, held only in the local environment and never committed
or copied into logs, fixtures, screenshots, reports, or generated artifacts.

## CLI workflows

The command is `msi`. Readiness and registered-company discovery are local and
network-free:

```bash
uv run msi doctor --json
uv run msi discover
uv run msi discover --company JPM
uv run msi discover --phase5-cohort-a
```

`doctor` validates both cohort source manifests and their shared financial-field
mapping version before reporting readiness. It reports readiness only for the
implemented local, read-only workflows; it does not claim production readiness or
comprehensive issuer coverage. `discover` lists the declarative cohort B registry
by default and never contacts the SEC unless `--live` is present.

Live filing discovery and publication require a valid `EDGAR_IDENTITY`. Sync is
also an explicitly live command: `--dry-run` prevents database writes but still
queries SEC filings.

```bash
uv run msi discover --live --company WFC
uv run msi sync --all --dry-run
uv run msi sync --company JPM --database-url sqlite:///phase5.db
uv run msi ingest --live --database-url sqlite:///phase5.db
uv run msi ingest --live --phase5-cohort-a --database-url sqlite:///phase5-a.db
```

Non-dry live commands require an explicit isolated `--database-url`. They do not
fall back to the default local database. Missing or invalid identity, company,
configuration, date, storage, validation, and database failures are returned as
bounded JSON errors. The CLI never echoes `EDGAR_IDENTITY` or raw filing content.

Offline retained-data compatibility and governed replay are separate from live
acquisition:

```bash
uv run msi ingest --stage-a
uv run msi ingest --phase3
uv run msi seed-phase3
uv run python -m scripts.phase5_replay --check
uv run msi ingest --phase5-cohort-b --database-url sqlite:///phase5-replay.db --runtime-dir .msi
uv run msi ingest --phase5-cohort-a --database-url sqlite:///phase5-a-replay.db --runtime-dir .msi-a
```

Calling `ingest` without a mode still loads Stage A for backward compatibility;
new automation should say `--stage-a` explicitly. A Phase 5 cohort selector without
`--live` uses only the governed checkout replay, publishes through the same parser
and atomic persistence path, requires an explicit isolated database, and retains
the verified bounded derived fixtures below `--runtime-dir/evidence/edgartools` by
content SHA-256. Passing that same runtime root to `serve` makes the advertised
evidence resolvable. Repeating the replay against that database reports
`UNCHANGED`. A bare wheel contains the Phase 5 registries and live-sync manifests
needed at runtime, but not replay excerpts or retained evidence bytes; it returns a structured
`phase5_replay_unavailable` error. Phase 3 retained evidence is also checkout-only.

Validate and inspect a populated local database without seeding it:

```bash
uv run msi validate --database-url sqlite:///phase5.db
uv run msi calendar --database-url sqlite:///phase5.db
uv run msi coverage --database-url sqlite:///phase5.db --limit 50
uv run msi evidence --database-url sqlite:///phase5.db --evidence-id <evidence-id>
uv run msi serve --database-url sqlite:///phase5.db --runtime-dir .msi
```

These read commands require `--database-url` or `MSI_DATABASE_URL` pointing to an
existing database at the current Alembic revision. They never create the default
`.msi` directory, initialize a schema, migrate, or seed. `coverage` is paged to at
most 100 rows. `evidence` returns allow-listed metadata and never emits raw filing
content or a retained excerpt. `serve --runtime-dir` bounds evidence resolution
below that runtime root; its API also provides `GET /api/v1/coverage` and
`GET /api/v1/evidence/{evidence_id}`.

`review` remains the legacy quarantine workflow; approval and rejection reconstruct
the persisted run, record an audited decision, and revalidate rather than editing an
observation. `init-db` and `seed` remain Stage A compatibility commands.

## Read-only web surface

The FastAPI application provides a versioned read API and server-rendered Jinja2
pages enhanced with local vanilla JavaScript for:

- coverage and freshness;
- company comparison;
- company detail;
- methodology; and
- evidence drill-through.

Chart assets are hosted locally and every chart has an accessible table. Every
page shows data-as-of time and clearly labels reported actual, preliminary,
pro-forma, announced-impact, derived, and not-disclosed states.

The light Servicing Lens interface uses stable governed company and metric order,
a searchable company universe, a comparison bench with three visual slots, three
synchronized KPI selectors, and an event-backed earnings brief at `GET /earnings`.
The companies shown come from the populated database: Phase 5 cohort B can supply
ten, while the legacy Stage A compatibility ingest supplies two. These components
are server-rendered Jinja templates enhanced by local vanilla JavaScript; the
existing FastAPI routes, repository, evidence views, and public read API remain
authoritative.

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
and accessibility before a release is declared ready.
