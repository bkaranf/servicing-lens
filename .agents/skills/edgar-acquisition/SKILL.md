---
name: edgar-acquisition
description: Implement or review Servicing Lens SEC acquisition through edgartools, including identity, bounded retrieval, document provenance, and offline replay.
---

# EDGAR acquisition

Use this skill when changing or auditing the SEC acquisition lane.

- Start at `src/mortgage_servicing_dashboard/edgartools_adapter/` and the
  `edgar_tools*.py` modules. Search identity and provider references with
  `rg -n 'EDGAR_IDENTITY|MSD_SEC_USER_AGENT|edgartools|api\.edgar\.tools'
  src tests config docs pyproject.toml`.
- Require `EDGAR_IDENTITY` before any live company, filing, attachment, or XBRL
  call. Reach only official SEC resources through public core `edgartools` APIs
  and one centralized lane. Keep aggregate traffic below nine requests per
  second, reuse caches, and bound retries and backoff. Missing identity, an SEC
  block/rate limit, or a public-API gap is a stop condition, not a fallback to a
  different client or host.
- Preserve accession, CIK, form, filing date, report period, document name,
  locator, retrieval metadata, library version, byte length, and SHA-256 in the
  evidence records. Keep raw XBRL fact text, units, contexts, and source identity
  until deterministic normalization has completed.
- Put offline replay material under `tests/fixtures/edgartools/`,
  `tests/fixtures/xbrl/`, and the appropriate
  `config/recorded_evidence/` manifest. Exercise the existing adapter and
  retention tests before any explicitly authorized live smoke test; normal tests
  must not open a socket or require an identity.
