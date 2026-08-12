# Next prompt: build the synthetic dashboard vertical slice

Copy the prompt below into the next Codex session.

---

Work in `mortgage_servicing_dashboard/` and implement Phase 1 of the Mortgage Servicing Dashboard foundation at maximum rigor. Begin by reading every applicable `AGENTS.md`, the application `README.md`, `QUALITY_GUIDE.md` if present, and all files under `docs/`, especially `IMPLEMENTATION_BACKLOG.md`, `ARCHITECTURE_DECISIONS.md`, `DATA_AND_INTERFACES.md`, and `SECURITY_PRIVACY_AND_COMPLIANCE.md`. Inspect the current package and dirty worktree before editing; preserve other people's work, do not revert unrelated changes, do not modify unrelated framework source for application behavior, and do not commit or push.

Outcome: deliver a runnable, accessible, internal read-only dashboard vertical slice using only independently generated **SYNTHETIC** data, plus a safe three-part LangChain + LangGraph + Deep Agents foundation. The dashboard must work fully with live model calls, remote tracing, persistence, and Deep Agents disabled. Do not connect real borrower data or any external servicing system. Do not build a servicing action workflow.

Make reasonable documented implementation choices without stopping for preferences. Select the smallest production-capable dashboard/API approach compatible with the existing Python package; favor a single-runtime, server-authorized design unless the repository already establishes a better pattern. Record the choice by resolving ADR-013 in the architecture decisions. Use `uv` for Python dependencies and commands; verify current official package compatibility before adding dependencies, and do not hardcode a live model/provider default.

Implement this bounded scope:

1. Create a deterministic, versioned synthetic dataset and adapter. It must use an obvious fixed seed and `SYN-*` opaque tokens, contain no real-looking names/contact information, SSNs, bank/card data, full loan numbers, credentials, or production-derived values, and cover current/1–29/30–59/60–89/90+/unknown DPD plus representative payment, escrow, loss-mitigation, bankruptcy/foreclosure, complaint, stale, missing, conflict, and correction states. Put `data_classification=synthetic`, dataset/generator versions, and a fixed timezone-aware reporting as-of value in the contract.

2. Add strict typed canonical snapshot/event/lineage models. Use exact decimals with currency codes, explicit event/effective/received/posted/ingestion/snapshot time semantics, controlled codes, immutable correction/supersession links, and distinct zero/null/unknown states. Reject ambiguous timestamps and unexpected unauthorized fields.

3. Build a deterministic metric registry and independent expected-value oracle for a coherent Phase 1 subset: at minimum active loan count/UPB, 30+ and 90+ delinquency count/UPB rates, unapplied/suspense amount, aged payment exceptions, feed freshness, required-field completeness, and reconciliation variance. Return metric ID/version, value/unit, numerator/denominator, population, as-of/timezone, lineage, and quality. Tests must prove filters reconcile and must fail if a formula is deliberately broken.

4. Add a versioned, bounded, server-authorized read service/API for context, metrics, metric definitions, loan-token search, read-only loan detail/timeline, and exception preview. Enforce synthetic development roles and portfolio entitlements before query, aggregation, and cache. Strictly validate filters, paginate deterministically, return safe typed errors, and test forged roles, IDOR/cross-portfolio access, invalid filters, and small/unknown populations. Do not add source mutation routes, raw SQL, generic HTTP/file/shell tools, or any `PUT`, `PATCH`, or `DELETE` servicing behavior.

5. Build the accessible dashboard shell, portfolio overview, and read-only loan detail. Every page must prominently say **SYNTHETIC DATA — NOT FOR SERVICING USE** and show as-of time, business date/timezone, population, freshness, quality, and active filters. Include KPI cards, delinquency count and UPB distribution, a snapshot trend, exception preview, definition/evidence affordances, and filter-to-contributing-record drill-through. Every chart needs an equivalent accessible table. Implement keyboard/focus, headings/landmarks/labels, contrast/reflow, status/error/empty/loading states, and no color-only meaning. Do not show edit, approve, send, post, reverse, disburse, refer, or other action controls.

