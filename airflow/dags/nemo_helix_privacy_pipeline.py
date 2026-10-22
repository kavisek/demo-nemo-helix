"""Generate PII-laden data, redact it, and optionally synthesize a safe copy.

Chains three NeMo Helix services:

1. Data Designer generates customer support notes that mention a fake
   customer's name, phone number, and city (examples/customer_notes.py).
2. The Anonymizer finds the PII in each note with the local Ollama model and
   redacts it. The DAG joins the redacted notes back onto the generated rows
   and stores the table in the privacy-pipeline fileset.
3. Safe Synthesizer trains on the redacted table and generates new records.
   It needs a CUDA GPU, so it only runs when run_safe_synthesizer is true.

Every run also appears on the Helix Traces page (see helix_tracing.py).
"""

import io
import logging
from pathlib import Path

import pandas as pd
import pendulum
import yaml
from airflow.sdk import Param, PokeReturnValue, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from helix_api import (
    JOB_FAILED_STATES,
    WORKSPACE,
    download,
    download_file,
    ensure_fileset,
    helix_request,
    list_fileset_files,
    run_job_fields,
    split_fileset_ref,
    upload_file,
)
from helix_tracing import DAG_CALLBACKS, TASK_CALLBACKS

logger = logging.getLogger(__name__)

SPEC_DIR = Path("/opt/airflow/examples")
OUTPUT_FILESET = "privacy-pipeline"

DATA_DESIGNER_API = f"/apis/data-designer/v2/workspaces/{WORKSPACE}/jobs/create"
ANONYMIZER_API = f"/apis/anonymizer/v2/workspaces/{WORKSPACE}/jobs/run"
SAFE_SYNTHESIZER_API = f"/apis/safe-synthesizer/v2/workspaces/{WORKSPACE}/jobs"


def load_spec(name: str) -> dict:
    """Read a job spec from examples/.

    Args:
        name: File name, such as "privacy-anonymizer.yaml".

    Returns:
        The parsed spec.
    """
    return yaml.safe_load((SPEC_DIR / name).read_text())


def run_folder() -> str:
    """Return the folder in OUTPUT_FILESET for this DAG run's files.

    Returns:
        A path-safe folder name derived from the run ID.
    """
    run_id = get_current_context()["dag_run"].run_id
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in run_id)


def submit_job(api: str, spec: dict, label: str, name_tag: str) -> str:
    """Submit a Helix platform job named after the current DAG run.

    Args:
        api: The service's jobs collection path.
        spec: Job spec for the "spec" field.
        label: Service name for the log line.
        name_tag: Job name prefix, such as "anonymizer".

    Returns:
        The job name.
    """
    job = helix_request("POST", api, json={**run_job_fields(name_tag), "spec": spec})
    logger.info("Submitted %s job %s, status %s", label, job["name"], job["status"])
    return job["name"]


def artifacts_ref(api: str, job_name: str) -> str:
    """Return the fileset ref of a completed job's "artifacts" result.

    Args:
        api: The service's jobs collection path.
        job_name: Job name.

    Returns:
        A ref like "default/job-fileset-<job>#results/<attempt>/artifacts".

    Raises:
        AirflowFailException: The job has no artifacts result.
    """
    results = helix_request("GET", f"{api}/{job_name}/results")["data"]
    for result in results:
        if result["name"] == "artifacts":
            return result["artifact_url"]
    raise AirflowFailException(f"Job {job_name} has no artifacts result")


def read_parquet_files(fileset: str, paths: list[str]) -> pd.DataFrame:
    """Download Parquet files from a fileset and concatenate them.

    Args:
        fileset: Fileset name.
        paths: File paths inside the fileset.

    Returns:
        All rows, in file order.
    """
    frames = [pd.read_parquet(io.BytesIO(download_file(fileset, p))) for p in paths]
    return pd.concat(frames, ignore_index=True)


@task.sensor(poke_interval=15, timeout=3600, mode="reschedule")
def wait_for_job(api: str, job_name: str) -> PokeReturnValue:
    """Poll a Helix platform job until it completes (nemo jobs get-status <job>)."""
    status = helix_request("GET", f"{api}/{job_name}/status")
    state = status["status"]
    logger.info("Job %s: %s", job_name, state)
    if state in JOB_FAILED_STATES:
        raise AirflowFailException(
            f"Job {job_name} ended {state}: {status.get('error_details') or status.get('status_details')}"
        )
    return PokeReturnValue(is_done=state == "completed", xcom_value=job_name)


