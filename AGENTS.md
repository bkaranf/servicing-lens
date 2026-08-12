# Servicing Lens development guidelines

## Scope and product boundary

This repository is a local, read-only, provenance-first public-data application for comparing selected U.S. mortgage servicers. It is pre-alpha: it is not production-ready, investment advice, or an industry ranking.

Preserve these boundaries:

- deterministic code owns numbers, normalization, formulas, validation, reconciliation, revisions, and comparability;
- use `Decimal` and SQL `NUMERIC` for money, balances, UPB, rates, and derived values;
- retained evidence is immutable and content-addressed; never edit bytes under `config/recorded_evidence/`;
- ambiguous values enter quarantine, and missing disclosure remains `NOT_DISCLOSED`;
- public routes are read-only;
- model calls, Deep Agents, tracing, and LangGraph persistence remain disabled by default;
- do not add deployment, hosting, authentication, scheduled jobs, or model-provider wiring without an accepted decision.

Read the governing documents in this order before changing behavior: `docs/PRODUCT_SCOPE.md`, `docs/SOURCE_AND_EVIDENCE_POLICY.md`, `docs/REPORTING_ENTITY_AND_SCOPE_MODEL.md`, `docs/METRIC_CATALOG.md`, `docs/DATA_MODEL.md`, `docs/ORCHESTRATION.md`, `docs/COMPARABILITY_POLICY.md`, `docs/IMPLEMENTATION_PLAN.md`, `docs/DECISIONS.md`, then `QUALITY_GUIDE.md`.

## Development workflow

Use `uv` for dependency and environment operations. Depend only on released packages. Do not use path or editable dependency sources. All Python code must be typed; new behavior and bug fixes require deterministic unit tests. Unit tests must not use the network.

Run the complete gate from the repository root:

```text
uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest --cov=mortgage_servicing_dashboard --cov-report=term-missing
uv run msi doctor --json
```

Use Conventional Commits with a scope, for example `feat(acquisition): add SEC filing discovery`. Branches use `<github-user>/<scope>/<short-description>`. Keep changes reviewable, inspect dependency and evidence diffs, and do not merge draft pull requests.

## Code quality and safety

- Preserve public signatures unless a reviewed change explicitly authorizes a break.
- Prefer keyword-only parameters for additions to public callables.
- Use Google-style docstrings for public functions.
- Avoid `eval`, `exec`, pickle on untrusted input, bare `except`, and silent exception handling.
- Close files, database connections, sockets, and threads deterministically.
- GitHub Actions must be pinned to full commit SHAs.
