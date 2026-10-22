"""Build Airflow DAGs that run a NeMo Helix evaluation as an experiment.

Each DAG automates the manual walkthrough in docs/datasets-and-experiments.md:
check the datasets, create the experiment and an evaluation, submit the
evaluator job, wait for it, then report its logs and scores. Each step calls
the Helix REST API that the matching `nemo` CLI command uses. The single-turn
and multi-turn DAGs differ only in the defaults passed to build_eval_pipeline.

Every run is also sent to the Helix Traces page as one trace (see
helix_tracing.py). The evaluator job publishes its own trace per dataset row,
under the evaluation this run creates.
"""

import logging
import re
from pathlib import Path

import pendulum
import yaml
from airflow.sdk import Param, PokeReturnValue, dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from helix_api import (
    FILES_API,
    HELIX_URL,
    JOB_FAILED_STATES,
    WORKSPACE,
    airflow_run_link,
    helix_request,
    run_job_fields,
)
from helix_tracing import DAG_CALLBACKS, TASK_CALLBACKS

logger = logging.getLogger(__name__)

SPEC_DIR = Path("/opt/airflow/examples")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")

INTAKE_API = f"/apis/intake/v2/workspaces/{WORKSPACE}"
EVALUATOR_API = f"/apis/evaluator/v2/workspaces/{WORKSPACE}/evaluate/jobs"


def fileset_from_dataset_ref(ref: str) -> str:
    """Extract the fileset name from a spec dataset ref.

    Args:
        ref: Dataset ref like "default/sample-qa-dataset#validation/validation.jsonl".

    Returns:
        The fileset name, for example "sample-qa-dataset".
    """
    return ref.split("#", 1)[0].split("/")[-1]


def build_eval_pipeline(
    dag_id: str,
    doc_md: str,
    experiment_description: str,
    spec_file: str,
    filesets: list[str],
    name_tag: str,
    tags: list[str],
):
    """Build one eval DAG.

    Args:
        dag_id: Airflow DAG ID.
        doc_md: DAG documentation shown in the Airflow UI.
        experiment_description: Default experiment description.
        spec_file: Default evaluator spec in examples/.
        filesets: Default filesets that must exist.
        name_tag: Inserted into default evaluation names, <agent>-<tag>-<time>,
            so evaluations from different DAGs never collide.
        tags: Extra Airflow tags.

    Returns:
        The DAG.
    """

    def pipeline() -> None:
        eval_pipeline_tasks(name_tag)

    return dag(
        dag_id=dag_id,
        schedule=None,
        start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
        catchup=False,
        max_active_runs=1,
        tags=["nemo-helix", "evaluation", *tags],
        default_args={
            "retries": 2,
            "retry_delay": pendulum.duration(seconds=30),
            **TASK_CALLBACKS,
        },
        **DAG_CALLBACKS,
        params={
            "experiment_name": Param(
                dag_id,
                type="string",
                description="Experiment to create or reuse; defaults to the DAG ID.",
            ),
            "experiment_description": Param(experiment_description, type="string"),
            "evaluation_name": Param(
                "",
                type="string",
                description=f"Leave blank to name it <agent_name>-{name_tag}-<run time>.",
            ),
            "dataset_version": Param("v1", type="string"),
            "spec_file": Param(
                spec_file, type="string", description="Evaluator spec in examples/."
            ),
            "agent_name": Param("gemma4-e4b-mlx", type="string"),
            "filesets": Param(
                filesets,
                type="array",
                description="Filesets that must exist; their files are listed.",
            ),
        },
        doc_md=doc_md,
    )(pipeline)()


