import base64
import io
import zipfile
from pathlib import Path

import pytest

from openhexa.companion import InvalidInputError
from openhexa.companion.pipelines import package_pipeline


def test_package_pipeline_skips_local_files(tmp_path: Path):
    files = [
        "pipeline.py",
        "lib/helpers.py",
        ".env",
        ".venv/lib/site.py",
        "lib/__pycache__/helpers.pyc",
        "workspace/data.csv",
    ]
    for name in files:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("")
    (tmp_path / "pipeline.py").write_text('@pipeline(name="x", timeout=120)\ndef x():\n    pass\n')

    encoded, timeout = package_pipeline(tmp_path)

    with zipfile.ZipFile(io.BytesIO(base64.b64decode(encoded))) as archive:
        assert sorted(archive.namelist()) == ["lib/helpers.py", "pipeline.py"]
    assert timeout == 120


def test_package_pipeline_reports_syntax_errors(tmp_path: Path):
    (tmp_path / "pipeline.py").write_text("def broken(:\n")

    with pytest.raises(InvalidInputError, match="line 1"):
        package_pipeline(tmp_path)
