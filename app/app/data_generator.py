"""Generate sample datasets and load them into NeMo Helix as filesets."""

import json
import logging
import os
import random
import sys
import time
from pathlib import Path

import colorlog
import httpx

logger = logging.getLogger(__name__)

HELIX_URL = os.environ.get("HELIX_URL", "http://localhost:8080")
HELIX_WORKSPACE = os.environ.get("HELIX_WORKSPACE", "default")
DATASET_NAME = os.environ.get("DATASET_NAME", "sample-qa-dataset")
DATASET_SEED = int(os.environ.get("DATASET_SEED", "42"))
TRAIN_ROWS = int(os.environ.get("TRAIN_ROWS", "200"))
VALIDATION_ROWS = int(os.environ.get("VALIDATION_ROWS", "50"))
SINGLE_TURN_DATASET = os.environ.get("SINGLE_TURN_DATASET", "single-turn-evals")
MULTI_TURN_DATASET = os.environ.get("MULTI_TURN_DATASET", "multi-turn-evals")
EVAL_ROWS = int(os.environ.get("EVAL_ROWS", "50"))
READY_TIMEOUT_SECONDS = int(os.environ.get("READY_TIMEOUT_SECONDS", "600"))
READY_POLL_SECONDS = int(os.environ.get("READY_POLL_SECONDS", "5"))
STABLE_CHECKS = int(os.environ.get("STABLE_CHECKS", "3"))
HEALTH_FILE = Path(os.environ.get("HEALTH_FILE", "/tmp/healthy"))

ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "messages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["role", "content"],
            },
        }
    },
    "required": ["messages"],
}

SINGLE_TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "category": {"type": "string"},
        "input": {"type": "string"},
        "reference": {"type": "string"},
    },
    "required": ["id", "input", "reference"],
}

MULTI_TURN_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "category": {"type": "string"},
        "messages": ROW_SCHEMA["properties"]["messages"],
        "reference": {"type": "string"},
    },
    "required": ["id", "messages", "reference"],
}

CITIES = {
    "France": "Paris",
    "Japan": "Tokyo",
    "Canada": "Ottawa",
    "Brazil": "Brasilia",
    "Kenya": "Nairobi",
    "Australia": "Canberra",
    "Germany": "Berlin",
    "India": "New Delhi",
    "Mexico": "Mexico City",
    "Egypt": "Cairo",
}


def mark_healthy(path: Path | None = None) -> None:
    """Write the marker file that the container healthcheck tests for.

    Args:
        path: Marker file to create. Defaults to ``HEALTH_FILE``.
    """
    (path or HEALTH_FILE).touch()


def setup_logging() -> None:
    """Configure colored logging on the root logger."""
    handler = colorlog.StreamHandler()
    handler.setFormatter(
        colorlog.ColoredFormatter(
            "%(log_color)s%(cyan)s%(asctime)s %(levelname)s%(reset)s:  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            log_colors={
                "DEBUG": "white",
                "INFO": "cyan",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "bold_red",
            },
        )
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _arithmetic_row(rng: random.Random) -> dict:
    a, b = rng.randint(1, 999), rng.randint(1, 999)
    op = rng.choice(["+", "-", "*"])
    answer = {"+": a + b, "-": a - b, "*": a * b}[op]
    return {
        "messages": [
            {"role": "user", "content": f"What is {a} {op} {b}?"},
            {"role": "assistant", "content": str(answer)},
        ]
    }


def _capital_row(rng: random.Random) -> dict:
    country = rng.choice(sorted(CITIES))
    return {
        "messages": [
            {"role": "user", "content": f"What is the capital of {country}?"},
            {
                "role": "assistant",
                "content": f"The capital of {country} is {CITIES[country]}.",
            },
        ]
    }


def generate_rows(count: int, rng: random.Random) -> list[dict]:
    """Generate chat-format question/answer rows.

    Args:
        count: Number of rows to generate.
        rng: Random generator, seeded for reproducible output.

    Returns:
        A list of rows, each with a ``messages`` list of user/assistant turns.
    """
    makers = [_arithmetic_row, _capital_row]
    return [rng.choice(makers)(rng) for _ in range(count)]


