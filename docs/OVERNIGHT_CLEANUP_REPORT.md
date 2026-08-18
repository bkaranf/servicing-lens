# Overnight Cleanup Report

Status: implementation phases 0–7 are complete, locally gated, and pushed to
the authoritative cleanup branch. The overall overnight mission is **not yet
complete**: the six fresh-context final critics and completion audit remain
outstanding.

## 1. Starting branch and commit

- Branch: `bkaranf/data/edgar-tools-only-cleanup`
- Mission starting checkpoint: `f8e7d3758cea6a50c6f20f8ebcd9d5b18a7181cf`
  (`feat(qualification): pass financial qualification gate`)
- The starting checkpoint was a clean descendant of the earlier required
  baseline `0add6bca1351c020edec59251657c60fc0bc7e45` and was 0 behind / 14 ahead
  of `origin/main` at the Phase 0 audit.

## 2. Ending branch and commit

- Branch: `bkaranf/data/edgar-tools-only-cleanup`
- Product-code checkpoint:
  `de7d512b7ce0cac6fa103c17af7578ebfb4bd5cd`
  (`chore(repo): remove obsolete framework and evidence bloat`)
- Initial documentation checkpoint:
  `5b7efc57d3a5cf97176ab6990bedc87d8044f507`
  (`docs(repo): add cleanup report and continuation handoff`).
- Final handoff correction: the commit containing this updated report, with
  subject `docs(repo): correct pushed dashboard handoff`.

## 3. Divergence from origin/main

At the product-code checkpoint, `git rev-list --left-right --count
origin/main...HEAD` returned `0 23`: 0 behind and 23 ahead. The documentation
checkpoint made this 0 behind / 24 ahead before the final handoff correction.
Re-run the command after fetching remote metadata for the current count.

## 4. Summary of deleted complexity

- Replaced LangGraph/deep-agent orchestration with explicit typed,
  deterministic Python state transitions while preserving ordering, status,
  idempotence, quarantine, review, resume, revision, and revalidation behavior.
- Consolidated acquisition behind one thin public-edgartools adapter; removed
  hosted/provider abstractions, direct SEC HTTP acquisition, agent middleware,
  and their obsolete tests.
- Simplified the financial comparison core to declarative canonical mappings
  with exact Decimal/SQL NUMERIC/string-JSON paths.
- Removed stale Phase 2 calendar fixtures, obsolete phase-specific catalog and
  recipe files, three zero-caller Stage A fixtures, a stale progress file, and
  dead compatibility tests/modules.
- Narrowed wheel contents to runtime configuration actually needed by an
  installed package.
- Optimized `og.png` from 1,083,569 bytes to 553,830 bytes while preserving
  visual content and adding structural image validation.

The 63.74 MiB recorded-evidence corpus was deliberately retained; section 24
explains why it is still authoritative rather than obsolete bloat.

## 5. Dependencies removed

Direct dependencies removed from `pyproject.toml`:

- `deepagents`
- `langchain`
- `langchain-core`
- `langgraph`
- direct `httpx` declaration (it may remain transitively through edgartools)

The obsolete `msd-foundation` script alias was also removed. `psycopg[binary]`
was retained because PostgreSQL URLs remain a supported SQLAlchemy path.

## 6. Dependencies added

No new direct runtime package was added. A lock-resolution constraint,
`numpy>=2.4.6,<2.5`, was added to preserve the declared Python 3.11 and 3.14
quality gate; NumPy was already present transitively through edgartools.

## 7. Whether LangGraph remained and exact reason

LangGraph did not remain. Production and test call-site audits showed that its
graph/model abstraction was unnecessary for the deterministic ingestion and
review workflow. Explicit typed transitions now implement the preserved
behavior without an application AI runtime or workflow framework.

## 8. edgartools version

The project pins and locks open-source `edgartools==5.48.0`. Live acquisition
uses supported public edgartools APIs only.

## 9. SEC identity status

`EDGAR_IDENTITY` was present and validated before any Phase 5 socket use. Its
value was never printed, logged, stored in tracked files, or included here.
Missing or invalid identity is rejected before a socket is opened.

## 10. Live smoke status

One coordinated, sequential Phase 5 network lane ran through public edgartools
at the bounded project rate (no more than 9 requests/second), with caching and
bounded retries. TFC, WFC, PFSI, and RKT passed the common-path proof; the 5+5
published cohort and the supported registry were then evidenced. Company Facts
worked after correcting the supported bootstrap setting
`EDGAR_USE_LOCAL_DATA=0`. There was no SEC block or rate-limit stop.

