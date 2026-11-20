import pytest

from openhexa.companion.datasets import guess_content_type


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("cases.parquet", "application/vnd.apache.parquet"),
        ("data/cases.csv", "text/csv"),
        ("cases.csv.gz", "application/gzip"),
        ("README", "application/octet-stream"),
    ],
)
def test_guess_content_type(path: str, expected: str):
    assert guess_content_type(path) == expected
