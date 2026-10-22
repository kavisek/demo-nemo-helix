"""Print what the bootstrap containers loaded into NeMo Helix."""

import logging
import os
import sys
import time

import httpx

from app.data_generator import DATASET_NAME, MULTI_TURN_DATASET, SINGLE_TURN_DATASET
from app.model_setup import PROVIDER_NAME

logger = logging.getLogger(__name__)

HELIX_URL = os.environ.get("HELIX_URL", "http://localhost:8080")
HELIX_WORKSPACE = os.environ.get("HELIX_WORKSPACE", "default")
PROVIDER_TIMEOUT_SECONDS = int(os.environ.get("PROVIDER_TIMEOUT_SECONDS", "60"))
DATASETS = [DATASET_NAME, SINGLE_TURN_DATASET, MULTI_TURN_DATASET]


def count_rows(content: bytes) -> int:
    """Count non-empty lines in a JSONL file.

    Args:
        content: Raw file bytes.

    Returns:
        Number of non-empty lines.
    """
    return sum(1 for line in content.splitlines() if line.strip())


def dataset_files(client: httpx.Client, workspace: str, name: str) -> dict[str, int]:
    """Return the row count of every file in a dataset fileset.

    Args:
        client: HTTP client pointed at the Helix base URL.
        workspace: Helix workspace name.
        name: Fileset name.

    Returns:
        Row counts keyed by path inside the fileset.

    Raises:
        httpx.HTTPStatusError: If the fileset or a file cannot be read.
    """
    base = f"/apis/files/v2/workspaces/{workspace}/filesets/{name}"
    resp = client.get(f"{base}/files")
    resp.raise_for_status()
    counts = {}
    for item in resp.json()["data"]:
        content = client.get(f"{base}/-/{item['path']}")
        content.raise_for_status()
        counts[item["path"]] = count_rows(content.content)
    return counts


def wait_for_provider(
    client: httpx.Client,
    workspace: str,
    name: str,
    timeout: float | None = None,
    poll: float = 2,
) -> dict:
    """Wait until a provider is READY and has discovered its models.

    Args:
        client: HTTP client pointed at the Helix base URL.
        workspace: Helix workspace name.
        name: Provider name.
        timeout: Maximum seconds to wait. Defaults to ``PROVIDER_TIMEOUT_SECONDS``.
        poll: Seconds between checks.

    Returns:
        The provider as last returned by Helix, ready or not.
    """
    if timeout is None:
        timeout = PROVIDER_TIMEOUT_SECONDS
    deadline = time.monotonic() + timeout
    provider: dict = {}
    while True:
        resp = client.get(f"/apis/models/v2/workspaces/{workspace}/providers/{name}")
        if resp.status_code == 200:
            provider = resp.json()
            if provider.get("status") == "READY" and provider.get("served_models"):
                return provider
        if time.monotonic() >= deadline:
            return provider
        time.sleep(poll)


def build_report(client: httpx.Client) -> tuple[list[str], bool]:
    """Build the summary lines for datasets and the model provider.

    Args:
        client: HTTP client pointed at the Helix base URL.

    Returns:
        The report lines, and whether everything was found.
    """
    ok = True
    lines = [f"Datasets (workspace: {HELIX_WORKSPACE}):"]
    width = max(len(name) for name in DATASETS) + 1
    for name in DATASETS:
        try:
            files = dataset_files(client, HELIX_WORKSPACE, name)
        except httpx.HTTPStatusError:
            files = {}
        if not files:
            ok = False
            lines.append(f"  • {name + ':':<{width}} MISSING")
            continue
        detail = ", ".join(f"{path} ({rows} rows)" for path, rows in files.items())
        lines.append(f"  • {name + ':':<{width}} {detail}")
    provider = wait_for_provider(client, HELIX_WORKSPACE, PROVIDER_NAME)
    models = [m["served_model_name"] for m in provider.get("served_models") or []]
    status = provider.get("status", "MISSING")
    if status != "READY" or not models:
        ok = False
    lines += [
        "",
        "Model Provider:",
        f"  • {PROVIDER_NAME}: {status} -> {', '.join(models) or 'no models discovered'}",
    ]
    return lines, ok


def main() -> int:
    """Entry point for ``python -m app.summary``.

    Returns:
        0 if every dataset and the provider are present, otherwise 1.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        with httpx.Client(base_url=HELIX_URL, timeout=30) as client:
            lines, ok = build_report(client)
    except httpx.HTTPError as exc:
        logger.error("Could not reach Helix: %s", exc)
        return 1
    for line in lines:
        logger.info(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