@dag(
    dag_id="nemo_helix_privacy_pipeline",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["nemo-helix", "data-designer", "anonymizer", "safe-synthesizer"],
    default_args={
        "retries": 2,
        "retry_delay": pendulum.duration(seconds=30),
        **TASK_CALLBACKS,
    },
    **DAG_CALLBACKS,
    params={
        "num_records": Param(
            20,
            type="integer",
            minimum=1,
            maximum=500,
            description="Notes to generate. The Anonymizer takes about 15s per note on Ollama.",
        ),
        "run_safe_synthesizer": Param(
            False,
            type="boolean",
            description="Train Safe Synthesizer on the redacted table. Needs a CUDA GPU.",
        ),
        "synthetic_records": Param(
            100,
            type="integer",
            minimum=1,
            description="Records Safe Synthesizer generates.",
        ),
    },
    doc_md=__doc__,
)
def nemo_helix_privacy_pipeline():
    @task
    def check_helix_ready() -> None:
        """Fail fast when the Helix API is down (curl /health/ready)."""
        logger.info("NeMo Helix: %s", helix_request("GET", "/health/ready"))

    @task(retries=0)
    def submit_data_designer_job(params: dict) -> str:
        """Generate the notes (nemo data-designer create examples/customer_notes.py).

        Retries are off so a transient failure cannot submit the job twice.
        """
        spec = {
            **load_spec("privacy-data-designer.yaml"),
            "num_records": params["num_records"],
        }
        return submit_job(DATA_DESIGNER_API, spec, "Data Designer", "data-designer")

    @task
    def store_generated_notes(job_name: str) -> str:
        """Merge the generated Parquet batches into one file for the Anonymizer."""
        fileset, artifacts = split_fileset_ref(
            artifacts_ref(DATA_DESIGNER_API, job_name)
        )
        batches = list_fileset_files(fileset, f"{artifacts}/dataset/parquet-files/")
        if not batches:
            raise AirflowFailException(f"Job {job_name} produced no dataset files")
        notes = read_parquet_files(fileset, batches)
        logger.info(
            "Generated %d notes in %d batch(es), columns %s",
            len(notes),
            len(batches),
            list(notes.columns),
        )
        for note in notes["note"].head(3):
            logger.info("Sample note: %s", note)
        ensure_fileset(OUTPUT_FILESET, "nemo_helix_privacy_pipeline outputs, by run")
        return upload_file(
            OUTPUT_FILESET,
            f"{run_folder()}/generated.parquet",
            notes.to_parquet(index=False),
        )

    @task(retries=0)
    def submit_anonymizer_job(generated_ref: str) -> str:
        """Redact PII in the notes (nemo anonymizer run).

        Retries are off so a transient failure cannot submit the job twice.
        """
        spec = load_spec("privacy-anonymizer.yaml")
        spec["data"] = {**spec["data"], "source": generated_ref}
        return submit_job(ANONYMIZER_API, spec, "Anonymizer", "anonymizer")

    @task
    def store_redacted_table(generated_ref: str, job_name: str) -> str:
        """Join the redacted notes onto the generated rows and store them as CSV."""
        notes_fileset, notes_path = split_fileset_ref(generated_ref)
        notes = read_parquet_files(notes_fileset, [notes_path])
        fileset, artifacts = split_fileset_ref(artifacts_ref(ANONYMIZER_API, job_name))
        redacted = read_parquet_files(fileset, [f"{artifacts}/dataset.parquet"])
        redacted = redacted.drop_duplicates("note")
        table = notes.merge(redacted[["note", "note_replaced"]], on="note", how="inner")
        missing = len(notes) - len(table)
        if missing:
            logger.warning(
                "%d note(s) have no redacted version and are dropped", missing
            )
        if table.empty:
            raise AirflowFailException(f"Anonymizer job {job_name} redacted no notes")
        labels = pd.Series(
            [
                entity["label"]
                for entities in redacted["final_entities"]
                for entity in entities["entities"]
            ]
        ).value_counts()
        logger.info("Redacted entities: %s", labels.to_dict())
        for note in table["note_replaced"].head(3):
            logger.info("Sample redacted note: %s", note)
        table = table.drop(columns="note").rename(columns={"note_replaced": "note"})
        return upload_file(
            OUTPUT_FILESET,
            f"{run_folder()}/customer_notes_redacted.csv",
            table.to_csv(index=False).encode(),
        )

    @task.short_circuit
    def safe_synthesizer_enabled(params: dict) -> bool:
        """Skip Safe Synthesizer unless run_safe_synthesizer is true."""
        if not params["run_safe_synthesizer"]:
            logger.info("run_safe_synthesizer is false, skipping Safe Synthesizer")
        return params["run_safe_synthesizer"]

    @task(retries=0)
    def submit_safe_synthesizer_job(redacted_ref: str, params: dict) -> str:
        """Train and generate synthetic records (nemo safe-synthesizer generate).

        Retries are off so a transient failure cannot submit the job twice.
        """
        spec = load_spec("privacy-safe-synthesizer.yaml")
        spec["data_source"] = redacted_ref
        spec["config"]["generation"]["num_records"] = params["synthetic_records"]
        return submit_job(SAFE_SYNTHESIZER_API, spec, "Safe Synthesizer", "safe-synth")

    @task
    def store_synthetic_data(job_name: str) -> str:
        """Copy the synthetic records into the run's folder and log a sample."""
        content = download(
            f"{SAFE_SYNTHESIZER_API}/{job_name}/results/synthetic-data/download"
        )
        sample = pd.read_csv(io.BytesIO(content))
        logger.info("Synthetic records: %d, sample:\n%s", len(sample), sample.head(3))
        return upload_file(OUTPUT_FILESET, f"{run_folder()}/synthetic.csv", content)

    ready = check_helix_ready()
    dd_submit = submit_data_designer_job()
    dd_job = wait_for_job.override(task_id="wait_for_data_designer")(
        DATA_DESIGNER_API, dd_submit
    )
    generated = store_generated_notes(dd_job)
    anon_job = wait_for_job.override(task_id="wait_for_anonymizer")(
        ANONYMIZER_API, submit_anonymizer_job(generated)
    )
    redacted = store_redacted_table(generated, anon_job)
    nss_submit = submit_safe_synthesizer_job(redacted)
    nss_job = wait_for_job.override(task_id="wait_for_safe_synthesizer")(
        SAFE_SYNTHESIZER_API, nss_submit
    )

    ready >> dd_submit
    safe_synthesizer_enabled() >> nss_submit
    store_synthetic_data(nss_job)


nemo_helix_privacy_pipeline()
