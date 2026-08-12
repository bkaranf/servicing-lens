# Quality and integration guide

This guide is scoped to `mortgage_servicing_dashboard/`. The repository-level
`AGENTS.md` remains authoritative, including its requirement to use `uv` and its
Python, testing, documentation, and security standards.

## Required local gate

Run these commands from this directory before handing off a change:

```bash
uv sync --locked --group dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest --cov=mortgage_servicing_dashboard --cov-report=term-missing
uv run msd-foundation doctor --json
```

The locked sync must succeed without changing `uv.lock`. Pytest is configured to
deny sockets, and the coverage command enforces the project-wide 90% branch-aware
floor. The doctor command must remain deterministic, network-free, and limited to
allow-listed readiness metadata.

## Three-layer integration evidence

Dependencies alone do not prove integration. Preserve construction and behavior
tests for the responsibilities described in the official
[LangChain](https://docs.langchain.com/oss/python/langchain/overview),
[LangGraph](https://docs.langchain.com/oss/python/langgraph/overview), and
[Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview)
documentation.

| Layer | Required credential-free evidence |
| --- | --- |
| LangChain | Construct `create_agent` with the local recording model, invoke its tool loop, and prove that only the two static foundation tools are bound. |
| LangGraph | Compile and invoke the deterministic `StateGraph`, stop at mandatory human review, and prove an injected in-memory checkpointer receives the final metadata-only state. |
| Deep Agents | Construct and invoke `create_deep_agent` with the local recording model, prove filesystem/delegation/execution tools are absent, and prove a fabricated forbidden tool call fails closed. |

The focused smoke files are `test_agent.py`, `test_orchestration.py`, and
`test_deep_worker.py`. The GitHub Actions workflow runs them explicitly before the
complete coverage suite on the minimum and newest declared Python versions.

## Security invariants

- Use only independently generated, conspicuously **SYNTHETIC** or public test
  material. Sanitized or sampled production borrower data is not test data.
- Unit and smoke tests must need no provider key, external service, network access,
  remote trace, persistent store, or production configuration.
- Keep live model calls and all LangSmith or legacy LangChain remote tracing off in
  local defaults and CI. Never solve a test failure by enabling them.
- Keep prompts, model identifiers, tool arguments/results, credentials, and raw or
  non-allow-listed environment values out of diagnostic output, application-authored
  exceptions, snapshots, and test reports.
- The LangChain and Deep Agents entry points must accept only prompts approved as
  `public` or `synthetic`, retain blocking PII middleware on input/output/tool-result
  surfaces, and reject active remote tracing before invocation.
- The Deep Agents profile must exactly match the selected model. Keep the default
  general-purpose subagent disabled, pass no subagents, exclude filesystem,
  execution, task, and planning tools, retain deny-all filesystem permissions, and
  enforce the independent runtime tool allowlist. Test both the visible tool set and
  a fabricated forbidden call; permission configuration by itself is insufficient.
- LangGraph checkpoints must be opt-in and injected. State and checkpoint tests may
  contain only opaque correlation metadata and must always terminate at the human
  review boundary, never an operational action.
- Do not add a mutation route/tool, arbitrary SQL, filesystem or shell execution,
  unrestricted MCP/network access, persistent model memory, autonomous delegation,
  borrower communication, or mortgage calculation to this foundation.

## Dependency and review discipline

Use `uv` for every dependency operation. When dependencies change, update both
`pyproject.toml` and `uv.lock`, then inspect the lock diff for unexpected provider
SDKs, code-execution/sandbox extras, MCP clients, telemetry, or persistence packages.
Do not use `pip`, manually edit resolved lock entries, or leave an unlocked install
path in documentation or CI.

Add deterministic tests for every behavior change, including failure and refusal
paths. Do not lower coverage, relax Ruff or Mypy, remove socket blocking, or broaden a
tool/data allowlist merely to make a gate pass. Changes to privacy screening,
middleware order, model/profile selection, tool visibility, tracing, persistence,
checkpointing, or the human-review boundary require explicit security-focused review.

Keep implementation, tests, safe readiness claims, `.env.example`, and documentation
in sync. A reviewer should be able to distinguish deterministic facts, model-authored
drafts, technical review pauses, and authoritative human decisions without inference.

## CI scope

`.github/workflows/mortgage-servicing-dashboard.yml` is deliberately path-scoped to
this subtree and itself. It has read-only repository permissions, uses SHA-pinned
external actions through the repository's `uv` setup, provides no secrets, and forces
model calls, Deep Agents execution, LangGraph persistence, and remote tracing off. New
checks belong there only when they validate this application; do not couple the project
to unrelated repository release workflows.
