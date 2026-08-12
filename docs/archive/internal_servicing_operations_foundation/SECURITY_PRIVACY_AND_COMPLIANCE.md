# Security, privacy, and compliance guardrails

Baseline review date: 2026-08-11

## Status and use

This is a control-design and issue-spotting baseline, not legal advice, a compliance opinion, or a complete control library. Applicable requirements depend on entity role, loan/product, investor/insurer/guarantor, jurisdiction, borrower/case facts, servicing agreement, court order, consent order, and effective date. Compliance/legal counsel and accountable business owners must map, interpret, approve, and test requirements before real-data or production use.

All development samples and fixtures are **SYNTHETIC** and must be labeled **SYNTHETIC DATA — NOT FOR SERVICING USE**. Real borrower data and live model calls are outside the current approval boundary.

## Mandatory human-review rule

An authorized, trained human using current source records and approved procedures must review every payment, escrow, default/loss-mitigation, bankruptcy, foreclosure, and borrower-communication decision. The dashboard, LangChain agent, LangGraph, or Deep Agent may not make, approve, execute, schedule, prioritize, or silently influence those decisions.

| Decision area | AI/dashboard may later assist with | Prohibited autonomous behavior | Minimum external human control |
| --- | --- | --- | --- |
| Payment | Explain a sourced event; identify an approved deterministic exception; assemble source links | Initiate/cancel payment; post/reverse/reapply/refund; resolve suspense; assess/waive fee; quote payoff/reinstatement; decide effective date | Payment-authorized reviewer validates ledger, amount, dates, source rules, and dual control where required; action and reconciliation occur in system of record |
| Escrow | Summarize sourced balance, analysis/disbursement metadata, and data gaps | Calculate/finalize analysis; disburse; change payment; decide shortage/surplus/waiver; procure/cancel insurance | Escrow-authorized reviewer validates tax/insurance/vendor records, applicable rules, amounts, notices, and posting |
| Default/loss mitigation | Summarize sourced status, aging, and approved policy passages | Rank borrowers for treatment; decide contact, completeness, eligibility, option, waterfall, offer, denial, appeal, or outcome | Authorized specialist completes case-specific review under current program/investor/legal rules and records rationale/approval in authoritative workflow |
| Bankruptcy | Display separately sourced case assertions and conflicts | Interpret stay, discharge, reaffirmation, claim, plan, contact permission, or deadline; prepare/file legal document | Authorized bankruptcy specialist/counsel validates court docket, orders, case facts, and approved action |
| Foreclosure | Display sourced stage/hold/counsel facts | Refer/advance; determine first notice or filing; schedule sale; instruct counsel/vendor; bid; evict; calculate legal deadline | Authorized foreclosure specialist and counsel validate jurisdiction, protections, loss mitigation, title/case facts, investor authority, holds, notices, and approvals |
| Borrower communication | Retrieve approved template/policy and draft internal review copy in a later approved phase | Choose recipient/channel/timing; state amount/status/rights/deadline; make promise/threat/legal claim; send/publish | Authorized reviewer validates identity, authority, facts, consent/preferences, language/accessibility, required content, template/version, timing, suppressions, and delivery channel before release |
| Credit furnishing/disputes | Show sourced furnishing/dispute status and data-quality evidence | Furnish/update/delete tradeline; decide dispute result; generate consumer response | Authorized furnisher/dispute reviewer performs reasonable investigation and records source evidence in approved system |

A LangGraph/Deep Agents interrupt is only a technical pause. Resume must not be treated as compliant approval unless a separately designed control verifies reviewer identity/role, current entitlement, sufficient evidence, explicit decision scope, freshness, policy version, separation of duties, audit record, and target-system outcome. The current architecture contains no high-impact action tools even after a resume.

## Regulatory and contractual issue map

Use the official source and current codified text when completing the applicability register; links below are navigation aids. Proposed rules and informal guidance are not treated as final requirements without counsel review.

