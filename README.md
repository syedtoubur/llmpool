# llmpool

A private pool of friends' and family's devices in Dhaka. Each device serves local LLMs that fit in its free GPU memory or RAM. Members can use those models as orchestrator and subagents in an agent harness.

- Plan: see `docs/contracts.md` for interfaces and `docs/phase0-findings.md` for verified facts.
- Install dev environment: `uv sync`
- Tests: `uv run pytest`
- Security gate: `uv run python -m security.gate`
- Device B: [one-command Windows test bundle](docs/run-on-B.md)