6. Preserve and extend the LangChain safety boundary. Keep the existing provider-neutral configuration, live calls off by default, `public/synthetic` prompt classification, PII blocking, no prompt/tool content logging, no remote tracing, no checkpointer/store, deterministic fake-model tests, and read-only typed tools. The UI/API must never depend on a model response for a metric or source fact.

7. Extend the existing explicit LangGraph layer. Evolve its minimal typed state to contain only opaque request/thread tokens, synthetic/public classification, authorization/policy references, bounded plan/evidence/citation references, and result/refusal/review status. Implement a small explicit synthetic analysis graph whose deterministic nodes classify/authorize, call approved metric/definition tools, validate evidence, and return cited structured output or refuse. Persistence remains off by default. Add a test-only synthetic HITL interrupt/resume contract using an injected ephemeral checkpointer: opaque thread ID, same-thread resume, reauthorization, reject/edit/escalate, idempotent pre-interrupt work, and no operational side effect after resume.

8. Extend the existing separately controlled Deep Agents layer using the official Deep Agents APIs already compatible with the package. Its factory must remain off by default and fail closed for production/restricted data. For a deterministic fake-model synthetic test, allow only a bounded research task over approved metric/definition evidence. Preserve the current denial of model-visible write/edit/delete/execute tools, arbitrary network/MCP, persistent memory, and unbounded subagents. Constrain task/subagent/tool/token/time/result budgets. The output must contain a plan, citations, conflicts, and limitations. Prompts asking it to make a regulated servicing decision must refuse without calling an action tool. Prefer the explicit LangGraph path for known workflows; do not route routine dashboard reads through a Deep Agent.

9. Keep independent fail-closed settings/kill switches for LangChain live calls, LangGraph persistence, remote tracing, and Deep Agent execution. Safe readiness/doctor output may report allow-listed booleans, versions, and non-secret configuration metadata but must not reveal model identifiers, credentials, prompts, tool arguments/results, source content, or raw or non-allow-listed environment/configuration values.

10. Put the mandatory human-review notice in every payment, escrow, default/loss-mitigation, bankruptcy, foreclosure, and borrower-communication context: an authorized trained human must review every such decision in the applicable external system of record. A LangGraph/Deep Agents HITL resume is only a technical review event and is never authority to execute or communicate. There must be no payment, escrow, loss-mitigation, credit-furnishing, legal/foreclosure, source-write, or outbound communication connector/tool/route.

11. Add network-free deterministic unit, contract, authorization, component/UI, accessibility, safety, graph-state/resume, and Deep Agent-boundary tests. Preserve the project's coverage threshold. Run every command in the application quality guide plus the documented `uv` test, lint, format-check, and strict type-check commands. Fix all failures within scope. Do not weaken checks, broad-catch warnings, or mark tests as passing without exercising behavior.

Acceptance criteria:

- A documented single command starts the local synthetic dashboard; a documented single command runs the complete network-disabled verification suite.
- The complete overview-to-filter-to-loan-detail journey works, is accessible, and exactly reconciles to the independent synthetic oracle.
- Prominent synthetic and human-review labeling is present and asserted by tests.
- Route and tool inventory proves the product is read-only and contains no generic or high-impact action surface.
- The dashboard works with all AI/persistence/tracing switches off and no model credentials.
- LangChain, LangGraph, and Deep Agents each have a clear, typed, separately tested responsibility and kill switch; fake tests make no network calls.
- Missing, stale, conflicted, unauthorized, malformed, injected, or unsupported input fails closed with a safe explicit state.
- All tests, coverage, lint, formatting, typing, security/dependency checks, and manual accessibility spot checks pass or an exact blocker is reported with evidence.

Before finishing, inspect the final diff for accidental production data, secrets, broad tools, write routes, prompt/log leakage, generated caches/build artifacts, and unrelated edits. Update the application README and relevant docs/ADR/backlog status to match only what actually exists. Report the outcome first, then exact files changed, architecture choice, commands/results, key safety evidence, and remaining owner decisions. Do not claim production/compliance approval.
