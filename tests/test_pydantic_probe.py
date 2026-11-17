from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

from spikes.pydantic_probe import Endpoint, basic_chat, delegation, fallback
from tests.fakes.openai_server import app


@pytest.fixture
def fake_endpoint() -> Endpoint:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield Endpoint("fake", f"http://127.0.0.1:{port}/v1", "fake-model")
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_basic_chat(fake_endpoint: Endpoint) -> None:
    result = await basic_chat(fake_endpoint, "test-key")
    assert result["passed"] is True
    assert result["latency_seconds"] >= 0


@pytest.mark.asyncio
async def test_delegation_calls_worker(fake_endpoint: Endpoint) -> None:
    result = await delegation(fake_endpoint, fake_endpoint, "test-key")
    assert result["passed"] is True
    assert result["worker_called"] is True


@pytest.mark.asyncio
async def test_fallback_uses_second_model(fake_endpoint: Endpoint) -> None:
    result = await fallback(fake_endpoint, "test-key")
    assert result["passed"] is True
    assert result["fallback_exceptions"]
