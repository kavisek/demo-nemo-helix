"""Register a local Ollama model provider in NeMo Helix."""

import logging
import os
import sys

import httpx

from app.data_generator import mark_healthy, setup_logging, wait_until_stable

logger = logging.getLogger(__name__)

HELIX_URL = os.environ.get("HELIX_URL", "http://localhost:8080")
HELIX_WORKSPACE = os.environ.get("HELIX_WORKSPACE", "default")
PROVIDER_NAME = os.environ.get("PROVIDER_NAME", "ollama")
PROVIDER_URL = os.environ.get("PROVIDER_URL", "http://host.docker.internal:11434/v1")
PROVIDER_MODELS = [
    m.strip()
    for m in os.environ.get("PROVIDER_MODELS", "gemma4:e4b-mlx").split(",")
    if m.strip()
]


def upsert_provider(
    client: httpx.Client,
    workspace: str,
    name: str,
    host_url: str,
    models: list[str],
) -> dict:
    """Create or update an OpenAI-compatible model provider.

    Args:
        client: HTTP client pointed at the Helix base URL.
        workspace: Helix workspace name.
        name: Provider name.
        host_url: OpenAI-compatible base URL of the provider.
        models: Model names to enable from the provider.

    Returns:
        The provider as returned by Helix.

    Raises:
        httpx.HTTPStatusError: If the upsert fails.
    """
    payload = {
        "description": "Local Ollama running on the Docker host",
        "host_url": host_url,
        "enabled_models": models,
    }
    resp = client.put(
        f"/apis/models/v2/workspaces/{workspace}/providers/{name}", json=payload
    )
    resp.raise_for_status()
    logger.info("Provider %s/%s -> %s models=%s", workspace, name, host_url, models)
    return resp.json()


def run(client: httpx.Client) -> None:
    """Wait for Helix, then register the provider.

    Args:
        client: HTTP client pointed at the Helix base URL.
    """
    wait_until_stable(client)
    upsert_provider(
        client, HELIX_WORKSPACE, PROVIDER_NAME, PROVIDER_URL, PROVIDER_MODELS
    )


def main() -> int:
    """Entry point for the model-setup container.

    Returns:
        Process exit code.
    """
    setup_logging()
    try:
        with httpx.Client(base_url=HELIX_URL, timeout=30) as client:
            run(client)
    except (httpx.HTTPError, TimeoutError) as exc:
        logger.error("Model provider setup failed: %s", exc)
        return 1
    mark_healthy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
