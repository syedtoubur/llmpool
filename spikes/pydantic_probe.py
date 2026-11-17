"""Probe Pydantic AI against OpenAI-compatible chat-completions endpoints.

Installed API findings (pydantic-ai 2.69.0), verified in
``E:/local_ram_cluster/.venv/Lib/site-packages/pydantic_ai``:

* ``OpenAIChatModel`` is imported from ``pydantic_ai.models.openai`` and accepts
  ``provider=OpenAIProvider(base_url=..., api_key=...)``; the provider class is
  imported from ``pydantic_ai.providers.openai``. ``OpenAIModel`` is removed.
  Read ``models/openai/_chat_model.py``, ``providers/openai.py``, and
  ``models/openai/__init__.py``.
* ``FallbackModel(default_model, *fallback_models, fallback_on=(ModelAPIError,))``
  lives in ``pydantic_ai.models.fallback``. ``fallback_on`` accepts exception
  classes or predicates (and response handlers). Read ``models/fallback.py``.
* ``ToolOutput``, ``NativeOutput`` and ``PromptedOutput`` are public imports
  from ``pydantic_ai``. Tool output is the default structured mode; native and
  prompted modes are selected with ``Agent(output_type=...)``. Read
  ``_output.py`` and the package ``__init__.py``.
* Delegation is an ordinary ``@agent.tool`` function that calls another
  agent's ``run``/``run_sync`` and returns ``result.output``. Pass the same
  ``RunUsage`` using ``worker.run(..., usage=ctx.usage)`` to aggregate usage.
  Read ``_agent.py`` and ``_usage.py``.
* Set agent retry behavior with ``Agent(..., retries=N)`` (and tool retry
  behavior with ``@agent.tool(retries=N)``); run-level ``retries=`` overrides
  the agent default. Read ``_agent.py`` and ``tools.py``.

Run with ``python -m spikes.pydantic_probe --endpoint NAME=BASE_URL=MODEL_ID
--api-key KEY --trials 5 --out reports/pydantic-probe.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, NativeOutput, PromptedOutput, RunContext, ToolOutput
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    model_id: str


class Plan(BaseModel):
    steps: list[str]
    needs_code: bool


def parse_endpoint(value: str) -> Endpoint:
    parts = value.split("=", 2)
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError("endpoint must be NAME=BASE_URL=MODEL_ID")
    return Endpoint(*parts)


def make_model(endpoint: Endpoint, api_key: str) -> OpenAIChatModel:
    return OpenAIChatModel(endpoint.model_id, provider=OpenAIProvider(base_url=endpoint.base_url, api_key=api_key))


def _record(name: str, started: float, **values: Any) -> dict[str, Any]:
    return {"name": name, "latency_seconds": round(time.perf_counter() - started, 6), **values}


async def basic_chat(endpoint: Endpoint, api_key: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await Agent(make_model(endpoint, api_key), retries=0).run("Reply with the single word OK")
        answer = str(result.output)
        return _record("basic_chat", started, endpoint=endpoint.name, passed="OK" in answer.upper(), answer=answer)
    except Exception as exc:
        return _record("basic_chat", started, endpoint=endpoint.name, passed=False, error=type(exc).__name__)


async def delegation(first: Endpoint, second: Endpoint, api_key: str) -> dict[str, Any]:
    started = time.perf_counter()
    called = False
    worker = Agent(make_model(second, api_key), retries=0)
    orchestrator = Agent(make_model(first, api_key), retries=0)

    @orchestrator.tool
    async def ask_worker(ctx: RunContext[None], task: str) -> str:
        nonlocal called
        called = True
        result = await worker.run(task, usage=ctx.usage)
        return str(result.output)

    try:
        result = await orchestrator.run("Have the worker add 17 and 25, then report the result.")
        answer = str(result.output)
        return _record("delegation", started, passed=called and "42" in answer, worker_called=called, answer=answer)
    except Exception as exc:
        return _record("delegation", started, passed=False, worker_called=called, error=type(exc).__name__)


async def fallback(endpoint: Endpoint, api_key: str) -> dict[str, Any]:
    started = time.perf_counter()
    dead = Endpoint("dead", "http://127.0.0.1:1/v1", endpoint.model_id)
    errors: list[str] = []

    def record_error(exc: BaseException) -> bool:
        errors.append(type(exc).__name__)
        return isinstance(exc, ModelAPIError)

    model = FallbackModel(make_model(dead, api_key), make_model(endpoint, api_key), fallback_on=record_error)
    try:
        result = await Agent(model, retries=0).run("Reply with the single word OK")
        answer = str(result.output)
        return _record("fallback", started, passed=True, answer=answer, fallback_exceptions=errors)
    except Exception as exc:
        return _record("fallback", started, passed=False, error=type(exc).__name__, fallback_exceptions=errors)


async def structured_output(endpoint: Endpoint, api_key: str, trials: int) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for mode_name, wrapper in (("ToolOutput", ToolOutput), ("PromptedOutput", PromptedOutput), ("NativeOutput", NativeOutput)):
        started = time.perf_counter()
        ok = 0
        errors: list[str] = []
        for _ in range(trials):
            try:
                agent = Agent(make_model(endpoint, api_key), output_type=wrapper(Plan), retries=0)
                await agent.run("Return a short plan with steps and whether code is needed.")
                ok += 1
            except Exception as exc:
                errors.append(type(exc).__name__)
        outcomes.append(_record("structured_output", started, endpoint=endpoint.name, mode=mode_name,
                                trials=trials, successes=ok, success_rate=ok / trials if trials else 0,
                                errors=errors, supported=not (mode_name == "NativeOutput" and errors and ok == 0)))
    return outcomes


async def run_probe(endpoints: list[Endpoint], api_key: str, trials: int) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for endpoint in endpoints:
        records.append(await basic_chat(endpoint, api_key))
    if len(endpoints) >= 2:
        records.append(await delegation(endpoints[0], endpoints[1], api_key))
    records.append(await fallback(endpoints[0], api_key))
    for endpoint in endpoints:
        records.extend(await structured_output(endpoint, api_key, trials))
    return {"version": "2.69.0", "endpoints": [asdict(e) for e in endpoints], "checks": records}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", action="append", type=parse_endpoint, required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--out", type=Path, default=Path("reports/pydantic-probe.json"))
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("--trials must be at least 1")
    report = asyncio.run(run_probe(args.endpoint, args.api_key, args.trials))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
