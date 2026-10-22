"""Small NeMo Helix REST client shared by the DAGs in this folder."""

import logging
import os
import re
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

HELIX_URL = os.environ.get("NEMO_HELIX_URL", "http://host.docker.internal:7001")
WORKSPACE = os.environ.get("NEMO_HELIX_WORKSPACE", "default")
REQUEST_TIMEOUT = 30
TRANSFER_TIMEOUT = 300
JOB_FAILED_STATES = {"error", "cancelled", "cancelling"}

AIRFLOW_UI_URL = os.environ.get("AIRFLOW_UI_URL", "http://localhost:7002")
JOB_NAME_MAX_LENGTH = 51

FILES_API = f"/apis/files/v2/workspaces/{WORKSPACE}"


def helix_request(method: str, path: str, allow_conflict: bool = False, **kwargs):
    """Call the NeMo Helix API and return the decoded JSON body.

    Args:
        method: HTTP method, such as "GET" or "POST".
        path: API path starting with "/", appended to NEMO_HELIX_URL.
        allow_conflict: Return None instead of raising on 409, for create calls
            whose resource may already exist.
        **kwargs: Passed through to requests.request (json, params, ...).

    Returns:
        The response JSON, or None for an allowed 409.

    Raises:
        requests.HTTPError: The API returned an error status.
    """
    response = requests.request(
        method, f"{HELIX_URL}{path}", timeout=REQUEST_TIMEOUT, **kwargs
    )
    if allow_conflict and response.status_code == 409:
        logger.info("%s %s: already exists, reusing it", method, path)
        return None
    if not response.ok:
        logger.error(
            "%s %s failed: %s %s", method, path, response.status_code, response.text
        )
    response.raise_for_status()
    return response.json()


def split_fileset_ref(ref: str) -> tuple[str, str]:
    """Split a fileset ref into its fileset name and file path.

    Args:
        ref: Ref like "default/my-fileset#dir/file.parquet".

    Returns:
        A (fileset, path) tuple, for example ("my-fileset", "dir/file.parquet").
    """
    fileset, _, path = ref.partition("#")
    return fileset.split("/")[-1], path


def list_fileset_files(fileset: str, prefix: str = "") -> list[str]:
    """List the file paths in a fileset that start with a prefix.

    Args:
        fileset: Fileset name in the workspace.
        prefix: Only return paths starting with this.

    Returns:
        Sorted file paths.
    """
    body = helix_request(
        "GET", f"{FILES_API}/filesets/{fileset}/files", params={"page_size": 1000}
    )
    return sorted(f["path"] for f in body["data"] if f["path"].startswith(prefix))


def download(path: str) -> bytes:
    """Download raw bytes from a Helix API path.

    Args:
        path: API path starting with "/", appended to NEMO_HELIX_URL.

    Returns:
        The response body.

    Raises:
        requests.HTTPError: The download failed.
    """
    response = requests.get(f"{HELIX_URL}{path}", timeout=TRANSFER_TIMEOUT)
    response.raise_for_status()
    return response.content


def download_file(fileset: str, path: str) -> bytes:
    """Download one file from a fileset.

    Args:
        fileset: Fileset name in the workspace.
        path: File path inside the fileset.

    Returns:
        The file bytes.
    """
    return download(f"{FILES_API}/filesets/{fileset}/-/{path}")


def ensure_fileset(name: str, description: str) -> None:
    """Create a dataset fileset unless it already exists.

    Args:
        name: Fileset name.
        description: Fileset description.
    """
    helix_request(
        "POST",
        f"{FILES_API}/filesets",
        allow_conflict=True,
        json={"name": name, "description": description, "purpose": "dataset"},
    )


def upload_file(fileset: str, path: str, content: bytes) -> str:
    """Upload one file into a fileset, overwriting any existing copy.

    Args:
        fileset: Fileset name in the workspace.
        path: File path inside the fileset.
        content: Raw file bytes.

    Returns:
        The fileset ref of the uploaded file, "<workspace>/<fileset>#<path>".

    Raises:
        requests.HTTPError: The upload failed.
    """
    response = requests.put(
        f"{HELIX_URL}{FILES_API}/filesets/{fileset}/-/{path}",
        data=content,
        headers={"Content-Type": "application/octet-stream"},
        timeout=TRANSFER_TIMEOUT,
    )
    response.raise_for_status()
    logger.info("Uploaded %s (%d bytes) to %s", path, len(content), fileset)
    return f"{WORKSPACE}/{fileset}#{path}"


def job_name_for_run(name_tag: str, run_id: str, try_number: int) -> str:
    """Name an evaluator job after the Airflow run that submits it.

    Helix limits job names three ways: the name pattern
    ^[a-z](?!.*--)[a-z0-9-@.+_]{1,62}(?<!-)$, the log store's partition values
    ([A-Za-z0-9._:-]), and the job's "job-fileset-<name>" fileset (63 chars).
    So the name keeps only [a-z0-9._-], drops the run ID's "+00:00" UTC offset,
    and is at most 51 characters.

    Args:
        name_tag: DAG kind, such as "single-turn".
        run_id: Airflow run ID, such as "manual__2026-10-22T09:04:10.551009+00:00".
        try_number: Task attempt; attempts after the first get a "-try<n>" suffix
            so a cleared task can resubmit without a name conflict.

    Returns:
        A job name like "single-turn-manual__2026-10-22t09-04-10.551009".
    """
    suffix = f"-try{try_number}" if try_number > 1 else ""
    base = f"{name_tag}-{run_id.removesuffix('+00:00')}".lower()
    base = re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9._-]", "-", base))
    return base[: JOB_NAME_MAX_LENGTH - len(suffix)].rstrip("-") + suffix


def airflow_run_link(dag_id: str, run_id: str) -> str:
    """Return the Airflow UI URL of a DAG run.

    Args:
        dag_id: Airflow DAG ID.
        run_id: Airflow run ID.

    Returns:
        The run's page in the Airflow UI.
    """
    return f"{AIRFLOW_UI_URL}/dags/{dag_id}/runs/{quote(run_id, safe='')}"


def run_job_fields(name_tag: str) -> dict:
    """Return the name, description and custom fields for a job this task submits.

    Ties a Helix platform job to the current Airflow run, so the job is named
    after the run on Studio's job pages and records the exact run ID.

    Args:
        name_tag: Job kind, such as "single-turn" or "anonymizer".

    Returns:
        The "name", "description" and "custom_fields" request fields.
    """
    from airflow.sdk import get_current_context

    context = get_current_context()
    dag_run, ti = context["dag_run"], context["ti"]
    return {
        "name": job_name_for_run(name_tag, dag_run.run_id, ti.try_number),
        "description": f"Airflow {dag_run.dag_id} run {dag_run.run_id}",
        "custom_fields": {
            "airflow_dag_id": dag_run.dag_id,
            "airflow_run_id": dag_run.run_id,
            "airflow_run_url": airflow_run_link(dag_run.dag_id, dag_run.run_id),
        },
    }
