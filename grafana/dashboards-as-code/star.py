"""Star every dashboard in ./dashboards for the Grafana admin user.

Stars are per-user and live in Grafana's database rather than in dashboard
JSON, so they're applied through the HTTP API after Grafana has provisioned
the dashboards. Connection details come from GRAFANA_URL, GRAFANA_USER and
GRAFANA_PASSWORD.
"""

import base64
import importlib
import json
import os
import pkgutil
import sys
import time
import urllib.error
import urllib.request

import dashboards

GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:7003")
GRAFANA_USER = os.environ.get("GRAFANA_USER", "admin")
GRAFANA_PASSWORD = os.environ.get("GRAFANA_PASSWORD", "admin")
TIMEOUT_SECONDS = 60


def request(method: str, path: str):
    token = base64.b64encode(f"{GRAFANA_USER}:{GRAFANA_PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        f"{GRAFANA_URL}{path}",
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)


def dashboard_uids() -> list[str]:
    uids = []
    for module_info in pkgutil.iter_modules(dashboards.__path__):
        module = importlib.import_module(f"dashboards.{module_info.name}")
        if hasattr(module, "dashboard"):
            uids.append(module.dashboard.uid)
    return uids


def wait_for_dashboard(uid: str) -> None:
    """Block until Grafana has provisioned the dashboard."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        try:
            request("GET", f"/api/dashboards/uid/{uid}")
            return
        except urllib.error.HTTPError as err:
            if err.code == 401:
                raise
            if time.monotonic() > deadline:
                raise
        except urllib.error.URLError:
            if time.monotonic() > deadline:
                raise
        time.sleep(2)


def main() -> int:
    try:
        starred = {d["uid"] for d in request_with_retry("GET", "/api/search?starred=true")}
        for uid in dashboard_uids():
            if uid in starred:
                print(f"already starred: {uid}")
                continue
            wait_for_dashboard(uid)
            # Grafana 12+ stars by uid; the old numeric-id endpoint returns 404.
            request("POST", f"/api/user/stars/dashboard/uid/{uid}")
            print(f"starred: {uid}")
    except urllib.error.HTTPError as err:
        if err.code == 401:
            print(
                f"Could not star dashboards: Grafana rejected the login for '{GRAFANA_USER}'. "
                "Set GRAFANA_PASSWORD if the admin password was changed.",
                file=sys.stderr,
            )
        else:
            print(f"Could not star dashboards: {err}", file=sys.stderr)
        return 1
    except urllib.error.URLError as err:
        print(f"Could not star dashboards: Grafana unreachable at {GRAFANA_URL} ({err.reason})", file=sys.stderr)
        return 1
    return 0


def request_with_retry(method: str, path: str):
    """Retry while Grafana is still starting up."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        try:
            return request(method, path)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, ConnectionError):
            if time.monotonic() > deadline:
                raise
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