def generate_single_turn_evals(count: int, rng: random.Random) -> list[dict]:
    """Generate single-turn eval rows with short reference answers.

    Args:
        count: Number of rows to generate.
        rng: Random generator, seeded for reproducible output.

    Returns:
        Rows with ``id``, ``category``, ``input`` and ``reference`` fields.
    """
    rows = []
    for i in range(count):
        if rng.random() < 0.5:
            a, b = rng.randint(1, 99), rng.randint(1, 99)
            row = {
                "category": "arithmetic",
                "input": f"What is {a} + {b}? Reply with just the number.",
                "reference": str(a + b),
            }
        else:
            country = rng.choice(sorted(CITIES))
            row = {
                "category": "capital",
                "input": f"What is the capital of {country}? Reply with just the city.",
                "reference": CITIES[country],
            }
        rows.append({"id": f"st-{i:04d}", **row})
    return rows


def _turns(*pairs: tuple[str, str]) -> list[dict]:
    return [{"role": role, "content": content} for role, content in pairs]


def generate_multi_turn_evals(count: int, rng: random.Random) -> list[dict]:
    """Generate multi-turn eval rows whose last user turn depends on earlier turns.

    Args:
        count: Number of rows to generate.
        rng: Random generator, seeded for reproducible output.

    Returns:
        Rows with ``id``, ``category``, ``messages`` (ending on a user turn) and the
        ``reference`` answer to that final turn.
    """
    rows = []
    for i in range(count):
        kind = rng.choice(["arithmetic-followup", "memory", "context-capital"])
        if kind == "arithmetic-followup":
            a, b, k = rng.randint(1, 50), rng.randint(1, 50), rng.randint(2, 9)
            messages = _turns(
                ("user", f"What is {a} + {b}?"),
                ("assistant", str(a + b)),
                ("user", f"Now multiply that by {k}. Reply with just the number."),
            )
            reference = str((a + b) * k)
        elif kind == "memory":
            n = rng.randint(10, 500)
            messages = _turns(
                ("user", f"Remember the number {n}."),
                ("assistant", f"Got it, I will remember {n}."),
                ("user", "What is double that number? Reply with just the number."),
            )
            reference = str(n * 2)
        else:
            country = rng.choice(sorted(CITIES))
            messages = _turns(
                ("user", f"I am planning a trip to {country}."),
                ("assistant", f"That sounds great! How can I help with {country}?"),
                ("user", "What is its capital? Reply with just the city."),
            )
            reference = CITIES[country]
        rows.append(
            {
                "id": f"mt-{i:04d}",
                "category": kind,
                "messages": messages,
                "reference": reference,
            }
        )
    return rows


def to_jsonl(rows: list[dict]) -> bytes:
    """Serialize rows as JSON Lines.

    Args:
        rows: Rows to serialize.

    Returns:
        UTF-8 encoded JSONL content.
    """
    return "".join(json.dumps(row) + "\n" for row in rows).encode("utf-8")


def wait_until_stable(
    client: httpx.Client,
    timeout: float = READY_TIMEOUT_SECONDS,
    poll: float = READY_POLL_SECONDS,
    stable_checks: int = STABLE_CHECKS,
) -> None:
    """Block until Helix reports all services and controllers healthy.

    The deployment counts as stable only after ``stable_checks`` consecutive
    healthy responses from ``/status``.

    Args:
        client: HTTP client pointed at the Helix base URL.
        timeout: Maximum seconds to wait.
        poll: Seconds between checks.
        stable_checks: Consecutive healthy checks required.

    Raises:
        TimeoutError: If the deployment does not stabilize in time.
    """
    deadline = time.monotonic() + timeout
    healthy_streak = 0
    while time.monotonic() < deadline:
        try:
            resp = client.get("/status")
            body = resp.json() if resp.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("Helix not reachable yet: %s", exc)
            body = {}
        not_ready = body.get("services", {}).get("not_ready", [None])
        controllers_ok = body.get("controllers", {}).get("healthy", False)
        if body.get("status") == "healthy" and not not_ready and controllers_ok:
            healthy_streak += 1
            logger.info("Helix healthy (%d/%d)", healthy_streak, stable_checks)
            if healthy_streak >= stable_checks:
                return
        else:
            healthy_streak = 0
            logger.info("Waiting for Helix, not ready: %s", not_ready)
        time.sleep(poll)
    raise TimeoutError(f"Helix did not become stable within {timeout}s")


