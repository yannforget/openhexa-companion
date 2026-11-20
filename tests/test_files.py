import uuid

import pytest

from openhexa.companion import Client, NotFoundError

pytestmark = pytest.mark.workspace


def test_write_read_and_delete_files(client: Client):
    directory = f"companion-test-{uuid.uuid4().hex[:8]}"
    client.files.upload(b"district,cases\nA,1\n", f"{directory}/sub/cases.csv")
    try:
        assert [item.path for item in client.files.list(directory)] == [f"{directory}/sub/"]
        [file] = client.files.list(directory, recursive=True)
        assert file.path == f"{directory}/sub/cases.csv"
        assert file.size == 19

        with client.files.read(file.path, offset=9, length=5) as stream:
            assert stream.read() == b"cases"
    finally:
        client.files.delete(directory)

    with pytest.raises(NotFoundError):
        client.files.get(directory)


def test_connections_and_database(client: Client):
    for connection in client.connections.list():
        assert client.connections.get(connection.slug) == connection
        assert all(value is None for value in _secret_values(connection))

    credentials = client.database.credentials(read_only=True)
    assert credentials.url.startswith("postgresql://")
    assert credentials.password not in repr(credentials)


def _secret_values(connection):
    secret_codes = {field.code for field in connection.fields if field.secret}
    return [connection.values()[code] for code in secret_codes]
