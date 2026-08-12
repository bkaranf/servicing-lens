# Architecture decisions

Decision statuses are `Accepted`, `Proposed`, `Superseded`, or `Rejected`. An accepted decision is binding for this application until a later record explicitly supersedes it. Dates reflect the baseline decision, not production approval.

## Decision summary

| ID | Status | Decision |
| --- | --- | --- |
| ADR-001 | Accepted | Isolate the application from unrelated framework source |
| ADR-002 | Accepted | Synthetic, internal, read-only decision support first |
| ADR-003 | Accepted | LangChain + LangGraph + Deep Agents form a layered three-part agent foundation |
| ADR-004 | Accepted | Deterministic domain and metric core is authoritative over model synthesis |
| ADR-005 | Accepted | Use ports/adapters, canonical facts, immutable events, and field-level lineage |
| ADR-006 | Accepted | Enforce authorization and data minimization before query, cache, retrieval, or model |
| ADR-007 | Accepted | Human review is mandatory and separate from graph pause/resume |
| ADR-008 | Accepted | Persistence and memory are off by default and specially governed |
| ADR-009 | Accepted | Deep Agents are bounded research/analysis harnesses, never regulated decision-makers |
| ADR-010 | Accepted | Observability is metadata-minimized; remote model tracing is off by default |
| ADR-011 | Accepted | Money and time have exact, explicit semantics |
| ADR-012 | Accepted | APIs and tools are typed, versioned, bounded, and read-only |
| ADR-013 | Proposed | Select dashboard/API/deployment technology after the first contract review |
| ADR-014 | Proposed | Select model/provider and production persistence only after governance approval |

## ADR-001: Isolated application boundary

**Status:** Accepted, 2026-08-11

**Context:** The application originally shared a repository with unrelated framework source. Product code must not complicate that source's versioning, tests, releases, or rebases.

**Decision:** Keep the application under `mortgage_servicing_dashboard/` with its own `pyproject.toml`, source, tests, docs, environment contract, and commands. Depend on framework packages through supported package interfaces. Do not alter unrelated framework source to implement dashboard behavior.

**Consequences:** Application dependencies and CI are independently managed. Reusable fixes to upstream packages require a separate contribution; application-specific shortcuts do not enter public APIs.

## ADR-002: Synthetic, internal, read-only first

**Status:** Accepted, 2026-08-11

**Context:** Mortgage servicing data and decisions are high impact and heavily controlled. Building against production data or action interfaces before definitions and controls are tested creates avoidable risk.

**Decision:** The first release is an internal analytical dashboard over independently generated **SYNTHETIC** data. It has no mutation, payment, outbound communication, credit-furnishing, legal, or source-write integration. All high-impact modules repeat the human-review requirement.

**Consequences:** Early usability and technical evidence are meaningful but cannot establish production accuracy or compliance. Any real-data pilot and any action capability require distinct approval gates.

## ADR-003: Three-part agent foundation

**Status:** Accepted, 2026-08-11

**Context:** A single “agent” label obscures materially different responsibilities. The official stack distinguishes an agent framework, an orchestration runtime, and an agent harness.