No post-Phase-7 live rerun was performed. Final gates were intentionally
offline and socket-blocked. Live caches and databases stayed outside tracked
paths.

## 11. Five completed banks

The published bank cohort is TFC, WFC, JPM, BAC, and USB. Each passed the common
filing/XBRL/parser/mapping/sync/persistence/read-surface path with complete
accession, document, URL, hash, length, locator, entity, scope, and period
provenance.

## 12. Five completed nonbanks

The published nonbank cohort is PFSI, RKT, UWMC, RITM, and LDI. LDI replaced
ONIT after the governed ONIT historical-document stop described in section 14.
No predecessor/current-issuer history was blended.

## 13. 20-company registry status

The evidence-vetted registry contains exactly 10 banks and 10 public nonbanks,
without a ranking or “top ten” claim:

- Banks: TFC, WFC, JPM, BAC, USB, C, PNC, FITB, CFG, KEY.
- Nonbanks: PFSI, RKT, UWMC, RITM, LDI, TWO, CHMI, NLY, FOA, VEL.

The first five banks and first five nonbanks are published end to end. The
additional 5+5 are evidence-vetted registry entries, not claimed as fully
published historical cohorts. Registry and universe files are generated,
value-free, and reference parser-derived evidence cases.

## 14. Companies excluded and why

- ONIT: its current identity was valid, but the required 2024-Q3 primary
  document for accession `0001628280-24-046004`, `onit-20240930.htm`, returned
  a safe `NOT_FOUND` through the public adapter. It was excluded rather than
  blending Ocwen/Onity history or weakening the evidence standard.
- GHLD: its merger closed on 2025-11-28 and it filed Form 15 on 2025-12-08, so
  it was not treated as a current registrant candidate.
- COOP/Mr. Cooper and other predecessor histories were not represented as
  separate current issuers and were not blended across legal-entity boundaries.

## 15. TFC/PFSI before/after observation counts

The immutable legacy baseline had 439 observations: TFC 216 and PFSI 223.
Phase 5 added 16 parser-derived observations per published issuer (two metrics
across eight filing periods). The combined replay therefore has TFC 232 and
PFSI 239, within a 599-observation 5+5 database. All 439 legacy IDs and values
remain unchanged.

## 16. Intentional data changes

No legacy financial value, status, methodology, or observation ID changed.
Phase 5 intentionally adds new parser-derived observations and provenance for
the expanded cohort. All authoritative numeric values originate in tracked,
bounded SEC-derived replay fixtures and traverse the real adapter, parser,
mapping, sync, persistence, and publication path. Universe/registry files do
not embed manual financial values.

The 439-observation legacy CSV SHA-256 is
`112661f7d3414793f747c6cdd9a890f480a2f98768bb8268cae9ad70c2e3f0b2`;
725 legacy evidence links are preserved.

## 17. Remaining NOT_DISCLOSED limitations

The legacy baseline intentionally retains 222 explicit `NOT_DISCLOSED`
observations. Missing values are never converted to zero or estimated. The
Phase 5 mappings do not force completeness, unsupported corporate-action prose
was removed or marked `NOT_DISCLOSED`, and scope-incompatible comparisons fail
closed. Evidence-vetted registry entries outside the published 5+5 do not imply
eight-quarter metric completeness.

## 18. Test commands/results

The final Phase 7 run used the short external environment
`%LOCALAPPDATA%\Temp\servicing-lens\phase7-final-venv` with
`UV_LINK_MODE=copy`:

```powershell
uv sync --locked --group dev
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest --cov=mortgage_servicing_dashboard --cov-report=term-missing
uv run python scripts/phase5_replay.py --check
uv run python -m scripts.phase5_replay --check
uv run python scripts/generate_phase5_manifest.py --check
uv run python -m scripts.generate_phase5_manifest --check
uv run msi doctor --json
git diff --check
```

Results: lock, Ruff, format (101 files), mypy (55 source files), both generator
entry forms, doctor, and diff checks passed. The full socket-blocked test suite
passed 649 tests with 32 non-failing warnings in 304.12 seconds.

## 19. Migration results

Disposable SQLite Alembic upgrade to head, downgrade to base, and re-upgrade to
head passed. The bounded offline wheel build/install test and isolated installed
cohort-selector probes also passed. Three combined migration/wheel checks passed.

## 20. Coverage result

