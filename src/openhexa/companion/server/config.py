"""Configuration of the companion service, read from the environment."""

import base64
import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

# Where users download the openhexa-mcp skill, released with the MCP server.
SKILL_URL = "https://github.com/yannforget/openhexa-companion/releases/latest"
# Minimum length of `COMPANION_SECRET_KEY`, in characters.
MIN_SECRET_KEY_LENGTH = 32
# Hosts allowed to use plain HTTP, for local development.
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ConfigError(Exception):
    """The environment does not configure the service correctly."""


@dataclass(frozen=True)
class Config:
    """Settings of one companion deployment.

    Attributes:
        public_url: Base URL under which AI clients and browsers reach the service.
        openhexa_url: The only OpenHEXA instance tokens are sent to.
        secret_key: Master secret from which every key of the service is derived.
        database: Path of the SQLite file, or `:memory:`.
    """

    public_url: str
    openhexa_url: str
    secret_key: str = field(repr=False)
    database: Path | str

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_url", _check_url("COMPANION_PUBLIC_URL", self.public_url))
        if urlsplit(self.public_url).path:
            # OAuth metadata and the MCP endpoint are served from the root of the domain.
            raise ConfigError("COMPANION_PUBLIC_URL must not have a path")
        object.__setattr__(
            self, "openhexa_url", _check_url("COMPANION_OPENHEXA_URL", self.openhexa_url)
        )
        if len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            raise ConfigError(
                f"COMPANION_SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters long"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Config:
        """Read the configuration from environment variables.

        Raises:
            ConfigError: A variable is missing or invalid.
        """
        names = (
            "COMPANION_PUBLIC_URL",
            "COMPANION_OPENHEXA_URL",
            "COMPANION_SECRET_KEY",
            "COMPANION_DATABASE",
        )
        if missing := [name for name in names if not env.get(name)]:
            raise ConfigError(f"Missing environment variables: {', '.join(missing)}")
        return cls(
            public_url=env["COMPANION_PUBLIC_URL"],
            openhexa_url=env["COMPANION_OPENHEXA_URL"],
            secret_key=env["COMPANION_SECRET_KEY"],
            database=env["COMPANION_DATABASE"],
        )

    @property
    def mcp_url(self) -> str:
        """URL of the MCP endpoint, which access tokens are issued for."""
        return f"{self.public_url}/mcp"

    def derive_key(self, purpose: str) -> bytes:
        """Derive a 32-byte key for `purpose` from the secret key.

        Separate keys keep one use, such as signing file URLs, from forging another.
        """
        return hmac.digest(self.secret_key.encode(), f"companion:{purpose}".encode(), "sha256")

    def fernet_key(self) -> bytes:
        """The key encrypting stored OpenHEXA tokens, in the format Fernet expects."""
        return base64.urlsafe_b64encode(self.derive_key("fernet"))


def _check_url(name: str, url: str) -> str:
    """Return `url` without a trailing slash, raising unless it is an HTTPS base URL."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ConfigError(f"{name} must be an absolute URL, got '{url}'")
    if parts.scheme == "http" and parts.hostname not in LOOPBACK_HOSTS:
        raise ConfigError(f"{name} must use HTTPS unless it points to this machine")
    if parts.query or parts.fragment:
        raise ConfigError(f"{name} must not have a query string or fragment")
    return url.rstrip("/")
