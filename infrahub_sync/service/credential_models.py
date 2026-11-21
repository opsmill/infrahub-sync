"""The request and answer models of the stored credential routes (spec 027, contracts/http-api.md).

``credential_actions`` uses them and exports them again; ``credential_routes`` declares them on the
routes. A request that can hold a value shows no field in its representation or in a validation
error. No answer model for accounts has a field for a value; ``ReleasedBlock``, the answer of the
worker route, is the only model whose fields hold values.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves the resource annotations at runtime.
from typing import Annotated, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from infrahub_sync.product_store.models import (
    STORED_CREDENTIAL_DESCRIPTION_MAX_LENGTH,
    STORED_CREDENTIAL_NAME_PATTERN,
)

FLOW_RUN_ID_MAX_LENGTH: Final = 200
_DESCRIPTION_NOT_PRINTABLE = "description must hold printable characters only"


def _printable_description(value: str) -> str:
    """Refuse a description with a character that is not printable, such as a line break or NUL."""
    if not value.isprintable():
        raise ValueError(_DESCRIPTION_NOT_PRINTABLE)
    return value


Description = Annotated[
    str, Field(max_length=STORED_CREDENTIAL_DESCRIPTION_MAX_LENGTH), AfterValidator(_printable_description)
]
CredentialName = Annotated[str, Field(pattern=STORED_CREDENTIAL_NAME_PATTERN)]


class _SecretRequest(BaseModel):
    """A request that can hold a value: no field is trimmed, echoed in an error or printed."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        """Show no field: a field can be a value."""
        return f"{type(self).__name__}()"

    __str__ = __repr__


class CredentialValueRequest(_SecretRequest):
    """The whole new set of fields of a credential: ``PUT /credentials/{name}/value``."""

    username: str | None = None
    password: str | None = None
    token: str | None = None

    def values(self) -> dict[str, str]:
        """Return the fields that the request sets, by name."""
        candidates = {"username": self.username, "password": self.password, "token": self.token}
        return {name: value for name, value in candidates.items() if value is not None}


class CreateCredentialRequest(CredentialValueRequest):
    """A new credential: ``POST /credentials``."""

    name: CredentialName
    description: Description = ""


class DescribeCredentialRequest(BaseModel):
    """A new description: ``PATCH /credentials/{name}``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    description: Description


class CredentialResource(BaseModel):
    """A stored credential as the routes return it: the names of its fields, never their values.

    ``created_by`` and ``updated_by`` hold account identifiers here; the answer shows names.
    """

    name: str
    description: str
    fields: list[str]
    revision: int
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class CredentialPage(BaseModel):
    """One page of credentials sorted by name; ``next_offset`` is ``None`` on the last page."""

    items: list[CredentialResource]
    next_offset: int | None


class ReleaseCredentialsRequest(BaseModel):
    """The execution that asks for its values: ``POST /worker/runs/{run_id}/credentials``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    flow_run_id: str = Field(min_length=1, max_length=FLOW_RUN_ID_MAX_LENGTH)


class ReleasedBlock(BaseModel):
    """The credential of one block as the worker route releases it: its fields hold the values."""

    model_config = ConfigDict(frozen=True)

    name: str
    revision: int
    fields: dict[str, str] = Field(repr=False)


class ProposedChangeStates(BaseModel):
    """The answer of the second worker route: the state of each earlier proposed change, by its run.

    It holds no value, no address and no proposed-change identifier.
    """

    model_config = ConfigDict(frozen=True)

    states: dict[str, str]


class ReleasedCredentials(BaseModel):
    """The answer of the worker route: the blocks the stage needs that name a credential."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    blocks: dict[str, ReleasedBlock]
