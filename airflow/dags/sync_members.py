"""Sync NeMo Studio workspace members from examples/members.yaml.

The file lists, per workspace, who should be a member and with which roles
(Viewer, Editor, Admin). Each run compares it with the workspace's current
members (Settings > Members in Studio), adds the missing ones, and fixes roles
that differ. Members missing from the file are left alone unless the run's
prune param is true. Runs are idempotent: a second run with no file changes
does nothing.

Every run also appears on the Helix Traces page (see helix_tracing.py).
"""

import logging
from pathlib import Path
from urllib.parse import quote

import pendulum
import yaml
from airflow.sdk import Param, dag, task
from airflow.sdk.exceptions import AirflowFailException
from helix_api import helix_request
from helix_tracing import DAG_CALLBACKS, TASK_CALLBACKS

logger = logging.getLogger(__name__)

MEMBERS_FILE = Path("/opt/airflow/examples/members.yaml")
VALID_ROLES = {"Viewer", "Editor", "Admin"}


def members_api(workspace: str) -> str:
    """Return the members collection path for a workspace.

    Args:
        workspace: Workspace name.

    Returns:
        The API path.
    """
    return f"/apis/entities/v2/workspaces/{workspace}/members"


def plan_changes(
    desired: dict[str, list[str]], current: dict[str, list[str]], prune: bool
) -> dict:
    """Work out which members to add, update, and remove.

    Args:
        desired: Principal to roles, from members.yaml.
        current: Principal to roles, from the workspace.
        prune: Remove current members that members.yaml does not list.

    Returns:
        "add" and "update" map principals to roles; "remove" lists principals.
    """
    return {
        "add": {p: roles for p, roles in desired.items() if p not in current},
        "update": {
            p: roles
            for p, roles in desired.items()
            if p in current and sorted(current[p]) != sorted(roles)
        },
        "remove": sorted(set(current) - set(desired)) if prune else [],
    }


@dag(
    dag_id="sync-members",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    tags=["nemo-helix", "members"],
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
            description="Remove members that examples/members.yaml does not list.",
        ),
    },
    doc_md=__doc__,
)
def sync_members():
    @task
    def load_members() -> dict[str, dict[str, list[str]]]:
        """Read and validate the desired members per workspace."""
        workspaces = yaml.safe_load(MEMBERS_FILE.read_text())["workspaces"]
        desired = {}
        for workspace, members in workspaces.items():
            desired[workspace] = {}
            for member in members:
                roles = member.get("roles") or ["Editor"]
                unknown = set(roles) - VALID_ROLES
                if unknown:
                    raise AirflowFailException(
                        f"{member['principal']} in {workspace}: unknown roles {sorted(unknown)}"
                    )
                desired[workspace][member["principal"]] = sorted(roles)
            logger.info("%s: %d members wanted", workspace, len(desired[workspace]))
        return desired

    @task
    def sync_workspace(desired: dict[str, dict[str, list[str]]], params: dict) -> dict:
        """Apply members.yaml to every workspace and return what changed."""
        summary = {}
        for workspace, wanted in desired.items():
            api = members_api(workspace)
            current = {
                m["principal"]: m.get("roles", [])
                for m in helix_request("GET", api)["data"]
            }
            changes = plan_changes(wanted, current, params["prune"])
            for principal, roles in changes["add"].items():
                helix_request(
                    "POST", api, json={"principal": principal, "roles": roles}
                )
                logger.info("%s: added %s as %s", workspace, principal, roles)
            for principal, roles in changes["update"].items():
                helix_request(
                    "PUT", f"{api}/{quote(principal, safe='')}", json={"roles": roles}
                )
                logger.info(
                    "%s: %s roles %s -> %s",
                    workspace,
                    principal,
                    current[principal],
                    roles,
                )
            for principal in changes["remove"]:
                helix_request("DELETE", f"{api}/{quote(principal, safe='')}")
                logger.info("%s: removed %s", workspace, principal)
            unlisted = sorted(set(current) - set(wanted)) if not params["prune"] else []
            if unlisted:
                logger.info("%s: not in members.yaml, kept: %s", workspace, unlisted)
            summary[workspace] = {
                "added": sorted(changes["add"]),
                "updated": sorted(changes["update"]),
                "removed": changes["remove"],
            }
        return summary

    @task
    def verify(desired: dict[str, dict[str, list[str]]], summary: dict) -> None:
        """Check every workspace now has the members and roles members.yaml lists."""
        for workspace, wanted in desired.items():
            current = {
                m["principal"]: sorted(m.get("roles", []))
                for m in helix_request("GET", members_api(workspace))["data"]
            }
            wrong = {
                p: current.get(p)
                for p, roles in wanted.items()
                if current.get(p) != roles
            }
            if wrong:
                raise AirflowFailException(
                    f"{workspace}: members still out of sync: {wrong}"
                )
            logger.info(
                "%s in sync: %d members (%s)",
                workspace,
                len(current),
                summary[workspace],
            )

    desired = load_members()
    verify(desired, sync_workspace(desired))


sync_members()
