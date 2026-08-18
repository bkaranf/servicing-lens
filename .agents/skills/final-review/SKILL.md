---
name: final-review
description: Perform a fresh-context final review of Servicing Lens changes for SEC compliance, financial correctness, scope, tests, dependencies, and UI regressions.
---

# Final review

Use this skill before declaring a repository change complete.

- Start from the changed-file list and acceptance criteria, then inspect
  `git diff --check`, `git status --short`, and the complete diff without relying
  on earlier conclusions.
- Search the changed surfaces and their callers for acquisition or scope drift:
  `rg -n 'api\.edgar\.tools|requests|httpx|EDGAR_IDENTITY|MSD_SEC_USER_AGENT|float\(|eval\(|exec\(' src tests config docs pyproject.toml`.
  Confirm provenance, exact numeric paths, missingness, and read-only routes.
- Reconcile `README.md`, `AGENTS.md`, `docs/README.md`, active policy/data-model
  docs, configuration, and tests. Check that deleted files have no links or
  imports and that generated artifacts are changed only by their owner. Search
  changed modules for unused imports, orphaned call sites, unreachable branches,
  and obsolete compatibility paths before accepting that dead code is gone.
- Review focused behavior with `tests/unit_tests/test_api_ui_contract.py`, the
  relevant acquisition/financial tests, and dependency declarations in
  `pyproject.toml` plus `uv.lock`. Inspect rendered UI expectations where
  templates or static assets changed.
- Run the repository gate from `QUALITY_GUIDE.md` (including locked sync, Ruff,
  format, MyPy, Pytest coverage, doctor, migrations when applicable), then
  inspect the final diff and patch hygiene. Report blockers and residual risks;
  do not silently repair unrelated findings.
