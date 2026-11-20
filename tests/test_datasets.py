import uuid
from pathlib import Path

import pytest

from openhexa.companion import Client, NotFoundError, _transport

pytestmark = pytest.mark.workspace

# Dataset of the demo workspace with a single 505 MB Parquet file.
LARGE_DATASET = "large-parquet-range-request-demo-505-mb"
# Dataset shared with the demo workspace, with more versions than fit in a small page.
SHARED_DATASET = "bikes-in-brussels-6f971d"


def test_workspace_homepage(client: Client):
    original = client.get_workspace().description
    try:
        updated = client.update_homepage(original + "\n\nEdited by the companion tests.")
        assert updated.description.endswith("Edited by the companion tests.")
    finally:
        client.update_homepage(original)
    assert client.get_workspace().description == original


def test_read_byte_ranges_of_large_file(client: Client):
    assert LARGE_DATASET in {dataset.slug for dataset in client.datasets.list()}
    version = client.datasets.version(LARGE_DATASET)
    [file] = client.datasets.files(version)
    assert file.size > 500_000_000

    with client.datasets.read(file, length=4) as stream:
        assert stream.read() == b"PAR1"
    with client.datasets.read(file, offset=file.size - 4) as stream:
        assert stream.read() == b"PAR1"


def test_versions_span_several_pages(client: Client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(_transport, "PAGE_SIZE", 10)
    dataset = client.datasets.get(SHARED_DATASET)
    assert dataset.workspace != client.workspace

    versions = client.datasets.versions(SHARED_DATASET)
    assert len(versions) > 10
    assert len({version.id for version in versions}) == len(versions)
    assert versions[0].name == dataset.latest_version


def test_find_shared_dataset_and_version_again(client: Client):
    latest = client.datasets.version(SHARED_DATASET)
    dataset = client.datasets.get(SHARED_DATASET)
    assert client.datasets.get(dataset.reference) == dataset

    found = client.datasets.version(dataset.reference, version_id=latest.id)
    assert found == latest
    with pytest.raises(NotFoundError):
        client.datasets.get(f"{client.workspace}/{SHARED_DATASET}")


def test_unknown_version_lists_valid_names(client: Client):
    with pytest.raises(NotFoundError, match="'v3'"):
        client.datasets.version(SHARED_DATASET, "no-such-version")


def test_publish_dataset(client: Client, tmp_path: Path):
    dataset = client.datasets.create(f"companion test {uuid.uuid4().hex[:8]}", "Temporary")
    try:
        version = client.datasets.create_version(dataset.slug, "v1", changelog="First")
        local = tmp_path / "cases.csv"
        local.write_text("district,cases\nA,1\n")
        client.datasets.upload(version, local)
        client.datasets.upload(version, b'{"a": 1}', path="meta/info.json")

        assert client.datasets.version(dataset.slug).name == "v1"
        files = {file.path: file for file in client.datasets.files(version)}
        assert set(files) == {"cases.csv", "meta/info.json"}
        assert files["cases.csv"].content_type == "text/csv"
        with client.datasets.read(client.datasets.file(version, "cases.csv")) as stream:
            assert stream.read() == local.read_bytes()

        client.datasets.set_attribute(dataset.slug, "source", "DHIS2")
        updated = client.datasets.set_attribute(dataset.slug, "countries", ["COD", "SEN"])
        assert updated.attributes == {"source": "DHIS2", "countries": ["COD", "SEN"]}
        assert client.datasets.delete_attribute(dataset.slug, "source").attributes == {
            "countries": ["COD", "SEN"]
        }

        renamed = client.datasets.update_version(version, name="v1-fixed")
        assert client.datasets.update(dataset.slug, description="Cases").description == "Cases"
        second = client.datasets.create_version(dataset.slug, "v2")
        client.datasets.delete_version(second)
        assert client.datasets.version(dataset.slug) == renamed
    finally:
        client.datasets.delete(dataset.slug)

    with pytest.raises(NotFoundError):
        client.datasets.get(dataset.slug)
