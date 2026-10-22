"""Chat with a model through the Helix inference gateway and trace the call.

Sends one chat completion to the gateway, which routes it to Ollama, then
records the call as an LLM span on the Helix Traces page through Intake's
direct span ingest. Calls that share a session ID are grouped together.

Usage: python -m app.traced_chat "What is the capital of France?" [--session ID]
"""

import argparse
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from app.data_generator import setup_logging

logger = logging.getLogger(__name__)

HELIX_URL = os.environ.get("HELIX_URL", "http://localhost:8080")
HELIX_WORKSPACE = os.environ.get("HELIX_WORKSPACE", "default")
CHAT_MODEL = os.environ.get("CHAT_MODEL", "default/gemma4-e4b-mlx")
PROVIDER_NAME = os.environ.get("PROVIDER_NAME", "ollama")
AGENT_NAME = os.environ.get("CHAT_AGENT_NAME", "nemo-helix-chat")
TRACE_SOURCE = "traced-chat"
CHAT_TIMEOUT = 300


def chat_completions_path(workspace: str) -> str:
    """Return the gateway's OpenAI-compatible chat completions path.

    Args:
        workspace: Helix workspace name.

    Returns:
        The API path for chat completions routed by model name.
    """
    return f"/apis/inference-gateway/v2/workspaces/{workspace}/openai/-/v1/chat/completions"


def build_llm_span(
    request: dict,
    response: dict | None,
    error: str | None,
    started_at: datetime,
    ended_at: datetime,
    session_id: str,
) -> dict:
    """Build an Intake LLM span for one chat completion.

    Args:
        request: The chat completion request body.
        response: The response body, or None when the call failed.
        error: Error message when the call failed.
        started_at: When the request was sent (UTC).
        ended_at: When the response or error arrived (UTC).
        session_id: Session that groups related calls.

    Returns:
        A direct-ingest span.
    """
    span_id = uuid.uuid4().hex
    attributes = {
        "gen_ai.agent.name": AGENT_NAME,
        "gen_ai.provider.name": PROVIDER_NAME,
        "gen_ai.request.model": request["model"],
    }
    output = None
    if response is not None:
        usage = response.get("usage") or {}
        attributes.update(
            {
                "gen_ai.response.model": response.get("model", request["model"]),
                "gen_ai.usage.input_tokens": usage.get("prompt_tokens", 0),
                "gen_ai.usage.output_tokens": usage.get("completion_tokens", 0),
                "gen_ai.usage.total_tokens": usage.get("total_tokens", 0),
            }
        )
        output = response["choices"][0]["message"]
    if error is not None:
        attributes.update(
            {"exception.type": "ChatCompletionError", "exception.message": error}
        )
    return {
        "span_id": span_id,
        "trace_id": span_id,
        "session_id": session_id,
        "name": "chat.completions",
        "kind": "LLM",
        "status": "error" if error is not None else "success",
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "input": request["messages"],
        "output": output,
        "attributes": attributes,
    }


def record_span(client: httpx.Client, workspace: str, span: dict) -> None:
    """Send one span to Intake.

    Args:
        client: HTTP client pointed at the Helix base URL.
        workspace: Helix workspace name.
        span: Direct-ingest span.

    Raises:
        httpx.HTTPStatusError: If Intake rejects the span.
    """
    resp = client.post(
        f"/apis/intake/v2/workspaces/{workspace}/ingest/spans",
        json={"source": TRACE_SOURCE, "spans": [span]},
    )
    resp.raise_for_status()
    logger.info(
        "Traced call as session %s, trace %s", span["session_id"], span["trace_id"]
    )


def traced_chat(
    client: httpx.Client,
    prompt: str,
    session_id: str,
    model: str = CHAT_MODEL,
    workspace: str = HELIX_WORKSPACE,
) -> str:
    """Send a prompt through the gateway and record the call in Intake.

    The span is recorded whether the call succeeds or fails.

    Args:
        client: HTTP client pointed at the Helix base URL.
        prompt: User message.
        session_id: Session that groups related calls.
        model: Gateway model name, such as "default/gemma4-e4b-mlx".
        workspace: Helix workspace name.

    Returns:
        The assistant's reply.

    Raises:
        httpx.HTTPError: If the chat call or the trace upload fails.
    """
    request = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    started_at = datetime.now(timezone.utc)
    started = time.monotonic()
    response = None
    error = None
    try:
        resp = client.post(
            chat_completions_path(workspace), json=request, timeout=CHAT_TIMEOUT
        )
        resp.raise_for_status()
        response = resp.json()
    except httpx.HTTPError as exc:
        error = str(exc)
        raise
    finally:
        ended_at = started_at + timedelta(seconds=time.monotonic() - started)
        record_span(
            client,
            workspace,
            build_llm_span(request, response, error, started_at, ended_at, session_id),
        )
    return response["choices"][0]["message"]["content"]


def main(argv: list[str] | None = None) -> int:
    """Entry point: send one prompt and log the reply.

    Args:
        argv: Command-line arguments, defaulting to sys.argv.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("prompt")
    parser.add_argument("--session", default=f"chat-{uuid.uuid4().hex[:12]}")
    parser.add_argument("--model", default=CHAT_MODEL)
    args = parser.parse_args(argv)
    setup_logging()
    try:
        with httpx.Client(base_url=HELIX_URL, timeout=30) as client:
            reply = traced_chat(client, args.prompt, args.session, args.model)
    except httpx.HTTPError as exc:
        logger.error("Traced chat failed: %s", exc)
        return 1
    logger.info("%s: %s", args.model, reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