**Decision:** Use the following layered responsibilities, aligned with the official [LangChain](https://docs.langchain.com/oss/python/langchain/overview), [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview), and [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview) overviews:

- **LangChain** supplies model/message abstractions, typed tools, middleware, retrieval composition, structured output, and simple agent loops.
- **LangGraph** supplies explicit state graphs, deterministic/model node composition, streaming, durable execution, checkpointing, and interrupts/HITL for workflows that need them.
- **Deep Agents** supplies an optional higher-level harness for planning, constrained subagents, context management, and an isolated virtual workspace when bounded complex analysis justifies it.

The application service layer, domain rules, policy enforcement, and data adapters sit below all three. Dashboard/API clients do not invoke sources directly.

**Consequences:** A simple question does not require Deep Agents; a known sequence should prefer explicit LangGraph; deterministic UI/API reads may use neither model nor agent. Each layer has independent configuration, tests, kill switch, and audit version. Adding Deep Agents is not permission to add broad tools.

## ADR-004: Deterministic domain core

**Status:** Accepted, 2026-08-11

**Context:** LLMs are probabilistic and can produce plausible but unsupported amounts, dates, statuses, or definitions.

**Decision:** Typed deterministic services compute money, balances, time classification, populations, metrics, authorization projections, and quality status. The model may retrieve and explain returned facts; it cannot replace a calculation, fill a missing value, or create a canonical servicing fact.

**Consequences:** Metric definitions are versioned and unit-tested independently of prompts. Model responses cite the exact definition/tool result and state unknown/conflict rather than estimating.

## ADR-005: Ports, adapters, canonical facts, and lineage

**Status:** Accepted, 2026-08-11

**Context:** Authority varies by field and system. Directly coupling UI/prompts to source schemas makes conflicts and changes unsafe.

**Decision:** Define source ports and read-only adapters, validate/quarantine at ingress, normalize into a versioned canonical model, preserve immutable events/corrections, and attach field-level source lineage and time semantics. Build read models and metrics from canonical facts. Preserve competing authoritative assertions when no approved resolution rule exists.

**Consequences:** Initial synthetic adapters exercise the same contract. A field is not model/UI-authorized merely because it exists in canonical storage.

## ADR-006: Policy before data

**Status:** Accepted, 2026-08-11

**Context:** Post-filtering a response cannot undo unauthorized access, aggregation, retrieval, cache entry, trace, or model disclosure.

**Decision:** Enforce authenticated subject, tenant, portfolio, purpose, role, field, action, environment, and classification policy server-side before query execution, aggregation, caching, retrieval, tool invocation, graph state, or model context. Use deny by default and minimum necessary projection.

**Consequences:** Cache identity includes policy scope and data/definition version. UI role simulation is development-only and is never a production authorization mechanism.

## ADR-007: Accountable human review, not checkbox HITL

**Status:** Accepted, 2026-08-11

**Context:** LangGraph interrupts and Deep Agents HITL can pause and resume execution, but a technical resume does not prove a trained reviewer had authority, evidence, separation of duties, or recorded a compliant decision.

**Decision:** Use interrupts only for bounded review of an analysis, plan, or draft until a separately approved action architecture exists. Payment, escrow, default/loss-mitigation, bankruptcy, foreclosure, and borrower-communication decisions occur in the authoritative external process and require explicit authorized human review. No action tool exists in the baseline.

**Consequences:** Resume revalidates identity, entitlement, state ownership, source freshness, and policy. Review packages expose evidence and reject/edit/escalate choices. A future write still requires a new ADR, target-system approval, idempotency, reconciliation, rollback, and independent validation.

## ADR-008: Persistence and memory off by default

**Status:** Accepted, 2026-08-11

**Context:** LangGraph checkpointers persist thread state; stores can retain cross-thread application data. Deep Agents may add memory, context offloading, and workspaces. These can create shadow servicing records and privacy/retention risk.

**Decision:** Current invocations are ephemeral. Enable checkpointer, store, conversation memory, Deep Agent memory, or remote trace persistence only for an approved purpose and minimum schema. Use opaque random thread IDs, encrypted tenant-isolated backends, reauthorization on resume, automatic expiry/deletion, and no raw borrower content or cross-case memory.

**Consequences:** Durable HITL is unavailable until persistence approval. Production checkpointers are infrastructure, not systems of record; state may reference approved facts but not become their authoritative copy.

## ADR-009: Bounded Deep Agent analysis

**Status:** Accepted, 2026-08-11

**Context:** Deep Agents can plan, delegate, use tools/filesystems, execute code, and retain context. Those capabilities expand both usefulness and attack surface.

**Decision:** Deep Agents are disabled until a specific complex analysis use case is approved. Permitted later uses are analyst-initiated research over allowlisted synthetic/de-identified metrics and approved documents. Provide an ephemeral task workspace, read-only tool/corpus allowlist, narrow subagents, budgets, citations, and cancellation. Disable write/edit/delete/execute, arbitrary network/MCP, persistent borrower memory, and self-updating skills/prompts by default.

**Consequences:** Deep Agents cannot prioritize or decide individual servicing treatment, generate a final borrower communication, determine eligibility, interpret bankruptcy/foreclosure, or execute any operational action. Prefer a small explicit graph whenever the steps are known.

## ADR-010: Metadata-minimized observability

**Status:** Accepted, 2026-08-11

**Context:** Prompts, tool arguments/results, retrieved text, graph state, and traces can duplicate restricted data in systems with different access and retention.

**Decision:** Remote tracing remains off by default. Operational telemetry is an allowlist of random request token, environment, component/version, timing, counts, classification, outcome, and safe error code. Audit events separately record minimized evidence of access, policy, model/tool/graph/review lifecycle. No prompt/result/customer content is logged.

**Consequences:** Debugging requires synthetic reproduction, safe correlation tokens, and controlled break-glass evidence. Any later tracing vendor/use requires data-term approval and verified redaction or metadata-only capture.

## ADR-011: Exact money and explicit time

**Status:** Accepted, 2026-08-11

**Context:** Binary floating-point, implicit timezone, and conflated event/effective/posting times can materially misstate servicing facts.

**Decision:** Represent money with exact decimal and ISO currency; round only by approved display/accounting rule. Preserve business date, event, effective, received, posting, extraction, ingestion, and snapshot times separately with timezone/calendar identity. Missing or ambiguous values fail validation; they are not zero or “today.”

**Consequences:** API schemas and fixture tests cover precision, boundaries, timezone/DST, month-end, and late/out-of-order events.

## ADR-012: Typed, versioned, bounded, read-only interfaces

**Status:** Accepted, 2026-08-11

**Context:** Generic SQL/HTTP/file tools and unbounded queries make authorization, cost, reliability, and injection control difficult.

**Decision:** Dashboard APIs, source adapters, LangChain tools, graph state, interrupts, and Deep Agent task interfaces use explicit versioned schemas, allowlisted operations/fields, deterministic sorting, row/byte/time/concurrency budgets, classification, lineage, and safe errors. No source mutation interface is present.

**Consequences:** New operations and fields receive contract, privacy, authorization, and negative tests. Rich read-only analysis may use `POST` but must remain side-effect-free except minimized audit.

## ADR-013: Dashboard/API/deployment stack deferred

**Status:** Proposed, 2026-08-11

**Context:** The package has a Python/LangChain foundation, but user population, hosting target, enterprise identity, design system, data platform, deployment controls, and expected scale are not yet selected.

**Decision:** Choose the smallest maintainable dashboard/API stack compatible with the existing package and repository when implementing the synthetic vertical slice. Record the selection, accessibility/server-side authorization plan, supported environments, build/deploy path, and rationale in a superseding accepted ADR. Avoid a second language/runtime unless its user/deployment value justifies the operational cost.

**Acceptance factors:** Mature accessible components, typed contracts, server-side security, deterministic testing, charts with table alternatives, dependency support, internal deployment fit, and developer workflow using `uv` for Python.

## ADR-014: Model/provider and production persistence deferred

**Status:** Proposed, 2026-08-11

**Context:** No vendor is required to implement synthetic dashboard metrics and views. Provider and persistence choices affect data use, region, retention, security, cost, evaluation, and portability.

**Decision:** Keep the current provider-neutral, live-calls-disabled design. Select provider/model, checkpoint/store, retrieval/index, and optional LangSmith capabilities only after use-case, privacy/security/legal/model-risk, data residency, retention, egress, evaluation, support, and cost approval. Verify current model identifiers and packages from official sources when selecting them.

**Consequences:** Tests use deterministic fakes. The first dashboard slice succeeds without network/model credentials. No model name is hardcoded as a shipped default.

## Required new decisions

A new ADR is mandatory before any of the following:

- production borrower data or restricted document retrieval;
- production authentication provider or cross-tenant deployment;
- persistent graph checkpoint/store, conversational memory, or remote tracing;
- live model/provider, embedding/index, or internal knowledge corpus;
- Deep Agent filesystem, code execution, network/MCP, memory, or subagent expansion;
- export, borrower communication, payment, credit furnishing, source update, legal/vendor instruction, or other write/action interface;
- model-influenced individual queue ranking, eligibility, treatment, deadline, or alert severity; or
- material change to approved KPI population, source authority, data classification, or human-review gate.