def eval_pipeline_tasks(name_tag: str) -> None:
    """Define the eval tasks inside the DAG being built.

    Args:
        name_tag: Inserted into default evaluation names.
    """

    @task
    def check_helix_ready() -> None:
        """Fail fast when the Helix API is down (curl /health/ready)."""
        status = helix_request("GET", "/health/ready")
        logger.info("NeMo Helix at %s: %s", HELIX_URL, status)

    @task
    def list_filesets(params: dict) -> list[str]:
        """List filesets and check the required ones exist (nemo files filesets list)."""
        body = helix_request("GET", f"{FILES_API}/filesets", params={"page_size": 100})
        available = [fileset["name"] for fileset in body["data"]]
        for fileset in body["data"]:
            logger.info(
                "fileset %s (created %s)", fileset["name"], fileset["created_at"]
            )
        missing = sorted(set(params["filesets"]) - set(available))
        if missing:
            raise AirflowFailException(f"Filesets not found in Helix: {missing}")
        return params["filesets"]

    @task
    def list_files(fileset: str) -> list[dict]:
        """List the files in one fileset (nemo files list <fileset>)."""
        files = helix_request("GET", f"{FILES_API}/filesets/{fileset}/files")["data"]
        for item in files:
            logger.info("%s: %s (%s bytes)", fileset, item["path"], item["size"])
        return [{"path": item["path"], "size": item["size"]} for item in files]

    @task
    def load_spec(params: dict) -> dict:
        """Read the evaluator spec from examples/."""
        spec = yaml.safe_load((SPEC_DIR / params["spec_file"]).read_text())
        logger.info(
            "Loaded %s: dataset %s, target %s",
            params["spec_file"],
            spec["dataset"],
            spec["target"]["name"],
        )
        return spec

    @task
    def create_experiment(params: dict) -> str:
        """Create the experiment if needed and return its ID (nemo experiments create --exist-ok)."""
        name = params["experiment_name"]
        helix_request(
            "POST",
            f"{INTAKE_API}/experiments",
            allow_conflict=True,
            json={"name": name, "description": params["experiment_description"]},
        )
        experiment = helix_request("GET", f"{INTAKE_API}/experiments/{name}")
        logger.info(
            "Experiment %s: id %s, %s evaluations",
            name,
            experiment["id"],
            experiment["evaluation_count"],
        )
        return experiment["id"]

    @task
    def create_evaluation(experiment_id: str, spec: dict, params: dict) -> str:
        """Create an evaluation in the experiment (POST /evaluations) and return its name."""
        dag_run = get_current_context()["dag_run"]
        name = params["evaluation_name"]
        if not name:
            run_after = dag_run.run_after
            name = f"{params['agent_name']}-{name_tag}-{run_after.strftime('%Y%m%d-%H%M%S')}"
        name = re.sub(r"[^a-z0-9-]", "-", name.lower())
        helix_request(
            "POST",
            f"{INTAKE_API}/evaluations",
            allow_conflict=True,
            json={
                "name": name,
                "experiment_ids": [experiment_id],
                "dataset_name": fileset_from_dataset_ref(spec["dataset"]),
                "dataset_version": params["dataset_version"],
                "description": f"Airflow {dag_run.dag_id} run {dag_run.run_id}",
                "source_link": airflow_run_link(dag_run.dag_id, dag_run.run_id),
                "metadata": {
                    "airflow_dag_id": dag_run.dag_id,
                    "airflow_run_id": dag_run.run_id,
                },
            },
        )
        evaluation = helix_request("GET", f"{INTAKE_API}/evaluations/{name}")
        if experiment_id not in evaluation["experiment_ids"]:
            raise AirflowFailException(
                f"Evaluation {name} exists but is not in experiment {experiment_id}"
            )
        logger.info(
            "Evaluation %s: id %s, run_count %s",
            name,
            evaluation["id"],
            evaluation["run_count"],
        )
        return name

    @task(retries=0)
    def submit_evaluator_job(evaluation_name: str, spec: dict, params: dict) -> str:
        """Submit the evaluator job and return its name (nemo evaluator evaluate).

        The job is named after the DAG run and records the run ID, so it can be
        found on the Model Evaluations page. Retries are off so a transient
        failure cannot submit the job twice.
        """
        job_spec = {
            **spec,
            "publication": {
                "intake": {
                    "evaluation_id": evaluation_name,
                    "agent_name": params["agent_name"],
                }
            },
        }
        job = helix_request(
            "POST",
            EVALUATOR_API,
            json={**run_job_fields(name_tag), "spec": job_spec},
        )
        logger.info(
            "Submitted job %s (%s), status %s", job["name"], job["id"], job["status"]
        )
        return job["name"]

    @task.sensor(poke_interval=10, timeout=1800, mode="reschedule")
    def wait_for_job(job_name: str) -> PokeReturnValue:
        """Poll the job until it completes (nemo jobs get-status <job>)."""
        status = helix_request("GET", f"{EVALUATOR_API}/{job_name}/status")
        state = status["status"]
        logger.info("Job %s: %s", job_name, state)
        if state in JOB_FAILED_STATES:
            raise AirflowFailException(
                f"Job {job_name} ended {state}: {status.get('error_details') or status.get('status_details')}"
            )
        if state != "completed":
            return PokeReturnValue(is_done=False)
        evaluation = status["status_details"].get("evaluation", {})
        logger.info("Job %s: %s", job_name, evaluation.get("message", "completed"))
        return PokeReturnValue(is_done=True, xcom_value=evaluation)

    @task
    def get_job_logs(job_name: str) -> int:
        """Copy the job's logs into this task's log (nemo jobs get-logs <job>)."""
        count = 0
        cursor = None
        while True:
            query = {"limit": 500, **({"page_cursor": cursor} if cursor else {})}
            page = helix_request(
                "GET", f"{EVALUATOR_API}/{job_name}/logs", params=query
            )
            for entry in page["data"]:
                logger.info(
                    "[%s] %s", entry["timestamp"], ANSI_ESCAPE.sub("", entry["message"])
                )
            count += len(page["data"])
            cursor = page.get("next_page")
            if not cursor or not page["data"]:
                return count

    @task
    def get_scores(evaluation_name: str, params: dict) -> dict:
        """Report the evaluation's mean scores (nemo experiments list, GET /evaluations/<name>)."""
        experiment = helix_request(
            "GET", f"{INTAKE_API}/experiments/{params['experiment_name']}"
        )
        logger.info(
            "Experiment %s: %s evaluations",
            experiment["name"],
            experiment["evaluation_count"],
        )
        evaluation = helix_request("GET", f"{INTAKE_API}/evaluations/{evaluation_name}")
        scores = evaluation.get("aggregate_scores") or {}
        if not scores:
            raise AirflowFailException(
                f"Evaluation {evaluation_name} has no scores yet"
            )
        means = {metric: values["mean"] for metric, values in scores.items()}
        for metric, mean in means.items():
            logger.info("%s %s: mean %.3f", evaluation_name, metric, mean)
        logger.info(
            "Rows scored: %s, models: %s",
            evaluation["run_count"],
            evaluation["model_names"],
        )
        return means

    ready = check_helix_ready()
    filesets = list_filesets()
    files = list_files.expand(fileset=filesets)
    spec = load_spec()
    experiment_id = create_experiment()
    evaluation_name = create_evaluation(experiment_id, spec)
    job_name = submit_evaluator_job(evaluation_name, spec)
    job_done = wait_for_job(job_name)

    ready >> filesets
    files >> [spec, experiment_id]
    job_done >> [get_job_logs(job_name), get_scores(evaluation_name)]
