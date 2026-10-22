"""Tests for the data generator."""

import json
import random

import httpx
import pytest

from app import data_generator

HEALTHY = {
    "status": "healthy",
    "services": {"ready": ["files"], "not_ready": []},
    "controllers": {"healthy": True},
}
NOT_READY = {
    "status": "degraded",
    "services": {"ready": [], "not_ready": ["files"]},
    "controllers": {"healthy": False},
}


def make_client(handler):
    """Build an httpx client backed by a mock transport."""
    return httpx.Client(base_url="http://helix", transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Skip real sleeps while polling."""
    monkeypatch.setattr(data_generator.time, "sleep", lambda _: None)


@pytest.fixture(autouse=True)
def health_file(monkeypatch, tmp_path):
    """Point the healthcheck marker at a temp file."""
    path = tmp_path / "healthy"
    monkeypatch.setattr(data_generator, "HEALTH_FILE", path)
    return path


def test_generate_rows_is_reproducible_and_well_formed():
    """Rows have user/assistant turns and the same seed gives the same rows."""
    rows = data_generator.generate_rows(20, random.Random(1))
    assert rows == data_generator.generate_rows(20, random.Random(1))
    assert len(rows) == 20
    for row in rows:
        assert [m["role"] for m in row["messages"]] == ["user", "assistant"]


def test_to_jsonl_round_trips():
    """Each line parses back to the original row."""
    rows = [{"messages": [{"role": "user", "content": "hi"}]}] * 3
    lines = data_generator.to_jsonl(rows).decode().splitlines()
    assert [json.loads(line) for line in lines] == rows


def test_wait_until_stable_requires_consecutive_healthy_checks():
    """A not-ready response resets the healthy streak."""
    responses = iter([HEALTHY, NOT_READY, HEALTHY, HEALTHY])
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=next(responses))

    data_generator.wait_until_stable(
        make_client(handler), timeout=60, poll=0, stable_checks=2
    )
    assert len(calls) == 4


def test_wait_until_stable_tolerates_connection_errors():
    """Connection errors are retried rather than raised."""
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json=HEALTHY)

    data_generator.wait_until_stable(
        make_client(handler), timeout=60, poll=0, stable_checks=1
    )
    assert attempts["n"] == 2


def test_wait_until_stable_times_out(monkeypatch):
    """A deployment that never stabilizes raises TimeoutError."""
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(data_generator.time, "monotonic", lambda: next(clock))

    def handler(request):
        return httpx.Response(200, json=NOT_READY)

    with pytest.raises(TimeoutError):
        data_generator.wait_until_stable(make_client(handler), timeout=30, poll=0)


def test_ensure_fileset_creates_dataset_fileset():
    """The fileset is created with dataset purpose and a row schema."""
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={})

    schema = {"type": "object"}
    data_generator.ensure_fileset(make_client(handler), "default", "ds", "desc", schema)
    assert seen["path"] == "/apis/files/v2/workspaces/default/filesets"
    assert seen["body"]["name"] == "ds"
    assert seen["body"]["description"] == "desc"
    assert seen["body"]["purpose"] == "dataset"
    assert seen["body"]["metadata"]["dataset"]["schema"] == schema


def test_ensure_fileset_reuses_existing():
    """A 409 conflict is treated as success."""
    client = make_client(lambda request: httpx.Response(409, json={}))
    data_generator.ensure_fileset(client, "default", "ds", "desc", {})


def test_ensure_fileset_raises_on_error():
    """Other errors propagate."""
    client = make_client(lambda request: httpx.Response(500, json={}))
    with pytest.raises(httpx.HTTPStatusError):
        data_generator.ensure_fileset(client, "default", "ds", "desc", {})


def test_upload_file_puts_raw_bytes():
    """Files are uploaded as an octet stream to the fileset content path."""
    seen = {}

    def handler(request):
        seen.update(
            method=request.method,
            path=request.url.path,
            content=request.content,
            ctype=request.headers["content-type"],
        )
        return httpx.Response(200, json={})

    data_generator.upload_file(make_client(handler), "default", "ds", "a/b.jsonl", b"x")
    assert seen == {
        "method": "PUT",
        "path": "/apis/files/v2/workspaces/default/filesets/ds/-/a/b.jsonl",
        "content": b"x",
        "ctype": "application/octet-stream",
    }


def test_generate_single_turn_evals_shape():
    """Single-turn rows have unique ids, an input and a correct short reference."""
    rows = data_generator.generate_single_turn_evals(40, random.Random(3))
    assert len({r["id"] for r in rows}) == 40
    assert {r["category"] for r in rows} == {"arithmetic", "capital"}
    for row in rows:
        assert set(row) == {"id", "category", "input", "reference"}
        if row["category"] == "arithmetic":
            a, b = [int(t) for t in row["input"].split("?")[0][8:].split(" + ")]
            assert row["reference"] == str(a + b)
        else:
            assert row["reference"] in data_generator.CITIES.values()


def test_generate_multi_turn_evals_shape():
    """Multi-turn rows alternate roles, end on a user turn and need prior context."""
    rows = data_generator.generate_multi_turn_evals(60, random.Random(5))
    assert {r["category"] for r in rows} == {
        "arithmetic-followup",
        "memory",
        "context-capital",
    }
    for row in rows:
        roles = [m["role"] for m in row["messages"]]
        assert roles == ["user", "assistant", "user"]
        history = row["messages"][0]["content"]
        if row["category"] == "memory":
            n = int(history.split()[-1].rstrip("."))
            assert row["reference"] == str(n * 2)
        elif row["category"] == "context-capital":
            country = history.removeprefix("I am planning a trip to ").rstrip(".")
            assert row["reference"] == data_generator.CITIES[country]
        else:
            k = int(row["messages"][2]["content"].split("by ")[1].split(".")[0])
            assert row["reference"] == str(int(row["messages"][1]["content"]) * k)


def test_eval_rows_match_their_schemas():
    """Generated eval rows contain every field their fileset schema requires."""
    rng = random.Random(0)
    for rows, schema in [
        (
            data_generator.generate_single_turn_evals(5, rng),
            data_generator.SINGLE_TURN_SCHEMA,
        ),
        (
            data_generator.generate_multi_turn_evals(5, rng),
            data_generator.MULTI_TURN_SCHEMA,
        ),
    ]:
        for row in rows:
            assert set(schema["required"]) <= set(row)


def test_run_loads_all_datasets(monkeypatch):
    """run creates the QA, single-turn and multi-turn filesets and syncs their files."""
    monkeypatch.setattr(data_generator, "TRAIN_ROWS", 5)
    monkeypatch.setattr(data_generator, "VALIDATION_ROWS", 2)
    monkeypatch.setattr(data_generator, "EVAL_ROWS", 3)
    created = {}
    uploads = {}

    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            created[body["name"]] = body["metadata"]["dataset"]["schema"]
        if request.method == "PUT":
            fileset, path = request.url.path.split("/filesets/")[1].split("/-/")
            uploads[f"{fileset}#{path}"] = request.content
        return httpx.Response(201, json={})

    monkeypatch.setattr(
        data_generator,
        "wait_until_stable",
        lambda client: None,
    )
    data_generator.run(make_client(handler))
    assert created == {
        "sample-qa-dataset": data_generator.ROW_SCHEMA,
        "single-turn-evals": data_generator.SINGLE_TURN_SCHEMA,
        "multi-turn-evals": data_generator.MULTI_TURN_SCHEMA,
    }
    assert {k: len(v.splitlines()) for k, v in uploads.items()} == {
        "sample-qa-dataset#training/train.jsonl": 5,
        "sample-qa-dataset#validation/validation.jsonl": 2,
        "single-turn-evals#evals.jsonl": 3,
        "multi-turn-evals#evals.jsonl": 3,
    }


def test_main_returns_error_code_on_failure(monkeypatch, health_file):
    """main logs and returns 1 when loading fails."""

    def boom(client):
        raise TimeoutError("never stable")

    monkeypatch.setattr(data_generator, "run", boom)
    assert data_generator.main() == 1
    assert not health_file.exists()


def test_main_returns_zero_on_success(monkeypatch, health_file):
    """main returns 0 and marks the container healthy when loading succeeds."""
    monkeypatch.setattr(data_generator, "run", lambda client: None)
    assert data_generator.main() == 0
    assert health_file.exists()
