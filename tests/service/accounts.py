"""Accounts of the product store for the tests of the Sync API.

A test names its accounts and, when it needs fixed values, their API tokens::

    accounts = seed_accounts(projection, {"owner": {"token": OWNER_TOKEN}, "admin": {"administrator": True}})
    client = TestClient(create_app(service, accounts.resolver))

The store holds the digest of each token, and the resolver is the one the served application
uses. A record of the store names an account by its identifier (``accounts.identifier("owner")``);
an answer of the HTTP API shows the account name.

No account can sign in with a password: the stored hash is a fixed text, because computing a real
hash for every test would cost about 0.1 second each.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from infrahub_sync.service.auth import ADMIN_ACCOUNT_NAME, ADMIN_ACCOUNT_TOKEN_NAME, StoreAccountResolver
from infrahub_sync.service.credentials import digest, new_api_token

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrahub_sync.product_store import Account, ProductProjection

# The store reads no password, so any text stands for a hash here.
PASSWORD_HASH = "scrypt$32768$8$3$c2FsdA$a2V5"  # noqa: S105 - not a hash of any password.
IDLE_SECONDS = 600
TOKEN_NAME = "test"  # noqa: S105 - the name of a token, not its value.

# The accounts of a test that names none: one administrator and one account that is not.
DEFAULT_ACCOUNTS: Mapping[str, AccountSpec] = {"alice": {"administrator": True}, "bob": {}}


class AccountSpec(TypedDict, total=False):
    """What a test asks for one account. A token value is generated when none is given."""

    token: str
    administrator: bool


class SeededAccounts:
    """The accounts a test created, their API tokens and the resolver that reads them."""

    def __init__(
        self,
        resolver: StoreAccountResolver,
        accounts: Mapping[str, Account],
        tokens: Mapping[str, str],
    ) -> None:
        self.resolver = resolver
        self._accounts = dict(accounts)
        self._tokens = dict(tokens)

    def __repr__(self) -> str:
        """Show the account names only, so a failing test prints no token value."""
        return f"SeededAccounts({', '.join(sorted(self._accounts))})"

    def account(self, name: str) -> Account:
        """Return the stored account with this name."""
        return self._accounts[name]

    def identifier(self, name: str) -> str:
        """Return the identifier that records of the store hold for this account."""
        return self._accounts[name].account_id

    def token(self, name: str) -> str:
        """Return the API token of this account."""
        return self._tokens[name]

    def headers(self, name: str) -> dict[str, str]:
        """Return the request header that presents the API token of this account."""
        return {"Authorization": f"Bearer {self._tokens[name]}"}


def seed_accounts(
    projection: ProductProjection,
    accounts: Mapping[str, AccountSpec] = DEFAULT_ACCOUNTS,
    *,
    environment_token: bool = False,
) -> SeededAccounts:
    """Create the accounts with one API token each; return them with a resolver over this store.

    ``environment_token`` makes the token of the ``admin`` account the one that the environment
    defines: the resolver then lists its value for redaction, as the served application does. The
    value of any other token is in no list of the service.

    Raises:
        ValueError: ``environment_token`` is asked for and no administrator named ``admin`` is given.
    """
    if environment_token and not accounts.get(ADMIN_ACCOUNT_NAME, {}).get("administrator", False):
        msg = "environment_token needs an administrator account named 'admin'"
        raise ValueError(msg)
    created: dict[str, Account] = {}
    tokens: dict[str, str] = {}
    for name, spec in accounts.items():
        from_environment = environment_token and name == ADMIN_ACCOUNT_NAME
        account = projection.create_account(
            name=name,
            password_hash=PASSWORD_HASH,
            administrator=spec.get("administrator", False),
            environment_managed=from_environment,
        )
        value = spec.get("token") or new_api_token()
        projection.create_account_token(
            account.account_id,
            name=ADMIN_ACCOUNT_TOKEN_NAME if from_environment else TOKEN_NAME,
            digest=digest(value),
            environment_managed=from_environment,
        )
        created[name] = account
        tokens[name] = value
    resolver = StoreAccountResolver(
        projection,
        idle_seconds=IDLE_SECONDS,
        admin_token=tokens[ADMIN_ACCOUNT_NAME] if environment_token else None,
    )
    return SeededAccounts(resolver, created, tokens)


def account_identifier(projection: ProductProjection, name: str) -> str:
    """Return the identifier of the account with this name, for a test that holds the store only.

    Raises:
        LookupError: the store has no account with this name.
    """
    account = projection.lookup_account_by_name(name)
    if account is None:
        msg = f"the store has no account named {name!r}"
        raise LookupError(msg)
    return account.account_id
