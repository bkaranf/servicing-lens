# Data sources and interface plan

## Design intent

The dashboard consumes governed facts through read-only adapters, normalizes them into a versioned canonical model, calculates approved metrics deterministically, and exposes minimum-necessary views through authorized interfaces. No model connects directly to a source database, payment rail, servicing platform, document repository, legal platform, or communication channel.

The first build uses a versioned **SYNTHETIC** fixture only. Real borrower data, credentials, and network integrations are prohibited until the production-data gates in this document and the security baseline are approved.

## Logical flow

```text
approved sources -> read-only adapters -> contract validation/quarantine
                 -> canonical facts + lineage -> deterministic metrics/read models
                 -> authorization/policy API -> dashboard views
                                            -> typed LangChain tools
                                            -> LangGraph orchestration and HITL
                                            -> bounded Deep Agent analysis (later only)
```

Each arrow is an explicit trust boundary with authentication, authorization, schema validation, classification, lineage, quality, rate/size limits, audit metadata, timeout, and failure behavior.

## Three-part agent foundation

The responsibilities follow the official [LangChain](https://docs.langchain.com/oss/python/langchain/overview), [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview), and [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) documentation.

| Component | Responsibility here | Allowed initial/later use | Boundary |
| --- | --- | --- | --- |
| LangChain | Model abstraction, messages, typed tool interfaces, middleware, retrieval composition, structured outputs, and a simple agent loop | Current provider-neutral foundation; later source-grounded read-only questions | No direct database/model-generated SQL, write tool, unrestricted retriever, or authoritative calculation |
| LangGraph | Explicit orchestration runtime for deterministic and model nodes, state transitions, streaming, durable execution, checkpointing, and interrupts | Later reproducible inquiry/research graphs; pause for evidence review or draft review | Graph state is not a servicing system of record; an interrupt is a technical pause, not regulatory approval or authorization to act |
| Deep Agents | Higher-level harness on LangGraph for planning, context management, constrained subagents, and optional virtual filesystem | Later bounded, analyst-initiated research over approved synthetic/de-identified portfolio material | No autonomous regulated decisions; no unrestricted filesystem, code execution, network, memory, delegation, or borrower-level action |

### LangGraph state, persistence, and human-in-the-loop

The official persistence model distinguishes a thread-scoped checkpointer from a cross-thread store. Checkpoints may support resuming a graph and human-in-the-loop interrupts; stores hold application-defined cross-thread memory. Both are off by default in the current foundation.

When introduced:

- Graph state contains only the minimum task state: opaque request/thread token, authorization snapshot reference, synthetic/approved data classification, query plan, bounded tool-result references, citations, policy decisions, and review status.
- `thread_id` is a random opaque identifier, never a loan number, borrower identifier, case number, email, or other business key.
- Restricted content is not persisted merely because it appeared in a request. State schemas use allowlists; serialization tests block unexpected fields.
- Checkpoint and store backends require encryption, tenant isolation, access logging, retention/deletion controls, key rotation, backup classification, incident handling, and environment separation.
- Long-term model “memory” about borrowers or loan cases is prohibited. Approved reusable knowledge comes from versioned policy content, not remembered conversations.
- Interrupt payloads are minimal, JSON-serializable review packages with no unnecessary PII. Resumption revalidates identity, role, portfolio entitlement, policy version, record freshness, and checkpoint ownership.
- Nodes before an interrupt may run again on resume; they must be side-effect-free or idempotent. The read-only baseline has no operational side effects.
- A LangGraph interrupt may collect human review of an analysis or draft, but it does not itself satisfy servicing approval, legal review, dual control, or system-of-record documentation requirements.
- Payment, escrow, default/loss-mitigation, bankruptcy, foreclosure, and borrower-communication decisions remain outside the model and require an authorized human review record in the approved workflow. No action tool is made available simply because a reviewer resumed a graph.

See the official [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) and [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) guidance for runtime semantics.

### Deep Agent fit

A Deep Agent is justified only when a task benefits from bounded multi-step planning or delegated analysis beyond a small explicit LangGraph. Candidate later uses include:

- assemble an internal portfolio-review packet from approved aggregate metrics, definition documents, and source citations;
- compare approved policy or investor-guide versions and identify passages for a specialist to review;
- investigate a data-quality anomaly across allowlisted, de-identified datasets; and
- draft a reproducible analysis plan whose queries are executed by typed deterministic tools.

It is not appropriate for deciding payment application, escrow treatment, collection strategy, application completeness, loss-mitigation eligibility/outcome, bankruptcy treatment, foreclosure advancement, credit furnishing, or what/when/how to communicate to a borrower.

Before enablement, its execution environment must exclude write/edit/delete/execute tools by default; mount only an ephemeral, per-task, read-only approved corpus; disable arbitrary MCP/network access; constrain subagents, recursion, tokens, wall time, result size, and concurrency; isolate tasks; and preserve source citations. Human approval to continue analysis does not grant access or permission to act. See official [Deep Agents permissions](https://docs.langchain.com/oss/python/deepagents/permissions) and [human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop) guidance.

## Candidate source inventory

Names are logical roles, not vendor selections.

| Source role | Candidate facts | Proposed ingestion | Authority/risks |
| --- | --- | --- | --- |
| Core servicing system | Loan terms, balances, due/paid-through dates, servicing status, transaction postings, escrow summary | Read replica/API or governed snapshots | Field-level authority only; history and corrections must be preserved |
| Payment processor/bank files | Attempt, settlement, return, channel, reason, received/effective dates | Signed files/events/API into controlled landing zone | Reconcile lifecycle events; never expose instrument/account data |
| Escrow/tax/insurance vendors | Bills, disbursement status, policy/tax events, exceptions | Governed batch/API | Vendor feed may lag or conflict; unknown is not zero |
| Data warehouse/lakehouse | Conformed history and approved dimensions | Read-only views/snapshots | Convenience layer is authoritative only where owners approve |
| CRM/case/complaint platform | Contacts, complaints, NOE/RFI/dispute cases, owner, stage, source due status | Read-only API/CDC | Narratives may contain unrestricted PII and prompt injection; metadata first |
| Loss-mitigation platform | Application/case events, source completeness/decision status, milestones | Read-only views/events | High-impact statuses must remain attributed and human-owned |
| Bankruptcy/legal/counsel platform | Case, court/counsel events, holds, foreclosure stage, source deadlines | Read-only files/API | Specially restricted; conflicts fail closed; not legal truth by inference |
| Document management | Document type, version, received/sent metadata, approved policy text | Metadata API; content retrieval separately allowlisted | Borrower documents excluded from model corpus by default |
| Investor/insurer/guarantor reference | Portfolio mapping, program/guide version, source codes | Governed reference data | Requirements vary by effective date and contract |
| Identity/authorization service | Subject, role/group, tenant/portfolio entitlements, assurance, session | Signed identity claims and policy decision point | No UI-supplied role is trusted |
| Audit/security platform | Access, policy decision, export, model/tool/graph metadata, security events | Append-only event pipeline | Payload minimization and tamper evidence required |

Credit-bureau furnishing, sanctions screening, payment rails, outbound communication, court filing, and foreclosure vendor command interfaces are explicitly excluded from the read-only architecture.

## Source authority and conflict rules

Create a field-level source-of-truth matrix with effective dates. A general source ranking is unsafe: the core servicing platform may own UPB while a legal platform owns counsel-provided case status.

For every canonical field, record:

- source system and source object/field;
- source record token and source version/change token;
- owner and semantic definition;
- effective/event/as-of, extraction, and ingestion times;
- mapping/transformation version;
- expected freshness and reconciliation rule;
- correction/supersession semantics; and
- fallback source, or explicit “no fallback.”

When authoritative sources conflict, preserve each assertion and open a discrepancy. Do not choose by latest ingestion time, non-null value, majority vote, or model judgment unless a versioned owner-approved rule explicitly applies.

## Canonical entities

All identifiers exposed to the application are opaque tokens. The model excludes direct identity, contact, government ID, bank/card, credential, and unstructured borrower-document fields.

| Entity | Grain | Minimum fields |
| --- | --- | --- |
| `PortfolioSnapshot` | Portfolio and as-of instant | portfolio token, classification, business date/timezone, loan count, UPB/currency, source snapshot, freshness/quality status |
| `LoanSnapshot` | Loan and as-of instant | loan token, portfolio/product/investor categories, servicing status, property-state code if authorized, authoritative source references |
| `LoanTermsSnapshot` | Loan and effective interval | original/current terms, note-rate type/value, maturity, scheduled component tokens, modification source status |
| `BalanceSnapshot` | Loan/component/currency and as-of instant | component code, exact amount, source, quality state; UPB, escrow, suspense, advances remain separate |
| `PaymentEvent` | Immutable source lifecycle event | event token/type, exact amount/currency, received/effective/posted times, source reason/status, related/superseded event token |
| `PaymentException` | Source exception instance | type, opened/resolved times, source status/reason, age rule version, owner queue token |
| `EscrowSnapshot` | Loan and as-of instant | source escrow status/balance, analysis metadata, projected result source, next source disbursement metadata |
| `EscrowEvent` | Analysis/disbursement/vendor event | type, component category, exact amount/currency if authorized, due/effective/posted times, source status |
| `DelinquencySnapshot` | Loan and as-of instant | source/calculated DPD with method, bucket, earliest unpaid due-date token/value if approved, quality flags |
| `LossMitigationCaseEvent` | Case event | case token, source stage/event, occurred/effective/received times, source owner, supersession and quality flags |
| `LegalCaseAssertion` | Case/source/assertion/effective interval | case token, bankruptcy/foreclosure assertion type/value, source/counsel token, hold, freshness, conflict group |
| `CustomerCareCaseEvent` | Case event | case token/type/category, source status, received/acknowledged/responded times, source due-status, owner queue |
| `CommunicationMetadata` | Communication event | communication token, approved template/version, direction/channel category, source sent/delivery time/status, review token; no body by default |
| `DataQualityIssue` | Rule/entity occurrence | rule/version, severity, first/last seen, impacted entity/field tokens, quarantine state, owner/resolution source |
| `MetricObservation` | Metric/dimensions/as-of/version | metric ID/version, value/unit, numerator/denominator, population token, dimensions, lineage set, quality/freshness |
| `SourceLineage` | Canonical assertion | source, record/field token, source version, transform/version, four time semantics, correction chain, classification |

### Event rules

- Prefer immutable events plus corrections/supersession over destructive history updates.
- Preserve source and canonical code; never discard the original code during mapping.
- Use globally unique opaque event tokens and idempotency keys for ingestion.
- Separate event, effective, received, posted, source-update, extraction, and ingestion time.
- Make timezone and business-calendar identity explicit; reject ambiguous local timestamps.
- Use decimals and currency codes for money, ISO-formatted dates/times on interfaces, and controlled units for rates.
- A missing required fact enters quarantine or a degraded state; it is never defaulted to zero/current/no-case.

## Adapter contracts

Every inbound adapter implements the conceptual operations below, regardless of batch, event, or API transport:

1. `discover`: report source contract/version, extract watermark, classification, and expected scope without content leakage.
2. `read`: retrieve a bounded, read-only page/window using an authorized service identity.
3. `validate`: verify signature/checksum where applicable, schema, types, codes, times, referential integrity, and limits.
4. `normalize`: map through a versioned field/code crosswalk while retaining raw source tokens in a restricted landing layer.
5. `publish`: atomically make a complete snapshot/event batch available or quarantine it; no partial silent success.
6. `reconcile`: compare counts/totals/control records with approved tolerance and produce evidence.
7. `observe`: emit allowlisted operational metadata without row content, secrets, or direct identifiers.

Contract evolution uses additive compatibility where possible, explicit deprecation windows, consumer contract tests, replay fixtures, and rollback. A schema change does not automatically authorize a new field to flow to the UI or model.

## Read API plan

The transport may be selected later; these resource contracts remain framework-neutral. All endpoints require identity and policy enforcement. Versioning starts at `/v1`.

| Operation | Purpose | Important response metadata |
| --- | --- | --- |
| `GET /v1/context` | Entitled portfolios, classification, supported dates/filters, freshness/quality | request token, policy version, as-of/timezone, dataset/contract version |
| `GET /v1/metrics` | Bounded approved metric query | metric IDs/versions, values, numerator/denominator, population, lineage, quality |
| `GET /v1/definitions/{metric_id}` | Metric contract | owner/approval, formula, exclusions, versions/effective dates |
| `GET /v1/loans` | Authorized pseudonymous search/filter | opaque tokens only, bounded pagination, active filters, result coverage |
| `GET /v1/loans/{loan_token}` | Minimum-necessary loan read model | field-level lineage, as-of time, source conflicts, authorization projection |
| `GET /v1/loans/{loan_token}/events` | Authorized typed timeline | bounded event types/window, event/effective/ingestion times, correction chain |
| `GET /v1/exceptions` | Role-scoped operational/data exceptions | rule/source status, age method, owner queue, quality/freshness |
| `POST /v1/analysis/query` | Structured read-only analysis request too rich for URL parameters | validated query AST, limits, policy/definition versions; no source mutation |
| `POST /v1/assistant/query` | Later source-grounded assistant invocation | thread/request token, citations, tool evidence, limitations, model/prompt/policy versions |

No `PUT`, `PATCH`, `DELETE`, bulk action, or servicing mutation endpoint exists in the initial application. `POST` query endpoints are computational and side-effect-free except minimized audit events.

### Interface behavior

- Reject unrecognized filters/fields rather than ignoring them.
- Apply authorization before query execution and aggregation; never accept tenant/portfolio scope solely from the client.
- Use stable bounded cursor pagination, maximum date range, row/byte/cell budgets, timeout, and cancellation.
- Include `data_status` such as `current`, `stale`, `partial`, `conflicted`, or `unavailable` and affected fields.
- Use a typed error envelope with safe code, correlation token, retryability, and user-safe message; no SQL, stack, secret, prompt, or restricted record content.
- Cache only after policy projection and include tenant/entitlement, classification, dataset, definition, and locale/timezone in cache identity.

## LangChain tool plan

Tools call the application service layer; they do not bypass it. Proposed read-only tools:

- `get_metric_definition(metric_id, version?)`
- `query_approved_metrics(metric_ids, snapshot, filters, comparison_snapshot?)`
- `search_loan_tokens(filters, page_size, cursor?)`
- `get_authorized_loan_summary(loan_token, snapshot, field_groups)`
- `get_source_lineage(entity_token, field_names)`
- `compare_approved_snapshots(metric_ids, first_snapshot, second_snapshot, filters)`
- `search_approved_knowledge(query, corpus_ids, effective_date, max_results)`
- `get_data_quality_context(entity_or_metric_token)`

Each tool has strict typed input/output, schema version, purpose, authorization, row/byte/time budget, allowlisted fields, deterministic sorting, definition/source citations, classification, and redacted audit metadata. Tool results are untrusted inputs to the model and pass output validation. There is no raw SQL, generic HTTP, arbitrary retriever, file-write, shell/code execution, outbound message, or source mutation tool.

## Retrieval corpus plan

Start with approved public or synthetic documentation only. Later internal content requires corpus owner, classification, effective dates, supersession, access labels, chunk lineage, deletion process, and prompt-injection review.

- Index atomic, versioned sections with title, source URI/token, owner, publication/effective/expiration dates, jurisdiction/product/investor applicability, and checksum.
- Retrieve only after entitlement filtering; post-filtering model results is insufficient.
- Prefer exact policy/definition retrieval over semantic similarity when identifiers or dates are known.
- Show citations to the precise approved version. Never cite a model summary as authority.
- Treat instructions inside retrieved content as data. They cannot override system policy or tool permissions.
- Do not embed/index raw borrower documents, free-form case notes, complaint narratives, credentials, or secrets in the general corpus.

## Synthetic dataset contract

The initial fixture is generated from rules and a fixed seed, not copied, masked, perturbed, or derived from production. Every file/object and record includes `data_classification: synthetic`, `dataset_version`, `generator_version`, and fixed `reporting_as_of` metadata. Screens and exports display **SYNTHETIC DATA — NOT FOR SERVICING USE**.

The fixture contains no names, real-looking contact details, SSNs/TINs, bank/card data, credentials, unstructured borrower content, real addresses, full loan numbers, or values taken from real cases. Use obvious opaque tokens such as `SYN-LOAN-0001`.

Coverage includes:

- current, 1–29, 30–59, 60–89, 90+, and unknown DPD;
- fixed/ARM category, active/transfer/payoff states, multiple portfolio/product/investor categories;
- accepted, returned, reversed, suspense/unapplied, duplicate, and late/out-of-order payment events;
- escrow/no-escrow, analysis due, projected shortage/surplus, disbursement due, and missing vendor facts;
- source-defined loss-mitigation stages, bankruptcy/foreclosure assertions and holds, complaints/NOE/RFI metadata;
- fresh, stale, missing, malformed, contradictory, quarantined, and corrected records; and
- privacy/authorization boundary cases across synthetic tenants/portfolios.

Fixture invariants and expected metric results are stored separately from implementation results so tests detect broken calculations. High-impact statuses are factual synthetic source states only; the fixture includes no “recommended action,” eligibility, legal conclusion, or borrower communication.

## Data-quality release gates

| Gate | Minimum proof before a dataset is queryable |
| --- | --- |
| Contract | Exact schema/semantic version supported; unexpected fields classified and blocked from propagation |
| Integrity | Unique/idempotent keys, valid references, controlled codes, decimal/currency/time validity |
| Completeness | Expected control totals and state-conditional required fields; missing remains explicit |
| Freshness | Source watermark and owner-approved objective; critical stale feed causes degraded/fail-closed views |
| Reconciliation | Counts and approved monetary/metric totals match authoritative controls within signed-off tolerance |
| Isolation | Tenant/portfolio entitlements and field projections pass negative tests before aggregation/caching |
| Lineage | Material facts trace to source, transformation, times, and definition versions |
| Safety | Classification and field policy prevent restricted data from logs, traces, prompts, checkpoints, and exports |

## Production-data readiness checklist

- Named business, data, privacy, security, compliance/legal, model-risk, and platform approvals.
- Field-level source authority matrix and canonical data contracts approved.
- Data protection impact/threat assessments and vendor/model data-use terms complete.
- Nonproduction environments use synthetic data; controlled production support access is defined.
- Identity, tenant/portfolio/field policy, separation of duties, and break-glass controls tested.
- Encryption, secrets, egress, private connectivity, key management, backup, retention/deletion, and recovery controls verified.
- Reconciliation, freshness, quarantine, lineage, correction, and incident runbooks exercised.
- Model, graph, checkpoint/store, Deep Agent, tool, retrieval, prompt-injection, leakage, and human-review evaluations meet approved thresholds.
- High-impact write and communication interfaces remain absent; any future proposal requires a new decision and independent control review.
