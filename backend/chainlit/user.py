"""The user an auth callback returns.

Plain dataclasses. The persistence package has its own ``UserRecord`` and
the auth controller its own ``AuthenticatedUser``; this is only the object
an application constructs in ``password_auth_callback`` / ``oauth_callback``
and reads back from ``cl.context.session.user``.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Literal, Optional, TypedDict

Provider = Literal[
    "credentials",
    "header",
    "github",
    "google",
    "azure-ad",
    "azure-ad-hybrid",
    "okta",
    "auth0",
    "descope",
]


class UserDict(TypedDict):
    id: str
    identifier: str
    display_name: Optional[str]
    metadata: Dict


@dataclass
class User:
    identifier: str
    display_name: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PersistedUser(User):
    # No defaults on purpose: a persisted user without a row id is a
    # contradiction, and ``kw_only`` is what lets required fields follow the
    # defaulted ones inherited from ``User``.
    id: str = field(kw_only=True)
    createdAt: str = field(kw_only=True)
