"""Send Airflow DAG runs to NeMo Helix Intake as traces.

Each DAG run becomes one trace and one session on the Helix Traces page: a
root span for the run and a child span for every task attempt. The spans are
posted from task and DAG callbacks through Intake's direct span ingest, so the
tasks themselves need no tracing code. Posting never fails a task: errors are
logged as warnings.
"""

import logging
import os
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

HELIX_URL = os.environ.get("NEMO_HELIX_URL", "http://host.docker.internal:7001")
WORKSPACE = os.environ.get("NEMO_HELIX_WORKSPACE", "default")
INGEST_SPANS_URL = f"{HELIX_URL}/apis/intake/v2/workspaces/{WORKSPACE}/ingest/spans"
SOURCE = "airflow"
ROOT_SPAN_ID = "dag-run"
REQUEST_TIMEOUT = 10


def utc_iso(value: datetime | None) -> str:
    """Format a datetime as ISO 8601 in UTC, using now when it is missing.

    Args:
        value: Aware datetime, or None.

    Returns:
        The timestamp with a UTC offset, as Intake requires.
    """
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()


def run_ids(context: dict) -> tuple[str, str]:
    """Return the DAG ID and run ID from a callback context.

    Args:
        context: Airflow callback context.

    Returns:
        A (dag_id, run_id) tuple.
    """
    dag_run = context.get("dag_run")
    run_id = dag_run.run_id if dag_run is not None else context["run_id"]
    return context["dag"].dag_id, run_id


def run_span_fields(dag_id: str, run_id: str) -> dict:
    """Build the fields every span of one DAG run shares.

    Args:
        dag_id: Airflow DAG ID, used as the agent name.
        run_id: Airflow run ID.

    Returns:
        trace_id, session_id, and the base attributes.
    """
    trace_id = f"{dag_id}/{run_id}"
    return {
        "trace_id": trace_id,
        "session_id": trace_id,
        "attributes": {
            "gen_ai.agent.name": dag_id,
            "airflow.dag_id": dag_id,
            "airflow.run_id": run_id,
        },
    }


def error_attributes(error: BaseException | str | None) -> dict:
    """Map a task error to Intake's semantic error attributes.

    Args:
        error: The exception or message from the callback context.

    Returns:
        exception.type and exception.message, or an empty dict.
    """
    if error is None:
        return {}
    if isinstance(error, BaseException):
        return {
            "exception.type": type(error).__name__,
            "exception.message": str(error),
        }
    return {"exception.type": "AirflowError", "exception.message": str(error)}


def task_output(ti) -> object:
    """Read a task's return value from XCom for the span output.

    Args:
        ti: The runtime task instance.

    Returns:
        The XCom return value, or None when there is none or it can't be read.
    """
    try:
        return ti.xcom_pull(task_ids=ti.task_id, map_indexes=ti.map_index)
    except Exception as exc:
        logger.warning("Could not read XCom for %s: %s", ti.task_id, exc)
        return None


def task_span(context: dict, status: str) -> dict:
    """Build the span for one task attempt.

    Args:
        context: Airflow task callback context.
        status: Intake span status, "success" or "error".

    Returns:
        A direct-ingest span, child of the DAG run's root span.
    """
    ti = context["ti"]
    dag_id, run_id = run_ids(context)
    fields = run_span_fields(dag_id, run_id)
    name = ti.task_id
    if ti.map_index is not None and ti.map_index >= 0:
        name = f"{ti.task_id}[{ti.rendered_map_index or ti.map_index}]"
    fields["attributes"].update(
        {
            "airflow.task_id": ti.task_id,
            "airflow.map_index": ti.map_index,
            "airflow.try_number": ti.try_number,
            **error_attributes(context.get("exception")),
        }
    )
    return {
        **fields,
        "span_id": f"{name}#try{ti.try_number}",
        "parent_span_id": ROOT_SPAN_ID,
        "name": name,
        "kind": "TOOL",
        "status": status,
        "started_at": utc_iso(ti.start_date),
        "ended_at": utc_iso(ti.end_date),
        "output": task_output(ti) if status == "success" else None,
    }


def dag_run_span(context: dict, status: str) -> dict:
    """Build the root span for a finished DAG run.

    Args:
        context: Airflow DAG callback context.
        status: Intake span status, "success" or "error".

    Returns:
        A direct-ingest root span covering the whole run.
    """
    dag_id, run_id = run_ids(context)
    dag_run = context.get("dag_run")
    fields = run_span_fields(dag_id, run_id)
    if status == "error":
        fields["attributes"].update(error_attributes(context.get("reason")))
    started_at = getattr(dag_run, "start_date", None)
    return {
        **fields,
        "span_id": ROOT_SPAN_ID,
        "name": dag_id,
        "kind": "CHAIN",
        "status": status,
        "started_at": utc_iso(started_at),
        "ended_at": utc_iso(getattr(dag_run, "end_date", None)),
        "input": dict(context.get("params") or {}),
        "output": {"state": status, "reason": context.get("reason")},
    }


def post_spans(spans: list[dict]) -> None:
    """Post spans to Intake, logging instead of raising on failure.

    Args:
        spans: Direct-ingest spans from one DAG run.
    """
    try:
        response = requests.post(
            INGEST_SPANS_URL,
            json={"source": SOURCE, "spans": spans},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("Could not send trace spans to Helix: %s", exc)
        return
    if not response.ok:
        logger.warning(
            "Helix rejected trace spans: %s %s", response.status_code, response.text
        )
        return
    logger.info("Sent %s span(s) to Helix trace %s", len(spans), spans[0]["trace_id"])


def on_task_success(context: dict) -> None:
    """Task callback: record a successful attempt."""
    post_spans([task_span(context, "success")])


def on_task_failure(context: dict) -> None:
    """Task callback: record a failed attempt, including ones that will retry."""
    post_spans([task_span(context, "error")])


def on_dag_run_success(context: dict) -> None:
    """DAG callback: record the run's root span as successful."""
    post_spans([dag_run_span(context, "success")])


def on_dag_run_failure(context: dict) -> None:
    """DAG callback: record the run's root span as failed."""
    post_spans([dag_run_span(context, "error")])


TASK_CALLBACKS = {
    "on_success_callback": on_task_success,
    "on_failure_callback": on_task_failure,
    "on_retry_callback": on_task_failure,
}
DAG_CALLBACKS = {
    "on_success_callback": on_dag_run_success,
    "on_failure_callback": on_dag_run_failure,
}
