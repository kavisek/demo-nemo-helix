"""Sync NeMo Studio workspace secrets from examples/secrets.yaml.

The file lists, per workspace, which secrets should exist and the Airflow
Variable that holds each value, so no secret value is ever committed. Each run
creates missing secrets, updates ones whose value or description changed, and
skips any whose Variable is unset. Secrets missing from the file are left alone
unless the run's prune param is true.

Values are read and compared inside one task and never returned, so they stay
out of XCom; Airflow masks Variables whose key contains "secret" in task logs.

Every run also appears on the Helix Traces page (see helix_tracing.py).
"""

import logging
from pathlib import Path

import pendulum
import requests
import yaml
from airflow.sdk import Param, Variable, dag, task
from airflow.sdk.exceptions import AirflowFailException
from helix_api import helix_request
from helix_tracing import DAG_CALLBACKS, TASK_CALLBACKS

logger = logging.getLogger(__name__)

SECRETS_FILE = Path("/opt/airflow/examples/secrets.yaml")


def secrets_api(workspace: str) -> str:
    """Return the secrets collection path for a workspace.

    Args:
        workspace: Workspace name.

    Returns:
        The API path.
    """
    return f"/apis/secrets/v2/workspaces/{workspace}/secrets"


def current_value(api: str, name: str) -> str | None:
    """Read a secret's stored value from Helix.

    Args:
        api: The workspace's secrets collection path.
        name: Secret name.

    Returns:
        The value, or None if it can't be read.
    """
    try:
        return helix_request("GET", f"{api}/{name}/access").get("value")
    except requests.HTTPError:
        return None


@dag(
    dag_id="sync-secrets",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["nemo-helix", "secrets"],
    default_args={
        "retries": 2,
        "retry_delay": pendulum.duration(seconds=30),
        **TASK_CALLBACKS,
    },
    **DAG_CALLBACKS,
    params={
        "prune": Param(
            False,
            type="boolean",
            description="Delete secrets that examples/secrets.yaml does not list.",
        ),
        "require_all": Param(
            False,
            type="boolean",
            description="Fail the run when a listed secret's Airflow Variable is unset.",
        ),
    },
    doc_md=__doc__,
)
def sync_secrets():
    @task
    def load_secrets() -> dict[str, list[dict]]:
        """Read the desired secrets per workspace (names and Variable keys, no values)."""
        workspaces = yaml.safe_load(SECRETS_FILE.read_text())["workspaces"]
        for workspace, secrets in workspaces.items():
            logger.info(
                "%s: %d secrets wanted: %s",
                workspace,
                len(secrets),
                [s["name"] for s in secrets],
            )
        return workspaces

    @task
    def sync_workspace(wanted: dict[str, list[dict]], params: dict) -> dict:
        """Create, update, and optionally prune secrets; return names only."""
        summary = {}
        for workspace, secrets in wanted.items():
            api = secrets_api(workspace)
            existing = {
                s["name"]: s
                for s in helix_request("GET", api, params={"page_size": 1000})["data"]
            }
            result = {"created": [], "updated": [], "unchanged": [], "missing": []}
            for secret in secrets:
                name, description = secret["name"], secret.get("description")
                value = Variable.get(secret["variable"], default=None)
                if not value:
                    result["missing"].append(name)
                    logger.warning(
                        "%s: %s skipped, Airflow Variable %s is unset",
                        workspace,
                        name,
                        secret["variable"],
                    )
                    continue
                if name not in existing:
                    helix_request(
                        "POST",
                        api,
                        json={"name": name, "description": description, "value": value},
                    )
                    result["created"].append(name)
                elif (
                    current_value(api, name) != value
                    or existing[name].get("description") != description
                ):
                    helix_request(
                        "PATCH",
                        f"{api}/{name}",
                        json={"description": description, "value": value},
                    )
                    result["updated"].append(name)
                else:
                    result["unchanged"].append(name)
            listed = {s["name"] for s in secrets}
            unlisted = sorted(set(existing) - listed)
            if params["prune"]:
                for name in unlisted:
                    helix_request("DELETE", f"{api}/{name}")
                result["deleted"] = unlisted
            elif unlisted:
                logger.info("%s: not in secrets.yaml, kept: %s", workspace, unlisted)
            logger.info("%s: %s", workspace, result)
            summary[workspace] = result
        return summary

    @task
    def check_missing(summary: dict, params: dict) -> None:
        """Report secrets whose Variable is unset, failing when require_all is on."""
        missing = {ws: r["missing"] for ws, r in summary.items() if r["missing"]}
        if not missing:
            logger.info("Every listed secret is in sync")
            return
        message = (
            f"Secrets skipped because their Airflow Variable is unset: {missing}. "
            "Set them with `make airflow-secret VAR=<variable>`."
        )
        if params["require_all"]:
            raise AirflowFailException(message)
        logger.warning(message)

    check_missing(sync_workspace(load_secrets()))


sync_secrets()
