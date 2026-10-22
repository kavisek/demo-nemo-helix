"""Tests for the bootstrap summary report."""

import httpx
import pytest

from app import summary

FILESETS = {
    "sample-qa-dataset": {"training/train.jsonl": b'{"a":1}\n{"a":2}\n'},
    "single-turn-evals": {"evals.jsonl": b'{"a":1}\n'},
    "multi-turn-evals": {"evals.jsonl": b'{"a":1}\n{"a":2}\n{"a":3}\n'},
}
READY = {
    "status": "READY",
    "served_models": [{"served_model_name": "gemma4:e4b-mlx"}],
}


def make_client(filesets=FILESETS, provider=READY):
    """Build a client that serves fake filesets and a provider."""

    def handler(request):
        path = request.url.path
        if "/providers/" in path:
            if provider is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=provider)
        name = path.split("/filesets/")[1].split("/")[0]
        if name not in filesets:
            return httpx.Response(404, json={})
        if path.endswith("/files"):
            data = [{"path": p} for p in filesets[name]]
            return httpx.Response(200, json={"data": data})
        file_path = path.split("/-/")[1]
        return httpx.Response(200, content=filesets[name][file_path])

    return httpx.Client(base_url="http://helix", transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """Skip sleeps and make provider polling time out immediately."""
    monkeypatch.setattr(summary.time, "sleep", lambda _: None)
    monkeypatch.setattr(summary, "PROVIDER_TIMEOUT_SECONDS", 0)


def test_count_rows_ignores_blank_lines():
    """Blank lines are not rows."""
    assert summary.count_rows(b'{"a":1}\n\n{"a":2}\n') == 2


def test_dataset_files_counts_each_file():
    """Every file in the fileset is counted."""
    counts = summary.dataset_files(make_client(), "default", "sample-qa-dataset")
    assert counts == {"training/train.jsonl": 2}


def test_build_report_lists_datasets_and_provider():
    """A healthy stack reports every dataset and the provider's models."""
    lines, ok = summary.build_report(make_client())
    text = "\n".join(lines)
    assert ok
    assert "single-turn-evals: evals.jsonl (1 rows)" in text
    assert "multi-turn-evals:  evals.jsonl (3 rows)" in text
    assert "ollama: READY -> gemma4:e4b-mlx" in text


def test_build_report_flags_missing_dataset():
    """A missing fileset is reported and fails the check."""
    filesets = {k: v for k, v in FILESETS.items() if k != "multi-turn-evals"}
    lines, ok = summary.build_report(make_client(filesets=filesets))
    assert not ok
    assert any("multi-turn-evals" in line and "MISSING" in line for line in lines)


def test_build_report_flags_provider_without_models():
    """A provider that has not discovered models fails the check."""
    provider = {"status": "CREATED", "served_models": []}
    lines, ok = summary.build_report(make_client(provider=provider))
    assert not ok
    assert any("CREATED -> no models discovered" in line for line in lines)


def test_build_report_flags_missing_provider():
    """A provider that does not exist fails the check."""
    lines, ok = summary.build_report(make_client(provider=None))
    assert not ok
    assert any("ollama: MISSING" in line for line in lines)


def test_wait_for_provider_polls_until_models_discovered(monkeypatch):
    """The provider is polled until it has served models."""
    monkeypatch.setattr(summary, "PROVIDER_TIMEOUT_SECONDS", 60)
    responses = iter([{"status": "READY", "served_models": []}, READY])

    def handler(request):
        return httpx.Response(200, json=next(responses))

    client = httpx.Client(base_url="http://h", transport=httpx.MockTransport(handler))
    assert summary.wait_for_provider(client, "default", "ollama", timeout=60) == READY


def test_main_exit_codes(monkeypatch):
    """main returns 0 when complete, 1 when incomplete or unreachable."""
    monkeypatch.setattr(summary, "build_report", lambda client: (["ok"], True))
    assert summary.main() == 0
    monkeypatch.setattr(summary, "build_report", lambda client: (["x"], False))
    assert summary.main() == 1

    def boom(client):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(summary, "build_report", boom)
    assert summary.main() == 1
