# Public Mortgage Servicing Intelligence

Public Mortgage Servicing Intelligence is a read-only public-data application for
comparing selected publicly traded U.S. mortgage servicers. It turns authoritative
SEC filings, filed earnings materials, limited issuer investor-relations material,
and official bank regulatory data into reproducible observations with complete
provenance.

Stage A is intentionally narrow:

- bank: Truist Financial Corporation (TFC);
- nonbank: PennyMac Financial Services, Inc. (PFSI);
- fiscal periods: Q3 2025, Q4 2025, Q1 2026, and Q2 2026; and
- at least five useful servicing metrics where public disclosure supports them.

Missing disclosure remains \`NOT_DISCLOSED\`. The application does not estimate a
value to complete a comparison.

## Status

Stage A is implemented as a deterministic recorded-data vertical slice. It includes
versioned configuration, hash-verified retained SEC DOM serializations,
SQLAlchemy/Alembic persistence,
a read-only API and dashboard, an interruptible LangGraph review workflow, and a
socket-blocked acceptance suite. Live acquisition remains opt-in.

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

The former synthetic internal loan-operations documentation is preserved unchanged
under \`docs/archive/internal_servicing_operations_foundation/\` and is not
authoritative for this product.

## Non-negotiable behavior

- Deterministic code owns numbers, normalization, formulas, validation,
  reconciliation, revisions, and comparability.
- Money, balances, UPB, rates, and derived values use \`Decimal\` and PostgreSQL
  \`NUMERIC\`, never binary floating point.
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

Stage A removes editable dependencies on \`../libs/\`; a locked install must work
from this directory using released packages. Do not install dependencies with
\`pip\`, and do not modify upstream LangChain \`libs/\` for application behavior.

No credential is required for the normal test suite. A real SEC contact string is
required only for opt-in live acquisition and belongs in an untracked \`.env\` or
secret manager.

## CLI

The Stage A command is \`msi\`:

\`\`\`bash
uv run msi doctor --json
uv run msi discover --company TFC
uv run msi ingest --company TFC
uv run msi ingest --company PFSI
uv run msi validate
uv run msi review list
uv run msi serve
\`\`\`

All listed commands are implemented. Review approval/rejection creates an audited
decision with the same thread identifier and marks the candidate for revalidation;
it never edits a published observation directly.

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

Public JSON resources are:

- \`GET /api/v1/companies\`;
- \`GET /api/v1/companies/{company_id}\`;
- \`GET /api/v1/metrics\`;
- \`GET /api/v1/observations\`;
- \`GET /api/v1/observations/{observation_id}\`;
- \`GET /api/v1/comparisons\`;
- \`GET /api/v1/evidence/{evidence_id}\`;
- \`GET /api/v1/earnings-events\`; and
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
