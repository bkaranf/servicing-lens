---
name: lavish
description: Optionally review a Servicing Lens cleanup plan as a local HTML artifact with Lavish; never publish or upload repository data.
---

# Local HTML review

Use this skill only when a local visual review of a cleanup plan or HTML artifact is requested.

- Keep all generated material under the ignored `.lavish/` directory. The only
  permitted command is `npx -y lavish-axi`, and only for local HTML review
  artifacts such as `.lavish/servicing-lens-cleanup-plan.html`.
- Never run `lavish-axi share`; never upload artifacts or publish public links.
  Do not place `EDGAR_IDENTITY`, secrets, retained raw filing contents, or
  complete financial evidence files in an artifact.
- Do not let an unattended run block on interactive polling or a missing visual
  review. Lavish is optional, is not a Python production dependency, and failure
  to run it must not affect the repository quality gate.
