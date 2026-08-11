# Phased implementation backlog

Priorities: `P0` blocks the phase, `P1` is required for phase exit, and `P2` is valuable but may move with owner approval. No phase authorizes capabilities listed in a later phase. Every fixture, screenshot, demo, test dataset, and export is explicitly **SYNTHETIC** until the real-data pilot gate is approved.

## Phase 0: Foundation review and decisions

Goal: convert this baseline and the safe package scaffold into an approved implementation contract without building servicing workflows.

| ID | Pri | Backlog item | Acceptance criteria |
| --- | --- | --- | --- |
| FND-001 | P0 | Review product/domain baseline | Product, servicing operations, data, security/privacy, compliance/legal, accessibility, AI/model-risk, and engineering owners record approval or owned gaps; this is not production approval |
| FND-002 | P0 | Confirm application boundary | Application remains isolated under `mortgage_servicing_dashboard/`; no product behavior changes upstream `libs/`; local `uv` commands are documented |
| FND-003 | P0 | Record stack ADR | Dashboard/API technology, dependency rationale, supported Python/runtime, local start path, accessibility approach, and deployment assumptions are recorded by superseding ADR-013 |
| FND-004 | P0 | Assign definition/source owners | Every Phase 1 metric/field has business/data owner, proposed population, source, as-of semantics, and independent expected result |
| FND-005 | P1 | Build traceability matrix | Each Phase 1 requirement maps to code, tests, UI/API evidence, security control, and owner; gaps fail phase exit |
| FND-006 | P1 | Create threat/privacy assessment | Covers synthetic/real transition, tenant isolation, model/tool/graph/Deep Agent boundaries, checkpoints, logs/traces, dependencies, browser, and incident kill switches |

## Phase 1: Synthetic read-only vertical slice

Goal: start the dashboard with one complete, testable path from versioned synthetic facts through deterministic metrics and an authorized read API to an accessible overview and loan detail. Establish all three agent layers safely, but make model-driven analysis optional/offline and nonessential to the dashboard.

### 1A — Domain and data spine

| ID | Pri | Backlog item | Acceptance criteria |
| --- | --- | --- | --- |
| MVP-001 | P0 | Versioned synthetic dataset | Generated with fixed seed and no production-derived values; top-level/record classification is `synthetic`; obvious `SYN-*` tokens; covers all DPD buckets, payment/escrow exceptions, source statuses, stale/missing/conflict cases, and authorization boundaries |
| MVP-002 | P0 | Canonical typed models | Models implement approved snapshot/event/lineage schemas; exact decimal/currency and explicit timezones; missing/unknown distinct from zero; validation rejects ambiguity and extra unauthorized fields |
| MVP-003 | P0 | Synthetic read adapter | Implements the same bounded read/validation/publish/reconcile interface planned for future sources; idempotent; no network or filesystem path supplied by a user/model |
| MVP-004 | P0 | Deterministic metric registry | Implements the approved Phase 1 subset: PF-001/002, DQ-001/002/003, PY-003/004, and DQY-001/002/003 or owner-approved smaller vertical subset; returns metric/definition version, numerator/denominator, population, as-of, lineage, and quality |
| MVP-005 | P0 | Independent reconciliation oracle | Expected values are declared independently of production calculation functions; exact results and filter invariants pass; mutation tests or deliberately broken implementation demonstrate tests fail |

### 1B — Read service and dashboard

