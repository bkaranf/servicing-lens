# Servicing Lens source and evidence policy

## Purpose

This policy governs discovery, acquisition, retention, parsing, extraction, and
publication for Public Mortgage Servicing Intelligence. A source is not authority
merely because it is public. Published observations require an eligible source,
immutable evidence, an exact locator, resolved entity/period/scope semantics, and
successful validation.

Stage A covers TFC and PFSI for Q3 2025 through Q2 2026. The source-discovery
assessment must verify the actual available filings and documents before a metric
is configured for publication.

## Eligible sources and acquisition

Use official SEC public interfaces and EDGAR documents for:

- submission and filing discovery;
- 10-K, 10-Q, and 8-K filings;
- filed earnings releases and investor presentations;
- inline XBRL and company facts; and
- filing exhibits and amendment chains.

SEC-hosted filings, XBRL facts, and SEC-filed exhibits acquired through the
public core `edgartools` company, filing, attachment, and XBRL interfaces are the
only eligible product sources. Hosted `api.edgar.tools`, custom SEC HTTP clients,
web scraping, FFIEC, FR Y-9C, agency data, issuer websites, and paid data are not
eligible acquisition paths or publication sources.

Supported live access occurs only through `msi sync` and requires a validated
`EDGAR_IDENTITY` held outside source control. One centralized acquisition lane
serves all workers, remains below nine SEC requests per second in aggregate,
reuses cached responses, and applies bounded retries and backoff. Missing or
invalid identity, blocked SEC access, repeated rate limiting, or a gap in the
public `edgartools` APIs stops acquisition without a fallback provider.

Retain accession, CIK, form, filing date, report period, document name, SEC URL,
locator, retrieval time, `edgartools` version, byte length, and SHA-256 where
document bytes are used. Preserve filing-specific raw XBRL fact text, taxonomy,
unit, scale, decimals, instant/duration period, context, and dimensions until
deterministic normalization and validation complete. Conflicting observations
enter quarantine rather than establishing an implicit precedence.

### Discovery-only material

Search results, search snippets, news, aggregators, third-party transcripts, and
model recollection can help locate a primary document. They cannot support a
published value. Paywalled or license-restricted material is not ingested without
separate documented authority.

Screenshots are never financial evidence. A PDF page image may be an internal
navigation aid, but the observation must resolve to retained document bytes and a
machine-verifiable page/table/text locator.

## Acquisition record

Every acquired document or payload receives:

- immutable source evidence ID;
- source class;
- original URL;
- timezone-aware retrieval timestamp;
- publication or filing timestamp;
- accession and SEC document identity;
- SHA-256 hash of the exact retained representation bytes;
- media type and byte length;
- representation type and capture method;
- parser name and version;
- acquisition pipeline-run ID;
- reporting-entity candidate;
- reporting-period candidate;
- HTTP validators and response status when applicable; and
- retention location.

Retained bytes are immutable and content-addressed. When the acquisition adapter
captures an HTTP response body, those are the original response bytes. A recorded
browser DOM serialization is instead labeled \`RECORDED_RENDERED_DOM\` with its
capture method; it must never be described as the original HTTP response. It may
support deterministic recorded replay only when it is the complete rendered
document from an official filing URL, carries the SEC accession, is hash and
length verified before parsing, and has a locator that resolves in the retained
view. Reacquiring different bytes from the same URL creates new evidence; it never
rewrites the earlier record. Parses, OCR, tables, and XBRL normalizations remain
reproducible derivatives and do not replace an original response when one exists.

Raw document bodies remain outside graph state, model prompts, tool results, logs,
and checkpoints. Those surfaces carry source IDs and bounded metadata references.

## Representation preference

Use the highest-authority usable representation:

1. structured official API or XBRL fact;
2. filed HTML or inline XBRL;
3. deterministic HTML table;
4. machine-readable PDF text; and
5. OCR only when no usable text representation exists.

Representation preference does not permit semantic guessing. A structured fact
with an unresolved context, unit, dimension, period, or reporting entity is
quarantined rather than preferred over a clear filed table.

## Extraction authority

Publication authority follows this order:

1. authoritative structured fact;
2. deterministic table or document parser;
3. controlled manual extraction with evidence and audited review; and
4. LLM-proposed candidate extraction into quarantine.

An LLM result is never a published observation. An LLM candidate must include:

- proposed metric and metric-definition version;
- raw source label and raw value;
- proposed normalized value;
- unit, currency, scale, and precision;
- period, entity, reporting scope, and methodology;
- evidence document ID;
- page, table, section, DOM, or XBRL locator;
- an exact bounded evidence excerpt;
- confidence;
- conflicts and uncertainties; and
- model and prompt versions.

The candidate remains quarantined until deterministic validation or a controlled
human review accepts it. Manual extraction and review use the CLI or authenticated
admin service; direct database edits are prohibited.

## Evidence-to-observation publication gate

A measured observation is publishable only when:

- the source class and document identity are eligible;
- the SHA-256 resolves to retained immutable bytes;
- the locator resolves within those bytes or a reproducible derivative;
- legal entity, reporting entity, reporting scope, fiscal period, instant/duration
  semantics, currency, unit, scale, precision, methodology, and accounting policy
  are resolved;
- the metric-definition version permits the source and scope;
- exact numeric parsing and normalization pass;
- required reconciliation and duplicate checks pass;
- no unresolved higher-authority conflict exists; and
- source and observation revision links are complete.

A derived observation additionally requires published compatible inputs, a
versioned deterministic formula, exact input IDs, and a reproducible calculation
trace. Derivation never repairs missing disclosure.

If any required semantic is absent or ambiguous, publication fails closed.

## Public-document boundary

Inputs use explicit classifications:

- \`PUBLIC_CORPORATE_DOCUMENT\`;
- \`PUBLIC_STRUCTURED_FACT\`;
- \`SYNTHETIC_TEST_DATA\`;
- \`RESTRICTED_INTERNAL_DATA\`; and
- \`PROHIBITED_CUSTOMER_DATA\`.

Only the first three are permitted in this repository, with synthetic data limited
to conspicuously labeled tests. Restricted internal data and prohibited customer
data are rejected before persistence or model context.

Typed CIKs, accession IDs, RSSDs, tickers, and hashes are approved public
identifiers. They belong in validated structured metadata and are not rejected
merely for being numeric or identifier-like.

Corporate contact blocks, signatures, email addresses, and telephone numbers are
removed deterministically from any bounded model excerpt. Secrets and credentials
remain blocked. Borrower, customer, payment, account, authentication, and other
nonpublic personal data are prohibited.

## Replay, freshness, and failure behavior

- A cached replay performs discovery-independent parsing through publication with
  sockets disabled.
- Same bytes plus the same parser, mapping, metric, and code versions produce the
  same semantic observation set.
- Conditional retrieval or a later filing can create a new evidence/revision
  chain without destroying prior history.
- A stale or failed source affects only its dependent observations; it cannot make
  prior data appear current.
- Pipeline status distinguishes source not checked, unavailable, fetched, parsed,
  quarantined, validated, published, stale, and failed.
- Logs contain safe IDs, versions, counts, timings, and error codes, never raw
  bodies, excerpts, credentials, or unpublished numeric candidates.
