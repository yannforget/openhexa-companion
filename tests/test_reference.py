"""The generated references in docs/ match the CLI and the MCP tools."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "reference.py"


def load_script():
    spec = importlib.util.spec_from_file_location("reference", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reference = load_script()


@pytest.mark.parametrize("filename", reference.REFERENCES)
def test_reference_is_up_to_date(filename: str):
    current = (reference.DOCS / filename).read_text(encoding="utf-8")
    assert current == reference.REFERENCES[filename](), (
        f"docs/{filename} is out of date: run `uv run python scripts/reference.py`"
    )
