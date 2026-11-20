"""Small helpers shared by the areas of the library."""

import mimetypes
import uuid
from collections.abc import Iterable

from openhexa.companion.errors import InvalidInputError

# Number of valid options listed in error messages.
MAX_OPTIONS = 20
# Content types missing from the table built into `mimetypes`.
EXTRA_CONTENT_TYPES = {
    ".geojson": "application/geo+json",
    ".gpkg": "application/geopackage+sqlite3",
    ".parquet": "application/vnd.apache.parquet",
    ".zst": "application/zstd",
}
# Content types of compressed files, by the encoding `mimetypes` reports for them.
COMPRESSED_CONTENT_TYPES = {
    "bzip2": "application/x-bzip2",
    "compress": "application/x-compress",
    "gzip": "application/gzip",
    "xz": "application/x-xz",
}
# Guesses content types from Python's built-in table, ignoring the system's MIME files so
# that a file gets the same type on every machine.
MIME_TYPES = mimetypes.MimeTypes()
for _suffix, _content_type in EXTRA_CONTENT_TYPES.items():
    MIME_TYPES.add_type(_content_type, _suffix)


def check_uuid(value: str, what: str) -> None:
    """Raise `InvalidInputError` unless `value` is a UUID, which OpenHEXA requires for IDs."""
    try:
        uuid.UUID(value)
    except ValueError:
        raise InvalidInputError(f"Invalid {what} '{value}': expected a UUID") from None


def format_options(items: Iterable[str]) -> str:
    """Quote and join valid options for an error message, truncating long lists."""
    names = list(items)
    if not names:
        return "none"
    shown = ", ".join(f"'{name}'" for name in names[:MAX_OPTIONS])
    if len(names) > MAX_OPTIONS:
        shown += f" and {len(names) - MAX_OPTIONS} more"
    return shown


def guess_content_type(path: str) -> str:
    """Guess the media type of a file from the extension of its `path`.

    Compressed files, such as `cases.csv.gz`, get the type of their compression, since
    their content is not readable as the inner type. Unknown extensions get
    `application/octet-stream`.
    """
    content_type, encoding = MIME_TYPES.guess_file_type(path)
    if encoding is not None:
        return COMPRESSED_CONTENT_TYPES.get(encoding, "application/octet-stream")
    return content_type or "application/octet-stream"