| Area | Primary reference | Dashboard control implication |
| --- | --- | --- |
| Federal mortgage servicing | CFPB [mortgage servicing rules and resources](https://www.consumerfinance.gov/compliance/compliance-resources/mortgage-resources/mortserv/) | Map applicable Regulation X/Z requirements by loan/entity; preserve accurate records, source deadlines, notices, and procedural protections; dashboard is not deadline authority |
| General servicing policies | Regulation X [12 CFR 1024.38](https://www.consumerfinance.gov/rules-policy/regulations/1024/38/) | Accurate/timely information, continuity, oversight, transfer, record, and loss-mitigation objectives inform data lineage, reconciliation, and controlled operations |
| Escrow | Regulation X [12 CFR 1024.17](https://www.consumerfinance.gov/rules-policy/regulations/1024/17/) and servicing provisions | Treat analysis, cushion, shortage/surplus, disbursement, notices, and force-placed insurance as controlled sourced processes, not model calculations |
| Transfers, force-placed insurance, RFI/NOE | Regulation X [12 CFR 1024.33–.37](https://www.consumerfinance.gov/rules-policy/regulations/1024/) | Source authority/transfer history, payment protection, evidence, cases, acknowledgements/responses, and exceptions require governed effective-date logic |
| Early intervention, continuity, loss mitigation | Regulation X [12 CFR 1024.39–.41](https://www.consumerfinance.gov/rules-policy/regulations/1024/39/) | No model determination of applicability, contact, completeness, evaluation, appeal, or foreclosure protection; use authoritative statuses and human review |
| Payment crediting, payoff, periodic statements | Regulation Z [12 CFR 1026.36 and .41](https://www.consumerfinance.gov/rules-policy/regulations/1026/) | Payment treatment, payoff statements, fees, and statement content/timing remain outside dashboard calculations and communications |
| Debt collection | CFPB [Regulation F, 12 CFR 1006](https://www.consumerfinance.gov/rules-policy/regulations/1006/) and FDCPA | Determine entity/debt/communication applicability; enforce contact restrictions, representation accuracy, disputes, validation, and retention in authoritative channels |
| Credit reporting | CFPB [FCRA resources](https://www.consumerfinance.gov/compliance/compliance-resources/other-applicable-requirements/fair-credit-reporting-act/) and Regulation V | Accuracy, integrity, disputes, identity theft, and furnishing are separate controlled processes; no model or dashboard write-back |
| Fair lending/equal housing | CFPB [ECOA/Regulation B resources](https://www.consumerfinance.gov/compliance/compliance-resources/other-applicable-requirements/equal-credit-opportunity-act/) and HUD [Fair Housing rights and obligations](https://www.hud.gov/program_offices/fair_housing_equal_opp/fair_housing_rights_and_obligations) | Test access, service, prioritization, loss-mitigation outcomes, language/accessibility, models, and proxies for unequal treatment; segregate compliance analytics from operational queues |
| Unfair, deceptive, or abusive acts/practices | Consumer Financial Protection Act and current supervisory/enforcement authority | Do not misstate amount, status, source, certainty, rights, deadlines, consequences, or model capability; design correction/escalation and evidence visibility |
| Military protections | DOJ [SCRA rights guide](https://www.justice.gov/servicemembers/know-your-rights-guide-servicemembers-civil-relief-act) | SCRA status is specially handled; rate, judgment, foreclosure, and contact implications require authorized review and current authoritative verification |
| Bankruptcy | U.S. Bankruptcy Code, Federal Rules of Bankruptcy Procedure, court orders/local rules, including current Rule 3002.1 where applicable | Treat court/counsel feed as restricted; no stay/discharge/deadline interpretation; preserve case-specific holds, filings, notices, and legal review |
| Privacy and information security | GLBA privacy requirements and FTC [Safeguards Rule](https://www.ftc.gov/legal-library/browse/rules/safeguards-rule), plus applicable regulator/state rules | Written risk-based security program, service-provider oversight, minimum data, access, encryption, monitoring, testing, response, and disposal |
| Electronic payments/communications | EFTA/Regulation E, TCPA and implementing rules/orders, E-SIGN, CAN-SPAM where applicable, state communication/recording laws | No payment or outbound channel in baseline; future consent, authorization, revocation, timing, content, evidence, and error controls require counsel-approved design |
| Accessibility | Applicable disability/access laws and [WCAG 2.2](https://www.w3.org/TR/WCAG22/) Level AA-oriented target | Complete workflows, charts/tables, authentication, timeouts, errors, financial/legal review, and documents must be usable with assistive technology |
| Sanctions and transaction controls | Treasury/OFAC [compliance framework](https://ofac.treasury.gov/media/16331/download) if applicable | Read-only portfolio analytics does not replace screening; any future financial/legal action must use the approved sanctions control environment |
| State, investor, insurer/guarantor, contract, court, consent order | Current authoritative requirements for in-scope population | Maintain applicability/effective-date matrix; stricter or additional requirements may control; generic prompts cannot encode them implicitly |

At each release, counsel/compliance records requirement owner, citation/version/effective date, applicability dimensions, system/process owner, control, evidence, test frequency, issue/exception process, and change-monitoring source.

## Data classification and minimization

| Class | Examples | Model/checkpoint baseline |
| --- | --- | --- |
| Public | Published regulations, approved public product documentation | Allowed only through approved sources and prompt-injection controls |
| Internal | Synthetic aggregate definitions, internal technical metadata without borrower facts | Allowed when task-required and approved |
| Confidential | Portfolio strategy, vendor/investor terms, nonpublic aggregates, internal case procedures | Model prohibited until provider/use-case approval and fine-grained retrieval policy |
| Restricted NPI/PII | Name, address, email/phone, full loan/account identifiers, financial history, tax/insurance facts, case narratives | Excluded from current model, logs, traces, checkpoints, stores, and general retrieval |
| Restricted-special | SSN/TIN, credentials, auth factors, bank/card data, credit reports, legal/medical/military indicators, protected-class data | Do not ingest without explicit necessity and specialized controls; never expose to general model context |

Pseudonymous loan tokens and masked values remain sensitive if linkable. Avoid collecting a field merely because a source provides it. Derive authorized aggregates before the presentation/model boundary, suppress small cohorts, and enforce purpose/field restrictions server-side.

Synthetic data must be generated independently rather than copied, masked, perturbed, or learned from production records. Synthetic does not mean unrestricted: label it, keep it out of external demos without approval, and avoid realistic identity/contact/payment credentials.

## Identity, authorization, and session controls

- Federated workforce identity with phishing-resistant MFA as required by policy; no local production passwords.
- Deny-by-default policy based on subject, role, tenant, portfolio, purpose, environment, field group, action, and resource classification.
- Authorization enforced at service/query/tool/retrieval layers before aggregation and cache; UI hiding is not control.
- Separation of duties for administrative, data, compliance, and any future action approval; administrators receive no routine content access.
- Short-lived sessions/tokens, secure cookies or equivalent, CSRF defense where relevant, reauthentication for sensitive functions, and clean logout/revocation.
- Break-glass access requires incident/case reference, narrow scope, short duration, approval, enhanced logging, retrospective review, and no model propagation.
- Periodic access certification, prompt revocation, joiner/mover/leaver automation, service identity inventory, and nonhuman key rotation.
- Opaque IDs in URLs and logs; object-level authorization on every request prevents insecure direct-object reference.

## Application and infrastructure controls

- Environment, tenant, data, secrets, keys, checkpoints/stores, caches, queues, and observability are isolated. Nonproduction uses synthetic data.
- Encrypt approved data in transit and at rest with governed key management, rotation, backup/restore, and access separation.
- Credentials come from a secret manager or protected local development environment, never source, fixtures, prompts, tool arguments/results, URLs, browser storage, checkpoints, or logs.
- Default-deny network egress; allowlist exact model/provider/source endpoints after review; block metadata services, loopback/private-address SSRF, redirect abuse, and untrusted URL fetching.
- Parameterized governed queries or typed repositories only; no model/user-generated SQL, `eval`, unsafe deserialization, or shell execution.
- Strict schemas, size/type/range/code validation, output encoding, content security policy, safe file parsing, malware scanning where relevant, and dependency/supply-chain controls.
- Immutable/reproducible builds, pinned/resolved dependencies, provenance, vulnerability/secret/license scanning, signed deployment artifacts where supported, and protected deployment approvals.
- Resource quotas, timeouts, cancellation, circuit breakers, bounded concurrency, retry budgets with jitter, idempotency, and fail-closed degraded states.
- Backups, recovery objectives, restore tests, region/residency, high availability, and incident ownership require production approval.

## LangChain, LangGraph, and Deep Agents controls

### Common model boundary

- Provider-neutral and live calls off by default, as in the current foundation.
- Provider contract must prohibit training/secondary use as required, define retention/subprocessors/location/deletion/security/incident terms, and support approved access/egress controls.
- Fixed versioned system policy; structured validated outputs; minimum temperature/variance appropriate to the use; explicit as-of/source citations and uncertainty.
- Deterministic services compute all metrics and servicing facts. Model output cannot overwrite canonical facts or silently affect filtering, queue order, entitlement, or alerts.
- Input and retrieved text are untrusted. System/developer policy and server-side permissions cannot be changed by documents, users, tools, or subagents.
- No raw borrower data, secrets, direct identifiers, free-form case narratives, or unrestricted documents in prompts. Pattern filters are defense-in-depth, not proof of de-identification.

### LangChain tools and retrieval

- Typed, allowlisted, read-only, purpose-specific tools that call the authorized service layer.
- Per-tool authorization, allowed fields/dimensions, record/byte/time limits, deterministic sorting, safe errors, classification, provenance, and audit metadata.
- No generic database, HTTP, browser, email/SMS, payment, legal, file-write, code-execution, or source-update tool.
- Retrieval filters access before search, retains document/section/version/effective-date citations, and treats embedded instructions as hostile data.
- Validate tool outputs before model context and validate model references against actual tool results before display.

### LangGraph state, checkpoints, and HITL

- Explicit typed state allowlist; opaque random thread IDs; no identifiers in checkpoint keys.
- Checkpoint/store off until approved. When used: encrypted persistent backend, tenant/thread ownership, authentication on resume, retention/deletion, backup classification, and cross-environment isolation.
- Every resume rechecks current identity, entitlement, policy, source freshness, and pending interrupt; a stolen/replayed resume token fails.
- Interrupts show exact proposed analysis/draft, sources, limitations, and allowed choices. No ambiguous “continue” for a high-impact context.
- Pre-interrupt nodes are side-effect-free/idempotent because a resumed node may re-execute.
- Human review captures reject/edit/escalate paths. It does not transform a prohibited write into an allowed action.

### Deep Agents

- Disabled until a bounded use case, threat model, evaluation set, and data corpus are approved.
- Prefer a small explicit LangGraph when steps are known; use a Deep Agent only for justified planning/context/delegation complexity.
- Per-task ephemeral workspace, read-only corpus, filesystem permission allowlist, no write/delete/execute, and no arbitrary network/MCP by default.
- Named subagent roles with narrower tools/data than the parent; bounded nesting, count, concurrency, tokens, time, and cost; cancellation and safe partial result.
- No persistent personal/borrower/case memory, self-modification, skill/prompt update from unreviewed usage, or cross-task state leakage.
- Final output includes plan/provenance, every source used, unresolved conflicts, and limitations; human analyst verifies it.
- Never deploy as an autonomous servicing, collection, loss-mitigation, legal, communications, furnishing, or payment agent.

## Threats and required tests

| Threat | Prevent/detect requirements |
| --- | --- |
| Cross-tenant/portfolio exposure | Negative object/row/aggregate/cache tests; entitlement before query; small-cell suppression; randomized token authorization |
| Prompt injection/retrieval poisoning | Instruction hierarchy, corpus approval/checksum/version, content isolation, tool authorization outside prompt, injection test suite, suspicious-source marking |
| Sensitive-data exfiltration | Classification/minimization, DLP/pattern and semantic tests, blocked tools/egress, output validation, canary tests, redacted telemetry |
| Hallucinated amount/status/deadline | Deterministic source tools, citations, claim-to-evidence validation, explicit unknown/conflict response, golden/negative evaluations |
| Tool abuse/parameter escalation | Strict schemas/enums, server scope override, budgets, no generic tools, authorization per call, rate limits, safe errors |
| Bias or unequal treatment | Exclude protected/proxy data from operations; representative scenario tests; performance/error/outcome monitoring; compliance-segregated analysis and review |
| Checkpoint replay/state leakage | Opaque thread ID, authenticated ownership, resume nonce/version, TTL, encryption, reauthorization, cross-thread/environment isolation tests |
| Deep Agent runaway/delegation escape | Tool/data inheritance tests, subagent-specific policy, nesting/concurrency/token/time budgets, sandbox, kill switch, complete delegated-task audit |
| Stale/partial/conflicting data | Freshness and reconciliation gates, explicit degraded state, fail closed for material facts, source assertions shown separately |
| Misleading UX/automation bias | Synthetic/AI labels, source/definition/limitations adjacent, no default preselection of high-impact outcomes, reject/edit/escalate options, user testing |

## Fair lending, model risk, and quality governance

- Maintain inventory and accountable owner for every model, prompt, graph, node, tool, retriever, corpus, metric, feature, threshold, and version.
- Write an intended-use/prohibited-use statement and independent validation plan before model enablement.
- Evaluate factuality, citation correctness, refusal, access isolation, privacy leakage, injection resistance, tool selection/arguments, determinism where required, subgroup performance, accessibility, and human reliance.
- Test representative servicing states without using protected-class attributes to assign operational treatment. Approved fair-lending analysis is segregated, privacy-protected, statistically reviewed, and not routed back into individual decisions.
- Establish change thresholds requiring revalidation for model/provider, prompt, tool, graph, data, definition, corpus, policy, dependency, or UI changes.
- Provide kill switch, rollback, previous approved version, incident criteria, correction process, affected-output identification, and user notification path.
- Sample human-reviewed output for overreliance, rubber-stamping, inconsistent treatment, and unsupported facts; do not grade workers on harmful speed/collection proxies.

## Borrower-communication control (future only)

Outbound delivery remains absent. If proposed later, design a separate, counsel-approved pipeline:

1. Retrieve only the applicable approved template and current authoritative facts.
2. Deterministically populate required fields and validate amount/date/status/source consistency.
3. Apply identity/authority, consent, channel, time/place, frequency, language/accessibility, cease-contact, counsel, bankruptcy, SCRA, dispute, and jurisdiction rules.
4. Show a versioned review packet with recipient/channel, exact final content, enclosures, required disclosures, evidence, and limitations.
5. Obtain explicit authorized human approval (and second control where required) in the communication system.
6. Send through the approved channel using idempotency and immutable template/content hash.
7. Record delivery/failure, reconcile, handle revocation/response, and retain evidence under policy.

The model may not infer consent, choose a template, translate legally material language without approved validation, alter a mandatory disclosure, or send because a graph was resumed.

## Logging, tracing, and audit

Application logs and remote traces are different from the compliance audit record. Remote tracing is disabled by default. Before any LangSmith or other tracing with nonpublic content, approve vendor/data terms and implement verified client/server redaction or strict metadata-only capture.

Allowed operational telemetry is typically: random request token, environment, route/tool/graph/node name, versions, timing, success/safe error code, bounded counts, and classification. Do not log prompt/response bodies, tool arguments/results, retrieved chunks, direct/pseudonymous loan identifiers, filters that reveal a person, secrets, headers/tokens, SQL, checkpoint state, case narrative, or stack traces to user-visible channels.

Security/compliance audit events should cover:

- authentication, authorization decision, denial, break-glass, entitlement/configuration change;
- dataset/query/record access at an appropriately minimized resource token and purpose;
- export request/approval/download/expiry/deletion if later enabled;
- model invocation and version, prompt policy version, tool name/version/policy result, graph/node transitions, interrupt/resume/reject/edit/escalate, and Deep Agent/subagent lifecycle;
- source/definition/corpus version, data-quality/freshness state, refusal, safety control, and error; and
- future human decision/action/result/rollback only in the authoritative controlled workflow.

Audit data requires tamper evidence, synchronized time, restricted access, monitoring, retention schedule, legal hold, integrity checks, and periodic review. Minimize content while retaining enough evidence to reconstruct who accessed what category, when, for which approved purpose, under which versions, and with what disposition.

## Retention and disposal

Create an owner/counsel-approved schedule by record class and jurisdiction; do not adopt a generic period from this document. Cover source extracts, canonical data, aggregates, caches, browser/client storage, logs, audit, exports, model inputs/outputs, tool results, retrieval indexes/embeddings, graph checkpoints, stores/memory, Deep Agent workspaces, backups, test artifacts, and deletion tombstones.

Use the shortest justified period, automatic expiry, secure deletion, backup-aging rules, litigation/regulatory hold controls, and evidence of deletion. A model checkpoint/store must never become an unofficial servicing file or a way around recordkeeping, correction, discovery, or deletion obligations.

## Incident and change response

- Detect and triage unauthorized access, cross-tenant exposure, prompt/data leakage, harmful or incorrect output, compromised source/corpus, checkpoint replay, agent escape, stale critical data, reconciliation failure, and dependency/provider incident.
- Immediately support disabling model calls, tools, graph routes, checkpoint resume, Deep Agents, exports, data feed, user/role, and egress independently.
- Preserve approved evidence without spreading restricted content; identify affected subjects, datasets, outputs, reviewers, and downstream use.
- Follow legal/privacy/security notification and borrower correction duties as determined by accountable teams.
- Root-cause, correct source/output/process, validate remediation, monitor recurrence, and obtain approval before re-enable.

## Release gates

### Synthetic MVP

- Prominent synthetic labeling; no production-derived data or realistic identifiers.
- Read-only routes/tools and no outbound connectors.
- Unit/contract/authorization/accessibility/safety tests pass; expected KPIs reconcile exactly to independent fixtures.
- Live model calls, checkpoint/store persistence, remote tracing, and Deep Agents remain disabled unless the phase explicitly introduces a synthetic-only controlled test.
- Human-review notice appears in every payment, escrow, default/loss-mitigation, bankruptcy, foreclosure, and communication context.

### Real-data pilot

- Applicability/control matrix and all production-data readiness checks approved.
- Independent privacy/security/model-risk/fair-lending/accessibility/compliance review and penetration testing complete.
- Field-level minimization/authorization, source reconciliation, redacted telemetry, incident exercises, recovery, retention, and user training validated.
- Read-only, bounded pilot population, monitoring, rollback/kill switches, and daily control review; no high-impact writes or communications.

### Any future action capability

Requires a new architecture decision and separate project authorization. It must define legal authority, deterministic validation, explicit qualified human approval, separation of duties, idempotency, target-system controls, reconciliation, rollback/correction, notices, evidence, monitoring, and independent validation. Human-in-the-loop alone is insufficient.