| ID | Pri | Backlog item | Acceptance criteria |
| --- | --- | --- | --- |
| MVP-006 | P0 | Versioned read service/API | Implements context, metrics, definition, bounded loan-token search, loan summary, timeline, and exceptions contracts; strict inputs; safe errors; no `PUT/PATCH/DELETE` or source mutations; POST analysis is side-effect-free |
| MVP-007 | P0 | Server-side development authorization | Synthetic roles/portfolio entitlements enforced before query/aggregation/cache; forged role/scope, IDOR, cross-portfolio, small-cohort, and direct endpoint tests fail closed; dev identity cannot start in production mode |
| MVP-008 | P0 | Dashboard context shell | Every route shows **SYNTHETIC DATA — NOT FOR SERVICING USE**, as-of, business date/timezone, population, freshness, quality, and active filters; no plausible values in loading state |
| MVP-009 | P0 | Portfolio overview | Accessible KPI cards, delinquency count/UPB distribution, approved snapshot trend, quality/freshness state, and exception preview; each value opens definition/evidence; charts have equivalent tables |
| MVP-010 | P0 | Read-only loan detail | Pseudonymous token, minimum fields, typed timeline, field-level lineage, conflict/quality state, and no edit/action/outbound control; browser/deep links reauthorize each request |
| MVP-011 | P1 | Filter and drill-through consistency | Portfolio/product/investor category, DPD bucket, source, and exception filters use governed codes; aggregate equals contributing results; unknown remains visible; URLs do not leak restricted data |
| MVP-012 | P1 | Responsive/accessibility baseline | Keyboard-only and screen-reader journeys, visible focus, landmarks/headings/labels, contrast, reflow, table semantics, chart alternatives, errors/status messages, reduced motion, and financial/error-prevention review target WCAG 2.2 AA |

### 1C — LangChain, LangGraph, and Deep Agents foundation

| ID | Pri | Backlog item | Acceptance criteria |
| --- | --- | --- | --- |
| AGT-001 | P0 | Preserve LangChain privacy boundary | Existing live-call-off configuration, prompt classification/boundary, PII middleware, no prompt logging, fake model tests, and read-only tool posture remain green; dashboard works without a model/provider |
| AGT-002 | P0 | Add explicit LangGraph contract | Typed graph state includes only opaque request/thread token, `public/synthetic` classification, authorization reference, plan/evidence/citation references, policy/result status; deterministic nodes are testable; no checkpointer/store by default |
| AGT-003 | P0 | Synthetic analysis graph | A small explicit graph can validate a structured synthetic metric question, call only deterministic approved read tools, validate evidence, and return cited structured output using a fake model or deterministic node; unsupported/action requests refuse |
| AGT-004 | P1 | HITL contract test | A synthetic-only review interrupt can pause/resume with an injected ephemeral checkpointer in tests; resume uses same opaque thread, reauthorizes, and supports reject/edit/escalate; pre-interrupt nodes are idempotent; no operational action follows approval |
| AGT-005 | P0 | Deep Agent bounded factory | Deep Agent capability is separately configured and off by default; creation refuses live/production/restricted use; only synthetic/public read-only tools/corpus are injectable; write/edit/delete/execute, arbitrary network/MCP, persistent memory, and unbounded subagents are absent |
| AGT-006 | P1 | Deep Agent analysis test | With deterministic fake model and synthetic corpus, a bounded research task produces plan, cited evidence, unresolved conflicts, and limits within subagent/tool/token/time budgets; regulated-decision prompts refuse and call no action tool |
| AGT-007 | P1 | Independent layer switches | LangChain live calls, LangGraph persistence, remote tracing, and Deep Agent execution have separate fail-closed settings/kill switches and safe readiness output; no credential/model name/prompt is printed |

### 1D — Evidence and handoff

| ID | Pri | Backlog item | Acceptance criteria |
| --- | --- | --- | --- |
| MVP-013 | P0 | Human-review notices | Payment, escrow, default/loss-mitigation, bankruptcy, foreclosure, and borrower-communication contexts explicitly require authorized human review outside the application; automated UI tests assert copy is present |
| MVP-014 | P0 | No-action proof | Route/tool inventory contains no servicing write, generic SQL/HTTP/file/shell, outbound communication, payment, legal, or credit-furnishing capability; negative tests assert prohibition |
| MVP-015 | P0 | Test and quality suite | Network-free deterministic unit/contract/component tests; coverage threshold met; lint, format, type, security/dependency checks and commands in project quality guidance pass |
| MVP-016 | P1 | Accessibility and security review | Automated checks plus manual keyboard/screen-reader spot checks; threat cases for authz, injection, output encoding, safe errors, cache scope, leakage, graph state, resume, and delegation pass with retained evidence |
| MVP-017 | P1 | Operator/developer docs | Local start, fixture regeneration, expected values, safe settings, three agent layers, failure states, test commands, and prohibited capabilities are accurate and verified from clean setup |

