# Servicing Lens agent instructions

## Working approach

- Choose the simplest robust design that satisfies the current goal.
- Do not add frameworks, providers, services, abstraction layers, deployment
  systems, authentication, remote tracing, or security machinery for hypothetical
  needs.
- Keep changes narrow. Fix unrelated issues only when they block the active goal
  or are small, obvious, and safely covered by tests. Otherwise record them.
- Read the README and only the documentation relevant to the files being changed.
- Never add an agent name as a commit co-author.
- Do not manually edit generated files, retained evidence bytes, or `uv.lock`.
  Use the tool that owns each generated artifact.

## Product boundary

- Servicing Lens is a local, read-only comparison application using public SEC
  data for current SEC registrants with material mortgage-servicing exposure.
- Product data may come only from SEC-hosted filings, XBRL facts, and SEC-filed
  exhibits acquired through `edgartools`.
- `edgartools` is the sole SEC acquisition library. Use only its core company,
  filing, attachment, and XBRL functionality.
- Do not use hosted `api.edgar.tools`, edgartools MCP or AI features, another SEC
  client, web scraping, FFIEC, FR Y-9C, agency data, issuer websites, or paid data.
- The application runtime is deterministic and non-agentic. Do not add LangChain,
  Deep Agents, model-provider wiring, or model-authored numeric paths.

## Financial authority

- Deterministic code owns all financial values, normalization, formulas,
  validation, reconciliation, revisions, and comparability.
- Use `Decimal` and SQL `NUMERIC` for money, balances, UPB, rates, and derived
  values. Never publish authoritative binary floating-point values.
- Never estimate, interpolate, infer, or fabricate a financial value.
- Missing disclosure remains `NOT_DISCLOSED`. Ambiguous or conflicting facts do
  not publish.
- Preserve legal entity, reporting scope, fiscal period, unit, scale,
  methodology, and source identity.
- Never blend parent, bank, subsidiary, segment, predecessor, successor,
  portfolio, or subservicer facts merely because they share a corporate family.
- Every published value must retain its CIK, accession, form, filing date, report
  period, document name, SEC URL, locator, retrieval time, `edgartools` version,
  byte length, and SHA-256 where document bytes are used.

## SEC access

- Live SEC access requires `EDGAR_IDENTITY` from the environment.
- Fail before opening a socket when the identity is missing or invalid.
- Never create a fallback identity, commit the identity, or expose it in logs,
  fixtures, screenshots, reports, or generated artifacts.
- Use one centralized SEC acquisition lane across all workers.
- Never exceed nine SEC requests per second in aggregate.
- Cache and reuse responses. Use bounded retries and backoff.
- Stop and report when SEC access is blocked or repeatedly rate-limited.

## Testing and delivery

- Reproduce defects with the smallest test that proves the failure.
- Use E2E tests for end-user workflows, not as the default for parser defects.
- New behavior requires deterministic offline tests.
- Normal tests must remain network-free and socket-blocked.
- Use `uv` for dependency and environment operations.
- Run the complete repository quality gate before declaring work complete.
- Do not merge, force-push, delete unlanded work, or publish a release without
  explicit user approval.
