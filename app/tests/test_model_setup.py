"""Tests for the model provider setup."""

import json

import httpx

from app import data_generator, model_setup


def make_client(handler):
    """Build an httpx client backed by a mock transport."""
    return httpx.Client(base_url="http://helix", transport=httpx.MockTransport(handler))


def test_upsert_provider_puts_provider_config():
    """The provider is upserted with its host URL and enabled models."""
    seen = {}

    def handler(request):
        seen.update(
            method=request.method,
            path=request.url.path,
            body=json.loads(request.content),
        )
        return httpx.Response(200, json={"name": "ollama", "status": "CREATED"})

    result = model_setup.upsert_provider(
        make_client(handler), "default", "ollama", "http://ollama/v1", ["m1"]
    )
    assert result["name"] == "ollama"
    assert seen["method"] == "PUT"
    assert seen["path"] == "/apis/models/v2/workspaces/default/providers/ollama"
    assert seen["body"]["host_url"] == "http://ollama/v1"
    assert seen["body"]["enabled_models"] == ["m1"]


def test_upsert_provider_raises_on_error():
    """Errors from Helix propagate."""
    client = make_client(lambda request: httpx.Response(422, json={}))
    try:
        model_setup.upsert_provider(client, "default", "ollama", "http://x", [])
    except httpx.HTTPStatusError:
        return
    raise AssertionError("expected HTTPStatusError")


def test_run_waits_then_upserts(monkeypatch):
    """run waits for stability before registering the provider."""
    calls = []
    monkeypatch.setattr(
        model_setup, "wait_until_stable", lambda c: calls.append("wait")
    )
    monkeypatch.setattr(
        model_setup, "upsert_provider", lambda *args: calls.append(args[1:])
    )
    model_setup.run(make_client(lambda request: httpx.Response(200)))
    assert calls == [
        "wait",
        (
            model_setup.HELIX_WORKSPACE,
            model_setup.PROVIDER_NAME,
            model_setup.PROVIDER_URL,
            model_setup.PROVIDER_MODELS,
        ),
    ]


def test_main_exit_codes(monkeypatch, tmp_path):
    """main returns 0 and marks healthy on success, and 1 on failure."""
    health_file = tmp_path / "healthy"
    monkeypatch.setattr(data_generator, "HEALTH_FILE", health_file)

    def boom_first(client):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(model_setup, "run", boom_first)
    assert model_setup.main() == 1
    assert not health_file.exists()

    monkeypatch.setattr(model_setup, "run", lambda client: None)
    assert model_setup.main() == 0
    assert health_file.exists()

    def boom(client):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(model_setup, "run", boom)
    assert model_setup.main() == 1
