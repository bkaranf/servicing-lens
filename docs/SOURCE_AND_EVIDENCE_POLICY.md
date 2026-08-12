# Source and evidence policy

## Purpose

This policy governs discovery, acquisition, retention, parsing, extraction, and
publication for Public Mortgage Servicing Intelligence. A source is not authority
merely because it is public. Published observations require an eligible source,
immutable evidence, an exact locator, resolved entity/period/scope semantics, and
successful validation.

Stage A covers TFC and PFSI for Q3 2025 through Q2 2026. The source-discovery
assessment must verify the actual available filings and documents before a metric
is configured for publication.

## Source classes and hierarchy

### SEC sources

Use official SEC public interfaces and EDGAR documents for:

- submission and filing discovery;
- 10-K, 10-Q, and 8-K filings;
- filed earnings releases and investor presentations;
- inline XBRL and company facts; and
- filing exhibits and amendment chains.

The acquisition client must send a configurable, descriptive User-Agent with a
real contact held outside source control. It must use bounded concurrency,
conservative rate limiting below the SEC's published maximum, bounded retries with
exponential backoff and jitter, caching, and conditional requests where supported.
Permanent errors do not retry indefinitely.

Only the controlled SEC adapter may issue SEC HTTP requests. Models, LangChain
tools, graph nodes outside acquisition, and Deep Agents have no generic or direct
SEC network access.

### Issuer investor-relations sources

Use an issuer's investor-relations material only when:

- the material is not available as a filed SEC exhibit; or
- it provides material context absent from the filed version.

Retain the original URL, retrieval time, content hash, media type, and document
version. A filed version has precedence for a published fact. An unfiled IR
document can supplement context but cannot silently overwrite or supersede a filed
fact.

### Bank regulatory sources

Adapters cover:

- FFIEC Call Reports;
- applicable FR Y-9C schedules; and
- National Information Center institution attributes, relationships, and
  transformations.

Regulatory facts remain attached to their actual reporting entity and reporting
scope. A depository institution, bank holding company, SEC registrant, reporting
segment, and servicing subsidiary remain distinct even when they share a
corporate family. SEC and regulatory values are not blended based on name or
ownership alone.

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
- accession or regulatory identifier when applicable;
- SHA-256 hash of the exact original bytes;
- media type and byte length;
- parser name and version;
- acquisition pipeline-run ID;
- reporting-entity candidate;
- reporting-period candidate;
- HTTP validators and response status when applicable; and
- retention location.

Original bytes are immutable and content-addressed. Reacquiring different bytes
from the same URL creates new evidence; it never rewrites the earlier record.
Parses, OCR, tables, and XBRL normalizations are reproducible derivatives and do
not replace original evidence.

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
