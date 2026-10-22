"""Render every dashboard in ./dashboards to JSON for Grafana to provision.

Each module in ./dashboards that defines a top-level `dashboard` is written to
grafana/dashboards/<module>.json. Pass --check to fail instead of writing when
the committed JSON is out of date.
"""

import argparse
import importlib
import json
import pkgutil
import sys
from pathlib import Path

from grafanalib._gen import DashboardEncoder

import dashboards

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "dashboards"


def render(dashboard) -> str:
    return json.dumps(dashboard.to_json_data(), cls=DashboardEncoder, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if generated JSON is out of date")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    stale = []
    for module_info in pkgutil.iter_modules(dashboards.__path__):
        module = importlib.import_module(f"dashboards.{module_info.name}")
        if not hasattr(module, "dashboard"):
            continue

        path = OUTPUT_DIR / f"{module_info.name}.json"
        rendered = render(module.dashboard)
        current = path.read_text() if path.exists() else None

        if rendered == current:
            print(f"up to date: {path.name}")
        elif args.check:
            stale.append(path.name)
            print(f"out of date: {path.name}")
        else:
            path.write_text(rendered)
            print(f"wrote: {path.name}")

    if stale:
        print("Run `make dashboards` to regenerate.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
