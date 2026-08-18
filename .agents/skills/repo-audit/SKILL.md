---
name: repo-audit
description: Audit Servicing Lens dependencies, call sites, documentation, generated artifacts, and cleanup candidates before repository maintenance.
---

# Repository audit

Use this skill when a cleanup or removal decision needs repository-wide evidence.

- Map the owned surfaces with `rg --files src tests config docs proposals`.
  Inventory declared and locked dependencies from `pyproject.toml` and
  `uv.lock`, then inspect entry points, package data, scripts, and generated-file
  ownership before proposing cleanup.
- Search imports, CLI routes, tests, configuration, and documentation with
  `rg -n '<candidate>|<module>|<path>' src tests config docs proposals README.md
  pyproject.toml`. Treat a candidate as dead only after this search also covers
  generated-file and fixture references.
- For large or retained artifacts, use
  `Get-ChildItem -Recurse -File | Sort-Object Length -Descending` and inspect
  manifests before proposing removal. Keep evidence bytes separate from
  narrative summaries and do not edit retained bytes.
- For documentation cleanup, identify duplicated instructions and the
  authoritative replacement, then check links from `README.md`,
  `docs/README.md`, and active policy/data-model files. Preserve `docs/archive/`
  unless its archive contract explicitly changes.
- Record each accepted deletion with its callers, replacement, and evidence.
  Finish with `git diff --check`, the smallest relevant tests, and `git status
  --short`; do not broaden a cleanup because an unrelated finding is interesting.