The final full offline suite reported 90.52% line coverage for
`mortgage_servicing_dashboard`, above the repository gate.

## 21. Critic findings/repairs

Fresh Luna critics were used after each phase. Material repaired findings
included cross-version wheel/NumPy/type-check failures; deterministic runtime
preservation; public-edgartools configuration and provenance boundaries;
Decimal/comparability correctness; manual-value removal; active-issuer and
GET-only application behavior; replay evidence retention; and Phase 3 run-key
stability.

The final Phase 7 critic found three related high-severity run-identity issues:
regulatory reconciliation could read a different config root than the one
hashed, absolute checkout paths affected evidence hashes, and a truncated run
ID could silently reuse a different full key. Repairs now thread the explicit
config root, hash a path-free evidence projection, and compare full keys before
reuse with fail-closed collision behavior. Explicit-root, relocated-checkout,
and collision/rollback regressions pass. Phase 7 critic closure was 0 blocker,
0 high, 0 medium, and 0 low.

The mission-wide six final fresh-context critics have not yet run; this report
does not substitute for them.

## 22. Files removed

Thirty-three files were removed since the mission starting checkpoint,
including:

- `GAUNTLET_PROGRESS.md`;
- obsolete runtime modules `agent.py`, `deep_worker.py`, `orchestration.py`,
  `state.py`, `tools.py`, `privacy.py`, old acquisition/evidence modules, and
  the superseded qualification module;
- `config/metrics/phase3_deepening.v1.yaml` and
  `config/issuers/tfc/phase3_metric_recipes.v1.yaml`;
- five stale Phase 2 calendar fixtures and three zero-caller Stage A fixtures;
- obsolete agent/framework/acquisition/privacy/Phase-2 tests; and
- the old live-smoke filename, replaced by the edgartools-specific test.

Phase 7 itself removed four files totaling 57,135 bytes. Archive bytes,
recorded evidence, and generated Phase 5 fixtures were not deleted.

## 23. Files added

Additions include six narrow repository skills under `.agents/skills`, three
Phase 5 generator/replay script files, 11 generated/value-free Phase 5 config
files, `capabilities.py`, 90 bounded SEC-derived XML replay fixtures plus their
index, the public-edgartools live smoke replacement, and focused Phase 5/6
regression suites. This report and `HANDOFF.md` are added by the documentation
checkpoint.

## 24. Known limitations

- The required six final critics and completion audit remain outstanding.
- ONIT's specified historical primary document remains unavailable through the
  supported public acquisition path; LDI is the governed published replacement.
- The second 5+5 registry cohort is evidence-vetted but not published with the
  same eight-quarter depth as the primary 5+5.
- No final post-cleanup SEC live smoke was run; all final validation was offline.
- `config/recorded_evidence` remains 25 files / 66,839,747 bytes (63.74335 MiB).
  It is required for the 439 immutable observations, 725 evidence links, 43
  derivations, and 222 negative disclosure decisions. Phase 5 overlaps only
  eight cells, reproduces none of the legacy observation IDs/source hashes, and
  is therefore not an equivalent replacement.
- Dashboard keyboard/dialog behavior is contract-tested without introducing a
  browser dependency.

## 25. Exact recommended review commands

```powershell
git status --short --branch
git log --oneline f8e7d3758cea6a50c6f20f8ebcd9d5b18a7181cf..HEAD
git rev-list --left-right --count origin/main...HEAD
git diff --stat f8e7d3758cea6a50c6f20f8ebcd9d5b18a7181cf..HEAD
git diff --check f8e7d3758cea6a50c6f20f8ebcd9d5b18a7181cf..HEAD
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest --cov=mortgage_servicing_dashboard --cov-report=term-missing
uv run python scripts/phase5_replay.py --check
uv run python -m scripts.phase5_replay --check
uv run python scripts/generate_phase5_manifest.py --check
uv run python -m scripts.generate_phase5_manifest --check
uv run msi doctor --json
```

Run these with `UV_PROJECT_ENVIRONMENT` pointing to a short external environment
and `UV_LINK_MODE=copy` if OneDrive locks the repository `.venv`.

## 26. Publication status

The branch `bkaranf/data/edgar-tools-only-cleanup` was pushed to `origin` after
authentication and destination verification. Nothing was merged; no pull
request, release, hosted deployment, or history rewrite was created. The
dashboard link in the handoff is local-only. No SEC network call occurred
during Phases 6, 7, or this wrap-up.
