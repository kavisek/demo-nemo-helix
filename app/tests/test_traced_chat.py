"""Tests for the traced chat helper."""

import json

import httpx
import pytest

from app import traced_chat

CHAT_PATH = traced_chat.chat_completions_path("default")
SPANS_PATH = "/apis/intake/v2/workspaces/default/ingest/spans"
CHAT_RESPONSE = {
    "model": "default/gemma4-e4b-mlx",
    "choices": [{"message": {"role": "assistant", "content": "Paris"}}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 1, "total_tokens": 13},
}


def make_client(handler):
    """Build an httpx client backed by a mock transport."""
    return httpx.Client(base_url="http://helix", transport=httpx.MockTransport(handler))


def recording_handler(chat_status=200):
    """Return a handler that answers chat calls and records ingested spans."""
    spans = []

    def handler(request):
        if request.url.path == CHAT_PATH:
            if chat_status != 200:
                return httpx.Response(chat_status, json={"error": "down"})
            return httpx.Response(200, json=CHAT_RESPONSE)
        assert request.url.path == SPANS_PATH
        body = json.loads(request.content)
        assert body["source"] == traced_chat.TRACE_SOURCE
        spans.extend(body["spans"])
        return httpx.Response(201)

    return handler, spans


def test_traced_chat_returns_reply_and_records_llm_span():
    """A successful call returns the reply and records a span with usage."""
    handler, spans = recording_handler()
    reply = traced_chat.traced_chat(make_client(handler), "Capital of France?", "s1")
    assert reply == "Paris"
    assert len(spans) == 1
    span = spans[0]
    assert span["kind"] == "LLM"
    assert span["status"] == "success"
    assert span["session_id"] == "s1"
    assert span["input"] == [{"role": "user", "content": "Capital of France?"}]
    assert span["output"]["content"] == "Paris"
    assert span["attributes"]["gen_ai.usage.total_tokens"] == 13
    assert span["attributes"]["gen_ai.agent.name"] == traced_chat.AGENT_NAME
    assert span["ended_at"] >= span["started_at"]


def test_traced_chat_records_error_span_and_raises():
    """A failed call still records an error span, then raises."""
    handler, spans = recording_handler(chat_status=503)
    with pytest.raises(httpx.HTTPStatusError):
        traced_chat.traced_chat(make_client(handler), "hi", "s2")
    assert spans[0]["status"] == "error"
    assert spans[0]["output"] is None
    assert "503" in spans[0]["attributes"]["exception.message"]


def test_record_span_raises_when_intake_rejects():
    """Intake rejections propagate."""
    client = make_client(lambda request: httpx.Response(422, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        traced_chat.record_span(client, "default", {"session_id": "s", "trace_id": "t"})


def test_main_exit_codes(monkeypatch):
    """main returns 0 on success and 1 when the call fails."""
    monkeypatch.setattr(traced_chat, "traced_chat", lambda *args: "ok")
    assert traced_chat.main(["hello", "--session", "s3"]) == 0

    def boom(*args):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(traced_chat, "traced_chat", boom)
    assert traced_chat.main(["hello"]) == 1
