"""Small helpers shared by the areas of the library."""

import uuid
from collections.abc import Iterable

# Number of valid options listed in error messages.
MAX_OPTIONS = 20


def check_uuid(value: str, what: str) -> None:
    """Raise `ValueError` unless `value` is a UUID, which OpenHEXA requires for IDs."""
    try:
        uuid.UUID(value)
    except ValueError:
        raise ValueError(f"Invalid {what} '{value}': expected a UUID") from None


def format_options(items: Iterable[str]) -> str:
    """Quote and join valid options for an error message, truncating long lists."""
    names = list(items)
    if not names:
        return "none"
    shown = ", ".join(f"'{name}'" for name in names[:MAX_OPTIONS])
    if len(names) > MAX_OPTIONS:
        shown += f" and {len(names) - MAX_OPTIONS} more"
    return shown