### Phase 1 exit criteria

- One command starts the documented local synthetic dashboard and one command runs the complete network-disabled verification suite.
- All visible data and artifacts are prominently synthetic; a production-derived-data scan/review finds none.
- The selected metrics exactly reconcile to independent expected fixtures across representative filters and boundary dates.
- The API/UI is read-only and authorization-negative tests cover cross-role/portfolio object and aggregate access.
- The dashboard remains fully usable when all model/graph-persistence/Deep-Agent/remote-tracing switches are off.
- LangChain, LangGraph, and Deep Agents each have a documented, typed, separately tested boundary; fake-model tests require no credentials/network.
- Human-review copy and no-action inventory prove that no payment, escrow, default/loss-mitigation, bankruptcy, foreclosure, or communication decision can be made or executed.
- There are no known critical/high security, privacy, accessibility, correctness, or safety defects.

## Phase 2: Complete synthetic servicing modules

Goal: extend deterministic read models and UI, not authority.

| ID | Pri | Backlog item | Acceptance criteria |
| --- | --- | --- | --- |
| MOD-001 | P0 | Payments/cash exceptions | Separate lifecycle dates/statuses, suspense/unapplied, return/reversal, source reason and reconciliation; no action controls; exact money tests |
| MOD-002 | P0 | Escrow monitoring | Posted vs projected values unmistakable, analysis/disbursement source status, missing vendor facts as unknown, and mandatory human review |
| MOD-003 | P0 | Delinquency migration | Stable cohort roll/cure matrix with transfer/payoff/missing treatment and independent expected results; no treatment ranking |
| MOD-004 | P0 | Loss-mitigation monitoring | Source-attributed case stages and aging; never independently decides completeness/eligibility/outcome/deadline |
| MOD-005 | P0 | Bankruptcy/foreclosure visibility | Competing source assertions preserved, holds/freshness prominent, missing/conflict fails closed, no legal advice/action |
| MOD-006 | P1 | Customer care/compliance metadata | Complaint/NOE/RFI source status/aging, preferences/protections minimized, no content/body or outbound channel |
| MOD-007 | P0 | Data quality operations | Freshness/schema/completeness/duplicates/conflicts/reconciliation and downstream impact; no inferred source correction |
| MOD-008 | P1 | Approved synthetic export | Only if separately approved: minimized, marked synthetic, role/purpose scoped, audited, expiring, formula-injection safe; otherwise remains disabled |

Phase exit requires all module KPI definitions approved for synthetic use, cross-module/time/filter reconciliation, access and accessibility coverage, and zero high-impact writes.

## Phase 3: Source-grounded assistant and bounded research

Goal: enable approved model behavior first on synthetic/public material, then on an explicitly approved minimized corpus. No operational actions.

| ID | Pri | Backlog item | Acceptance criteria |
| --- | --- | --- | --- |
| AI-001 | P0 | Provider/use-case approval | Provider/model/data terms, region, retention, egress, secrets, cost, model-risk and kill switch approved; exact current model identifier verified from official documentation at implementation time |
| AI-002 | P0 | Tool/retrieval productionization | Entitlement before retrieval, typed read tools, budgets, source/effective-date citations, output validation, prompt-injection corpus tests, no generic tools |
| AI-003 | P0 | LangGraph inquiry workflow | Explicit nodes for classify/authorize/plan/read/validate/cite/respond/refuse; state schema minimized; deterministic facts never model-computed |
| AI-004 | P0 | Persistence decision | If durable review is needed, approved checkpointer design passes encryption/isolation/retention/replay/resume/reauthorization tests; store/cross-thread memory remains prohibited unless separately justified |
| AI-005 | P1 | Draft-analysis HITL | Review package shows exact draft/evidence/limitations; reject/edit/escalate captured; no resume causes an operational action or communication |
| AI-006 | P1 | Bounded Deep Agent research | Only approved aggregate/policy/data-quality tasks; read-only ephemeral workspace; restricted subagents and budgets; complete citations; analyst verification |
| AI-007 | P0 | Evaluation and red team | Meets approved thresholds for facts/citations, refusal, leakage, injection, authz, tool arguments, stale/conflict behavior, subgroup quality, accessibility, checkpoint and delegation safety |
| AI-008 | P0 | Monitoring and incident control | Metadata-minimized telemetry, version inventory, drift/quality sampling, separate kill switches, rollback, affected-output discovery, and incident exercise |