def ensure_fileset(
    client: httpx.Client,
    workspace: str,
    name: str,
    description: str,
    schema: dict,
) -> None:
    """Create a dataset fileset, or reuse it if it already exists.

    Args:
        client: HTTP client pointed at the Helix base URL.
        workspace: Helix workspace name.
        name: Fileset name.
        description: Fileset description.
        schema: JSON Schema describing one row.

    Raises:
        httpx.HTTPStatusError: If creation fails for a reason other than a conflict.
    """
    payload = {
        "name": name,
        "description": description,
        "purpose": "dataset",
        "metadata": {"dataset": {"schema": schema}},
    }
    resp = client.post(f"/apis/files/v2/workspaces/{workspace}/filesets", json=payload)
    if resp.status_code == 409:
        logger.info("Fileset %s/%s already exists, reusing it", workspace, name)
        return
    resp.raise_for_status()
    logger.info("Created fileset %s/%s", workspace, name)


def upload_file(
    client: httpx.Client, workspace: str, name: str, path: str, content: bytes
) -> None:
    """Upload one file into a fileset, overwriting any existing copy.

    Args:
        client: HTTP client pointed at the Helix base URL.
        workspace: Helix workspace name.
        name: Fileset name.
        path: Path of the file inside the fileset.
        content: Raw file bytes.

    Raises:
        httpx.HTTPStatusError: If the upload fails.
    """
    resp = client.put(
        f"/apis/files/v2/workspaces/{workspace}/filesets/{name}/-/{path}",
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )
    resp.raise_for_status()
    logger.info("Uploaded %s (%d bytes) to %s/%s", path, len(content), workspace, name)


def load_dataset(
    client: httpx.Client,
    name: str,
    description: str,
    schema: dict,
    files: dict[str, list[dict]],
) -> None:
    """Create a dataset fileset and sync its files into Helix.

    Args:
        client: HTTP client pointed at the Helix base URL.
        name: Fileset name.
        description: Fileset description.
        schema: JSON Schema describing one row.
        files: Rows to upload, keyed by path inside the fileset.
    """
    ensure_fileset(client, HELIX_WORKSPACE, name, description, schema)
    for path, rows in files.items():
        upload_file(client, HELIX_WORKSPACE, name, path, to_jsonl(rows))
    logger.info("Dataset %s/%s loaded", HELIX_WORKSPACE, name)


def run(client: httpx.Client) -> None:
    """Wait for Helix, then generate and load every dataset.

    Args:
        client: HTTP client pointed at the Helix base URL.
    """
    wait_until_stable(client)
    rng = random.Random(DATASET_SEED)
    load_dataset(
        client,
        DATASET_NAME,
        "Synthetic chat Q&A dataset generated by the data-generator service",
        ROW_SCHEMA,
        {
            "training/train.jsonl": generate_rows(TRAIN_ROWS, rng),
            "validation/validation.jsonl": generate_rows(VALIDATION_ROWS, rng),
        },
    )
    load_dataset(
        client,
        SINGLE_TURN_DATASET,
        "Single-turn evals: one question per row with a short reference answer",
        SINGLE_TURN_SCHEMA,
        {"evals.jsonl": generate_single_turn_evals(EVAL_ROWS, rng)},
    )
    load_dataset(
        client,
        MULTI_TURN_DATASET,
        "Multi-turn evals: conversation history ending on a user turn, with a reference answer",
        MULTI_TURN_SCHEMA,
        {"evals.jsonl": generate_multi_turn_evals(EVAL_ROWS, rng)},
    )


def main() -> int:
    """Entry point for the data-generator container.

    Returns:
        Process exit code.
    """
    setup_logging()
    try:
        with httpx.Client(base_url=HELIX_URL, timeout=30) as client:
            run(client)
    except (httpx.HTTPError, TimeoutError) as exc:
        logger.error("Data generation failed: %s", exc)
        return 1
    mark_healthy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
