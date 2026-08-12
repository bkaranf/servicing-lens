# Stage A Gauntlet progress

Branch: `bkaranf/repo/standalone-extraction`

Base: filtered standalone `main` at
`ec758b6be35cd8363fcac7db94ba665d60b58b05`

Audit date: 2026-08-12

This is the concise release-audit ledger for the Stage A Gauntlet. Product
contracts, retained evidence, migrations, and tests remain authoritative.

## Evidence, data, migrations, and orchestration

- Verdict: **passed**.
- `config/stage_a_data.yaml` contains identity, acquisition metadata, source
  hashes, period metadata, and parser recipes. It contains no authoritative
  financial observation value.
- The retained TFC document is 1,697,426 bytes with SHA-256
  `7353334b2f40cb48d0ed6dc6756378e93260d2e2b6541ea37d800790057a7883`.
- The retained PFSI document is 741,531 bytes with SHA-256
  `db128f08fa4fff4835e13467e6dc18f081983b64618ada3e6a7ee7097ade78cf`.
- Hash-verified parsing produces 36 published observations. The catalog grid
  also retains 220 `SOURCE_NOT_CHECKED` cells and one quarantined candidate;
  none is mislabeled `NOT_DISCLOSED`.
- The graph has 16 substantive Stage A nodes. XBRL and bank-regulatory extraction
  are Phase 2 adapters, not placeholder Stage A nodes.
- Run keys are content/config/parser-derived, retries are bounded to three,
  terminal states are explicit, and deterministic failures fail closed.
- CLI approve and reject both rebuild the deterministic graph to its interrupt
  and resume on the candidate's persisted run thread, retain attributed
  decisions, and run deterministic revalidation.
  Approval leaves the ambiguous value `QUARANTINED_AFTER_REVALIDATION`; rejection
  leaves it `REJECTED`.
- Four versioned pairwise comparison assessments are retained. The Q2 2026
  `total_servicing_upb` assessment is `not_comparable` because reporting scopes
  and portfolio populations differ; no arithmetic is permitted.
- Migration `0001` contains 27 explicit create-table and 27 explicit drop-table
  operations and no metadata-wide `create_all()` or `drop_all()` call.

## API, tools, dashboard, and accessibility

- Verdict: **passed**.
- The governed dataset supports the read-only API, server-rendered dashboard,
  evidence drill-through, locally hosted assets, accessible chart tables, and
  exact `Decimal` presentation values.
- Every displayed reported or derived value carries observation/evidence IDs and
  a locator. Derived presentation values retain all inputs.
- The comparison bench has three visual slots but only the two governed Stage A
  issuers are available.
- Public API routes are read-only. Model calls, Deep Agents, remote tracing, and
  optional LangGraph persistence remain disabled by default.

## Final integration and release

- Verdict: **passed on 2026-08-12**.
- Final closure gate on the diff from HEAD `3139ad7c10`:

```text
uv sync --locked --group dev
Resolved 85 packages in 0.76ms
Checked 84 packages in 8ms

uv run ruff check .
All checks passed!

uv run ruff format --check .
63 files already formatted

uv run mypy src tests
Success: no issues found in 31 source files

uv run pytest --cov=mortgage_servicing_dashboard --cov-report=term-missing
84 passed, 23 warnings in 32.22s
Required test coverage of 90.0% reached. Total coverage: 91.33%

uv run msi doctor --json
status: ready; stage: A; universe: TFC, PFSI; all optional runtime switches: false
```

- The lockfile SHA-256 before and after the final gate was
  `C403A3368034CBA6613E2541005A1EEABCEA6AC66A3D0DA9822ADBAE8F36107B`.
- The 23 non-failing warnings comprise one third-party Python 3.17 deprecation
  warning and test-process SQLite connection `ResourceWarning` debt.
- Draft-PR evidence is recorded in the Phase 0 handoff and D-015. The
  pre-existing untracked `artifacts/` directory is
  user-owned and is not part of the closure commit.

## Standalone extraction

- Verdict: **local gate passed; fresh-clone handoff pending**.
- The source application commit and subtree tree were frozen before filtering;
  D-013 records the complete old-to-new commit mapping and rollback method.
- The extracted root contains application source, tests, migrations,
  configuration, proposals, governing documentation, released dependency pins,
  consolidated contributor instructions, and standalone CI for Python 3.11 and
  3.14.
- Scoped Git attributes keep retained evidence binary. Both retained files match
  the Stage A byte counts and SHA-256 values above after extraction.
- The standalone local gate passes locked sync, lock validation, Ruff, Ruff
  format, strict Mypy, 84 socket-blocked tests at 91.33% branch coverage, the
  explicit Alembic upgrade/check/downgrade/upgrade round trip, deterministic
  doctor, and patch hygiene.
- Phase 1 exits only after the new remote branch is cloned from scratch with
  automatic line-ending conversion enabled and that clone passes the same gate.
