"""The OpenHEXA user a token belongs to."""

from dataclasses import dataclass

from openhexa.companion._transport import Transport
from openhexa.companion.errors import AuthenticationError

ME_QUERY = """
query Me {
  me { user { id email } }
}
"""


@dataclass(frozen=True)
class User:
    """An OpenHEXA user.

    Attributes:
        id: Identifies the user across workspaces.
        email: Email address the user signs in with.
    """

    id: str
    email: str


def get_user(transport: Transport) -> User:
    """Read the user the token belongs to.

    Raises:
        AuthenticationError: OpenHEXA did not identify a user from the token.
    """
    data = transport.query(ME_QUERY)["me"]["user"]
    if data is None:
        raise AuthenticationError(f"OpenHEXA at {transport.host} did not identify a user")
    return User(id=data["id"], email=data["email"])
