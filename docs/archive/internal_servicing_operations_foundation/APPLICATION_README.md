# Mortgage Servicing Dashboard Foundation

This directory is an isolated, provider-neutral application foundation built deliberately
across LangChain, LangGraph, and Deep Agents. It is ready for the next development phase,
but it intentionally does **not** contain a dashboard UI, customer-data integration,
mortgage calculations, servicing decisions, or account actions.

## What is included

- validated `MSD_*` application settings with live model calls off by default;
- typed, domain-neutral LangChain state and runtime context;
- current `langchain.agents.create_agent` model/tool wiring against this repository checkout;
- a deterministic, checkpoint-ready LangGraph that stops at mandatory human review;
- a restricted Deep Agents research-draft worker with delegation and filesystem access off;
- static tools that report foundation capabilities and guardrails only;
- a fail-closed prompt boundary and LangChain PII middleware;
- a network-free `doctor` CLI and deterministic unit tests.

The package was intentionally isolated from its former host repository so future
application work could be extracted without coupling to unrelated packages.

## Three-layer architecture

- **LangChain** supplies the provider-neutral chat-model and tool primitives plus the
  `create_agent` loop. Its only tools expose static foundation metadata.
- **LangGraph** supplies deterministic stateful orchestration. The baseline graph is
  runnable without a model, accepts an optional checkpointer, and always ends in
  `awaiting_human_review`; it has no execution branch for operational work.
- **Deep Agents** supplies a future research/analysis worker boundary. The application
  activates a model-specific `HarnessProfile`, disables the general-purpose subagent,
  passes no subagents, excludes every filesystem/task/execute tool, applies a deny-all
  filesystem permission, and independently allow-lists tool visibility and execution.

No layer needs an API key for construction or tests. Test doubles exercise the real
LangChain and Deep Agents graphs, and an in-memory saver exercises LangGraph checkpointing.
Live LangChain calls, LangGraph persistence, and Deep Agents execution have separate
fail-closed switches and are all disabled in `.env.example`.

## Quick start

From this directory:

```bash
uv sync --locked --group dev
uv run msd-foundation doctor --json
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
```

The default CLI is also available through the module entrypoint:

```bash
uv run python -m mortgage_servicing_dashboard doctor
```

Copy `.env.example` to `.env` when local settings are needed. The CLI only emits an
allow-listed readiness summary; it never prints the configured model identifier,
credentials, prompts, tool arguments, or raw or non-allow-listed environment and
configuration values.

## Privacy and security boundary

Only text already classified as **public** or **synthetic** may cross the model boundary.
Raw borrower or customer data must remain outside this package. That includes names,
addresses, contact details, Social Security numbers, loan or account identifiers, payment
details, documents, authentication material, and provider credentials.

The boundary is enforced in layers:

1. `PromptBoundary` requires an explicit public/synthetic classification, caps input size,
   blocks common PII and credential patterns, and rejects values matching secrets already
   present in the process environment.
2. LangChain `PIIMiddleware` blocks sensitive patterns before the model, after the model,
   and in tool results. Blocking is used instead of redaction so sensitive content is not
   silently forwarded.
3. The LangChain agent has no checkpointer or store, `debug=False`, and refuses invocation
   whenever LangSmith or legacy LangChain remote tracing is enabled. LangGraph rejects a
   supplied checkpointer unless its independent persistence switch is enabled.
4. The Deep Agents worker has no delegation, skills, memory, filesystem, network, or
   execution tools. Every draft is marked `requires_human_review=True`.
5. Network-free CLI/diagnostic output and application-authored boundary errors contain
   only allow-listed metadata and never echo rejected values.
6. Successful agent results may contain model-authored text after configured PII screening;
   Deep Agents results are explicitly marked as drafts requiring human review. Third-party
   model or provider invocation errors can propagate and must be rendered generically at
   the deployment boundary.
7. Prompt bodies and tool arguments are never logged by this package.

Pattern detection cannot prove that arbitrary prose is de-identified (for example, a
person's name may not match a reliable pattern). Upstream UI and data adapters therefore
must remove or tokenize sensitive fields **before** calling `PromptBoundary.approve`.
Provider HTTP-body logging must also remain disabled. These are required controls, not
optional recommendations.

## Model setup boundary

No runtime model provider is selected or configured by this baseline. Deep Agents may
install Anthropic and Google provider integrations and SDKs transitively, but the baseline
supplies no credentials and does not initialize or invoke those providers by default. A
later phase may explicitly select and configure an approved LangChain provider and set
`MSD_MODEL` to its provider-qualified model identifier. `MSD_ENABLE_MODEL_CALLS` should
become `true` only after privacy review, secret-manager integration, and network egress
controls are in place. Deep Agents additionally requires `MSD_ENABLE_DEEP_AGENT=true`;
model-call enablement alone cannot activate it.

Application code constructs the LangChain agent with `create_dashboard_agent`, the
LangGraph boundary with `create_foundation_workflow`, and the restricted Deep Agents worker
with `create_research_worker`. Tests inject a local fake chat model, so they neither need
credentials nor make network calls.

## Deliberately unavailable

- dashboard pages, components, and APIs;
- borrower, loan, payment, escrow, or investor data access;
- balances, payoff amounts, schedules, fees, or other mortgage calculations;
- recommendations, servicing decisions, approvals, or operational mutations;
- conversation persistence, remote tracing, and prompt-content logging.

The static tools explicitly report these limitations and cannot access external systems.
