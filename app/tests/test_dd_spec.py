"""Tests for rendering Data Designer specs."""

from pathlib import Path

import yaml

from app import dd_spec

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "customer_notes.py"


def test_render_spec_builds_customer_notes_config():
    """The example config renders to a spec with its columns and model."""
    text = dd_spec.render_spec(str(EXAMPLE))
    assert text.startswith("# Generated from customer_notes.py")
    spec = yaml.safe_load(text)
    names = [column["name"] for column in spec["config"]["columns"]]
    assert names[:2] == ["id", "customer"]
    assert "note" in names
    assert spec["config"]["model_configs"][0]["provider"] == "default/ollama"
    assert "num_records" not in spec


def test_main_requires_one_argument(capsys):
    """main prints usage and exits 2 without exactly one argument."""
    assert dd_spec.main([]) == 2
    assert "Usage" in capsys.readouterr().err


def test_main_prints_spec(capsys):
    """main writes the rendered spec to stdout."""
    assert dd_spec.main([str(EXAMPLE)]) == 0
    assert "column_type: llm-text" in capsys.readouterr().out
