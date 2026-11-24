import time
import uuid
from pathlib import Path

import pytest

from openhexa.companion import APIError, Client, InvalidInputError, NotFoundError

pytestmark = pytest.mark.workspace

# Pipeline of the demo workspace with several versions and finished runs.
DEMO_PIPELINE = "pi-demo-monthly-quality"
# Seconds to wait for a run of the test pipeline to finish.
RUN_TIMEOUT = 300

PIPELINE_CODE = """
from openhexa.sdk import current_run, parameter, pipeline


@pipeline(name="companion test", timeout=600)
@parameter("greeting", type=str, default="hello")
def companion_test(greeting):
    current_run.log_info(f"{greeting} from {VERSION}")


VERSION = "{version}"

if __name__ == "__main__":
    companion_test()
"""


def test_deploy_and_run(client: Client, tmp_path: Path):
    source = tmp_path / "pipeline"
    source.mkdir()
    (source / "pipeline.py").write_text(PIPELINE_CODE.replace("{version}", "first"))
    (source / ".env").write_text("SECRET=1\n")

    first = client.pipelines.create(f"companion test {uuid.uuid4().hex[:8]}", source)
    try:
        assert first.timeout == 600
        assert [parameter.code for parameter in first.parameters] == ["greeting"]
        assert [file.path for file in client.pipelines.files(first)] == ["pipeline.py"]

        (source / "pipeline.py").write_text(PIPELINE_CODE.replace("{version}", "second"))
        second = client.pipelines.deploy(first.pipeline, source, version_name="second")
        assert second.label == "second [v2]"
        assert client.pipelines.version(first.pipeline).id == second.id

        run = client.runs.start(second, {"greeting": "hi"})
        deadline = time.monotonic() + RUN_TIMEOUT
        while not run.finished and time.monotonic() < deadline:
            time.sleep(5)
            run = client.runs.get(run.id)
        assert run.status == "success", client.runs.details(run.id).logs

        messages = [message.message for message in client.runs.details(run.id).messages]
        assert "hi from second" in messages
        succeeded = client.pipelines.list(last_run_status="success")
        assert first.pipeline in {pipeline.code for pipeline in succeeded}

        client.pipelines.delete_version(second)
        assert client.pipelines.version(first.pipeline).id == first.id
    finally:
        client.pipelines.delete(first.pipeline)

    with pytest.raises(NotFoundError):
        client.pipelines.get(first.pipeline)


def test_unknown_parameter_is_refused(client: Client):
    version = client.pipelines.version(DEMO_PIPELINE)

    with pytest.raises(ValueError, match="'mode'"):
        client.runs.start(version, {"no_such_parameter": 1})


SCHEDULED_CODE = """
from openhexa.sdk import parameter, pipeline


@pipeline(name="companion schedule test")
@parameter("country", type=str, required=True)
@parameter("year", type=int, default=2025)
def companion_schedule_test(country, year):
    pass
"""


def test_update_pipeline_and_its_defaults(client: Client, tmp_path: Path):
    (tmp_path / "pipeline.py").write_text(SCHEDULED_CODE)
    name = f"companion test {uuid.uuid4().hex[:8]}"
    version = client.pipelines.create(name, tmp_path)
    code = version.pipeline
    try:
        with pytest.raises(APIError, match="country") as refused:
            client.pipelines.update(code, schedule="0 6 * * 1")
        assert refused.value.codes == ("MISSING_VERSION_CONFIG",)

        with pytest.raises(InvalidInputError, match="no parameter 'region'"):
            client.pipelines.set_defaults(version, {"region": "north"})
        version = client.pipelines.set_defaults(version, {"country": "BE"})
        version = client.pipelines.set_defaults(version, {"year": 2026})
        assert version.config == {"country": "BE", "year": 2026}

        updated = client.pipelines.update(code, description="Weekly", schedule="0 6 * * 1")
        assert (updated.description, updated.schedule) == ("Weekly", "0 6 * * 1")
        assert client.pipelines.update(code, unschedule=True).schedule is None
    finally:
        client.pipelines.delete(code)
