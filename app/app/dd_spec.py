"""Render a Data Designer Python config as the YAML job spec Airflow submits.

The Airflow image has no data_designer library, so DAGs submit a pre-rendered
spec. This builds the config the same way `nemo data-designer create` does.

Usage: python -m app.dd_spec /examples/customer_notes.py > examples/privacy-data-designer.yaml
"""

import runpy
import sys

import yaml

HEADER = (
    "# Generated from {source} by `make privacy-spec`. Do not edit by hand.\n"
    "# num_records is set by the DAG at submit time.\n"
)


def render_spec(config_path: str) -> str:
    """Build the config in a Data Designer Python file and render it as YAML.

    Args:
        config_path: Path to a file defining load_config_builder().

    Returns:
        YAML with a header comment and the built config under "config".
    """
    builder = runpy.run_path(config_path)["load_config_builder"]()
    config = builder.build().model_dump(mode="json", exclude_none=True)
    body = yaml.safe_dump({"config": config}, sort_keys=False, width=88)
    return HEADER.format(source=config_path.split("/")[-1]) + body


def main(argv: list[str] | None = None) -> int:
    """Entry point: print the spec for the config file given as the argument.

    Args:
        argv: Command-line arguments, defaulting to sys.argv[1:].

    Returns:
        Process exit code.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stderr.write(f"{__doc__.strip().splitlines()[-1]}\n")
        return 2
    sys.stdout.write(render_spec(args[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
