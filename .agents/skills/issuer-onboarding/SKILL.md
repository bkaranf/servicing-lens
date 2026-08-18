---
name: issuer-onboarding
description: Add or assess a mortgage-servicing issuer in Servicing Lens with verified SEC identity, scope, filing coverage, and deterministic mappings.
---

# Issuer onboarding

Use this skill when adding an issuer or reviewing its eligibility for the governed universe.

- Verify the current SEC registrant, legal name, ticker, CIK, filing activity,
  and material servicing exposure through the approved acquisition path. If live
  verification is not authorized or identity is unavailable, record the gap
  instead of substituting an outside source.
- Keep bank/nonbank classification, legal entity, reporting entity, segment,
  portfolio, and predecessor/successor scope explicit. Capture corporate-action
  effective dates rather than combining pre-close and post-close observations.
- Update only the declarative identity and source maps in `config/universe.yaml`,
  `config/financial_fields.v1.yaml`, `config/xbrl_concepts.yaml`, and the
  issuer-specific config path when a mapping is genuinely required. Prefer the
  common acquisition, evidence, XBRL, and parser paths over a custom pipeline.
- Check the latest annual filing and latest eight fiscal quarters for qualifying
  forms and evidence. Publish an exact value only when entity, period, unit,
  scale, methodology, and locator agree; otherwise retain `NOT_DISCLOSED` and
  document the reason.
- Prove the issuer through targeted tests such as
  `tests/unit_tests/test_phase3_*`, `tests/unit_tests/test_edgartools*`, and the
  repository readiness command `uv run msi doctor --json` before expanding the
  cohort. Check API coverage, dashboard exposure, evidence drill-through, and
  idempotent reruns as part of acceptance.