## Phase 4: Controlled real-data read-only pilot

Goal: validate accuracy, controls, and operational fit on a narrow approved population without write capability.

| ID | Pri | Backlog item | Acceptance criteria |
| --- | --- | --- | --- |
| PIL-001 | P0 | Applicability/control approval | Compliance/legal matrix names applicable laws/contracts, effective dates, controls/evidence, owners, tests, monitoring; privacy/security/model/fair-lending reviews signed |
| PIL-002 | P0 | Production adapters | Field-level authority, service identity, signed/validated ingest, schema evolution, lineage, correction, quarantine, freshness, and reconciliation pass owner tolerances |
| PIL-003 | P0 | Production identity/policy | Federated identity/MFA, least privilege, field/portfolio/purpose policy, separation, recertification, break glass, and negative isolation tests |
| PIL-004 | P0 | Operational readiness | SLO/RTO/RPO, backup/restore, scaling, support, incident/correction, change/rollback, retention/deletion, vendor oversight, penetration/accessibility testing complete |
| PIL-005 | P0 | Narrow monitored pilot | Time/portfolio/user bounded; daily data/control review; user training; explicit feedback/correction; all action/export/model features independently gated |

Phase exit requires owner-certified data reconciliation, no high-severity findings, demonstrated user benefit without harmful incentives/automation bias, and an independent go/no-go review.

## Phase 5: Production read-only service

Goal: scale only the proven read-only scope.

- Progressive rollout with feature/tenant/model/graph/tool/corpus kill switches.
- Continuous access, data, model, fairness, security, privacy, accessibility, and compliance monitoring.
- Periodic owner certification of metrics, sources, controls, roles, vendors, retention, and recovery.
- Change management and revalidation for every material dependency/model/prompt/graph/tool/data/definition/policy/UI update.
- Correction and incident processes that identify affected outputs and downstream use.

## Phase 6: Transactional capability — not authorized

No item is scheduled. A future proposal for payment, escrow, default/loss-mitigation, bankruptcy, foreclosure, borrower communication, credit furnishing, export to a decision system, or source mutation requires a separate project charter, new ADR, legal/compliance authority, deterministic controls, qualified human approval, separation of duties, target-system integration, idempotency, reconciliation, rollback/correction, evidence, and independent validation. LangGraph or Deep Agents HITL does not by itself satisfy this gate.

## Cross-phase definition of done

An item is done only when:

- behavior and nonbehavior match the product scope and accepted ADRs;
- typed contracts, code, tests, UI copy, docs, and audit semantics agree;
- happy, edge, stale, missing, conflict, unauthorized, injection, and failure cases are tested;
- data classification, lineage, definition/version, time, quality, and human-review needs are visible;
- accessibility is tested for the complete changed journey;
- secrets/PII/log/trace/checkpoint/tool/delegation review is complete;
- no unrelated upstream or user work is overwritten;
- quality commands pass from documented clean setup; and
- an accountable owner accepts remaining risk, or none is shipped.

## Requirement traceability

| Product requirement | Primary backlog evidence |
| --- | --- |
| PR-01 context/freshness | MVP-001, MVP-006, MVP-008 |
| PR-02 deterministic KPIs | MVP-004, MVP-005, MVP-009 |
| PR-03 authorized drill-through | MVP-006, MVP-007, MVP-010, MVP-011 |
| PR-04 lineage/quality | MVP-002, MVP-003, MVP-006, MVP-010 |
| PR-05 read-only/human review | MVP-013, MVP-014, AGT-004/005 |
| PR-06 accessibility | MVP-009/010/012/016 |
| PR-07 safe audit | AGT-007, MVP-015/016; production design in PIL-003/004 |
| PR-08 fail closed | MVP-002/003/006/007, AGT-002/003/005 |
| PR-09 sourced assistant | AGT-001–006 for foundation; AI-001–008 for enablement |
| PR-10 export disabled/governed | MVP-014 and MOD-008 |
