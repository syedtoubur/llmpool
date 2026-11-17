"""Small scripted OpenAI Chat Completions API for offline tests."""

from __future__ import annotations

from fastapi import FastAPI, Request

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> dict:
    body = await request.json()
    messages = body.get("messages", [])
    tools = body.get("tools", [])
    tool_result = next((m for m in messages if m.get("role") == "tool"), None)
    if tool_result:
        content = "The worker added them and got 42."
        message = {"role": "assistant", "content": content}
    elif tools and any("ask_worker" in str(tool) for tool in tools):
        message = {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_worker", "type": "function", "function": {
                "name": "ask_worker", "arguments": '{"task":"Add 17 and 25"}'
            }}],
        }
    elif any("Add 17 and 25" in str(m.get("content", "")) for m in messages):
        message = {"role": "assistant", "content": "42"}
    else:
        message = {"role": "assistant", "content": "OK"}
    return {"id": "fake-chat", "object": "chat.completion", "created": 1,
            "model": body.get("model", "fake"), "choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if "tool_calls" in message else "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
