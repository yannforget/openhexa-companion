"""Error mapping for responses the demo workspace cannot produce on demand."""

import httpx2
import pytest

from openhexa.companion import APIError, Client, PermissionDeniedError


def _client(response: httpx2.Response) -> Client:
    transport = httpx2.MockTransport(lambda _request: response)
    return Client("https://openhexa.test", "ws", "token", http_transport=transport)


def test_mutation_error_codes_are_kept():
    result = {"success": False, "errors": ["PERMISSION_DENIED"], "dataset": None}
    client = _client(httpx2.Response(200, json={"data": {"createDataset": result}}))

    with pytest.raises(PermissionDeniedError, match="may not create datasets") as error:
        client.datasets.create("Cases")

    assert error.value.codes == ("PERMISSION_DENIED",)


def test_server_error_names_the_host():
    client = _client(httpx2.Response(502, text="Bad Gateway"))

    with pytest.raises(APIError, match=r"openhexa\.test returned HTTP 502"):
        client.get_workspace()
