import uuid
from pathlib import Path

import pytest

from openhexa.companion import Client, NotFoundError
from openhexa.companion.webapps import package_site

# Not valid UTF-8, so sent and stored in base64.
BINARY = b"\x89PNG\r\n\x1a\n\x00\xff"


def test_package_site_skips_local_files(tmp_path: Path):
    for name in ["index.html", "assets/logo.png", ".env", ".git/HEAD", "node_modules/x/a.js"]:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x")
    (tmp_path / "assets/logo.png").write_bytes(BINARY)

    files = {item["path"]: item for item in package_site(tmp_path)}

    assert set(files) == {"index.html", "assets/logo.png"}
    assert files["assets/logo.png"]["encoding"] == "BASE64"


@pytest.mark.workspace
def test_create_publish_and_delete(client: Client, tmp_path: Path):
    (tmp_path / "index.html").write_text("<h1>first</h1>")
    (tmp_path / "old.txt").write_text("old")
    (tmp_path / "logo.png").write_bytes(BINARY)
    created = client.webapps.create(f"companion test {uuid.uuid4().hex[:8]}", tmp_path)
    slug = created.webapp.slug
    try:
        (tmp_path / "old.txt").unlink()
        (tmp_path / "index.html").write_text("<h1>second</h1>")
        published = client.webapps.publish(slug, tmp_path, message="second")

        assert published.deleted == ["old.txt"]
        assert published.webapp.version != created.webapp.version
        files = {file.path: file.content for file in client.webapps.files(slug, content=True)}
        assert files == {"index.html": b"<h1>second</h1>", "logo.png": BINARY}
        assert client.webapps.versions(slug)[0].message == "second"
    finally:
        client.webapps.delete(slug)

    with pytest.raises(NotFoundError):
        client.webapps.get(slug)
