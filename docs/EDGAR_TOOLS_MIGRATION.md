# Historical hosted EdgarTools REST migration report

> **Historical and non-authoritative.** D-019 superseded this hosted-provider
> attempt. Active acquisition rules are in
> [SOURCE_AND_EVIDENCE_POLICY.md](SOURCE_AND_EVIDENCE_POLICY.md); the product uses
> the open-source `edgartools` package rather than `api.edgar.tools`.

Date: 2026-08-12. Branch: `bkaranf/data/edgar-tools-only-cleanup`.

The intended source boundary is the hosted EdgarTools REST API at `https://api.edgar.tools/v1/`. The non-destructive migration added one host-locked typed client, exact provider-response retention with `EDGAR_TOOLS_API_JSON` / `EDGAR_TOOLS_DOCUMENT_BYTES` labels, and a provider-only discovery workflow exposed as `msi sync`. Numeric definitions, extraction, validation, reconciliation, persistence, revisioning, and publication remain deterministic local responsibilities; no LLM is permitted to influence a financial value.

## Hard-gate result

The Phase 3 shadow gate failed with `EDGAR_TOOLS_CAPABILITY_GAP` and `PARITY_FAILED`:

- Filing discovery and filing detail work for TFC and PFSI.
- Document list and exact document fetch return 404 for governed accessions through both bounded probes and the implemented sync path.
- Structured financials return 403 `TIER_BLOCKED`.
- The provider-only path reproduced 0 of 439 current published observations (216 TFC, 223 PFSI).
- Existing `NOT_DISCLOSED` rows cannot be re-established without a qualified parser checking eligible filing documents; provider failure is reported as `SOURCE_NOT_AVAILABLE_VIA_PROVIDER` instead.

The old implementation, tracked evidence, dependencies, database schema, and published data therefore remain intact. Phase 4 cleanup, push, and draft-PR release work were not performed.

## Verification

- Default socket-blocked suite: 344 passed, 1 skipped, 90.59% branch coverage.
- Ruff, format check, strict mypy, lock check, doctor secret redaction, Alembic upgrade/check/downgrade/upgrade, diff check, and tree/tracked/history secret scans passed.
- Exact project installation with `uv sync --locked --group dev` remains blocked on Windows by uv's wheel-path handling for the deeply bundled tracked evidence tree; dependency-only locked synchronization passes. This is recorded as `QUALITY_GATE_FAILED`, not hidden as success.
- Full ignored reports and the 439-row parity table are under `artifacts/edgar-tools-migration/`.

## Smallest unblock action

Have EdgarTools enable or repair the documented filing-document list and exact-fetch routes for the governed TFC and PFSI accessions under the configured account, then rerun Phases 2 and 3. Structured-financial access may also need provisioning, but it cannot replace document retrieval for issuer-specific servicing disclosures. Cleanup remains prohibited until the rerun reaches exact 439-of-439 parity with zero unresolved conflicts.
