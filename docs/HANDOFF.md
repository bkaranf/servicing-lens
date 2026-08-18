# Continuation Handoff

## Mission status

Implementation phases 0–7 are complete and locally green. The mission is not
complete until six fresh-context final critics, their bounded repairs if any,
the completion audit, and the authorized push are complete.

## Branch and checkpoints

- Branch: `bkaranf/data/edgar-tools-only-cleanup`
- Mission start: `f8e7d3758cea6a50c6f20f8ebcd9d5b18a7181cf`
- Product-code checkpoint: `de7d512b7ce0cac6fa103c17af7578ebfb4bd5cd`
- Documentation checkpoint: the commit containing this file, subject
  `docs(repo): add cleanup report and continuation handoff`
- Product checkpoint divergence: 0 behind / 23 ahead of `origin/main`; expect
  0 behind / 24 ahead after the documentation commit if remote metadata is
  unchanged.

Mission commits, oldest first:

1. `8e067fb fix(build): restore cross-version quality gate`
2. `586d241 chore(repo): simplify agent instructions and project guidance`
3. `7954c6f refactor(runtime): remove unused agent framework complexity`
4. `b8e0c6d refactor(acquisition): make edgartools the sole SEC source`
5. `7a85eb3 refactor(metrics): simplify the financial comparison core`
6. `b2d1c81 fix(acquisition): harden public edgartools edge cases`
7. `d121654 feat(universe): onboard ten servicers and register expansion cohort`
8. `82231be feat(app): expose the expanded SEC comparison universe`
9. `de7d512 chore(repo): remove obsolete framework and evidence bloat`
10. The documentation commit containing this handoff.

## Completed phases

- Phase 0: branch/ancestry/divergence/baseline audit and cross-version gate
  repair.
- Phase 1: concise root instructions, six repository skills, ignore and guidance
  cleanup.
- Phase 2: deterministic typed runtime; agent frameworks removed.
- Phase 3: public edgartools 5.48.0 is the sole SEC acquisition source.
- Phase 4: simplified exact financial comparison core.
- Phase 5: parser-derived 5+5 publication plus evidence-vetted 10+10 registry.
- Phase 6: cohort-aware GET-only CLI/API/dashboard and evidence drill-through.
- Phase 7: proven obsolete cleanup, stable Phase 3 identity closure, packaging
  narrowing, documentation correction, and social-image optimization.

See [OVERNIGHT_CLEANUP_REPORT.md](OVERNIGHT_CLEANUP_REPORT.md) for the evidence
ledger and exact results.

## Exact remaining work

1. Verify this documentation commit and a clean worktree.
2. Run six independent, fresh-context, read-only final critics: architecture,
   financial correctness, SEC/security, test/CI, application, and scope. Each
   must report blocker/high/medium/low with accepted/rejected dispositions.
3. Apply only evidence-backed repairs allowed by the original mission, using no
   more than the specified repair rounds, then rerun affected and full gates.
4. Run the completion audit: requirements matrix, clean-checkout/offline replay,
   exact route inventory, dependency/reference/secret/cache/large-file scans,
   migration round trip, generators, wheel install, full tests, coverage, Ruff,
   mypy, and diff checks.
5. Confirm the final report remains accurate after any repair commits.
6. `/root` verifies remote/auth state and performs the authorized push.

Do not declare the entire mission complete before items 1–5 pass.

## Known live and evidence gaps

- ONIT accession `0001628280-24-046004`, primary document
  `onit-20240930.htm`, returned safe `NOT_FOUND`; LDI is the published
  replacement. Do not bypass edgartools with direct HTTP.
- The second 5+5 registry cohort is evidence-vetted, not an eight-quarter
  published cohort.
- No post-Phase-7 live smoke was run. Do not make SEC calls merely to repeat a
  proven gate.
- Retain `config/recorded_evidence` (66,839,747 bytes). Phase 5 fixtures do not
  reproduce its 439 observation IDs/source hashes or 725 evidence links.
- Live caches, runtime databases, `.lavish`, and artifacts must remain ignored.

## Stop conditions

Stop and report evidence rather than weakening the standard if any of these
occur:

- branch ancestry/divergence is unsafe or unexpected user work appears;
- a blocker/high critic finding cannot be repaired inside original scope;
- the exact offline gate remains red after evidence-based repair attempts;
- a required published value loses exact parser-derived provenance or changes
  without an old/new/reason/evidence ledger;
- an SEC block/rate limit or public-edgartools API gap occurs during separately
  authorized live work;
- a proposed cleanup would remove evidence without an exact parser/pipeline
  replay replacement;
- push authentication, destination, or remote state is ambiguous.

Never print `EDGAR_IDENTITY`, introduce direct SEC HTTP, weaken strictness,
rewrite history, merge, rebase, reset, or co-author commits.

## Start and seed the local dashboard

From the repository root in PowerShell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = "$env:LOCALAPPDATA\servicing-lens\handoff-venv"
$env:UV_LINK_MODE = "copy"
uv sync --locked --group dev
uv run msi ingest --phase5-cohort-b --database-url sqlite:///./.msi/servicing-lens.db --runtime-dir .msi
uv run msi validate --database-url sqlite:///./.msi/servicing-lens.db
uv run msi serve --database-url sqlite:///./.msi/servicing-lens.db --runtime-dir .msi --host 127.0.0.1 --port 8000
```

Open the dashboard at <http://127.0.0.1:8000/> and health JSON at
<http://127.0.0.1:8000/api/v1/health>. The first replay should publish 160
Phase 5 cases; a same-database rerun should report all 160 unchanged. These
commands are offline and use the bounded tracked replay fixtures.

## Validation commands

```powershell
git status --short --branch
git log -2 --oneline
git rev-list --left-right --count origin/main...HEAD
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
git diff --check f8e7d3758cea6a50c6f20f8ebcd9d5b18a7181cf..HEAD
```

Last verified result: 649 tests passed, 90.52% coverage, Ruff/format/mypy/lock,
generators, doctor, migration round trip, bounded wheel install, and diff checks
all passed.

## Push status

`PUSH_STATUS: NOT_PUSHED_AS_OF_HANDOFF`

After all final critics and the completion audit pass, `/root` should verify:

```powershell
git remote -v
gh auth status
git status --short --branch
git rev-list --left-right --count origin/main...HEAD
```

Then, only if the authenticated destination and branch are correct:

```powershell
git push origin bkaranf/data/edgar-tools-only-cleanup
```

Update the push-status line and the report's publication statement if another
tracked documentation commit is authorized after pushing; otherwise report the
actual pushed commit out of band without rewriting history.

## First next action

Run `git status --short --branch` and `git log -2 --oneline`, confirm the two
local checkpoint commits and clean worktree, then dispatch the six final critics
against the same immutable HEAD.
