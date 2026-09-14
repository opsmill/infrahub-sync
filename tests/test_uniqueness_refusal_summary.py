"""`[F6]` A uniqueness refusal is named for the operator, and nothing else server-said is.

G1 M8 measured exactly one thing an operator needs told apart from a generic rejection: the
destination refusing a duplicate on a kind with no HFID. Its signature is narrow and was
measured, not guessed — transport HTTP **200**, `extensions.code` the generic
`UNDEFINED_ERROR`, `extensions.http_status` **422**, and a message beginning
`Violates uniqueness constraint '<name>'`.

Two of those four are traps. The transport status is 200 for a refused mutation (G1 F2), so
anything reading it sees success; `extensions.code` is `UNDEFINED_ERROR`, which says nothing
(G1 F1). Only `extensions.http_status` and the message prefix discriminate, so only those
drive the classification.

The classifier is a narrow exception to the rule that server text is suppressed: it reports
the constraint name and nothing else from the response. Everything outside the measured
signature keeps the existing category-only summary, and the redaction canary in
`tests/adapters/test_infrahub_planned_write.py` still has to pass.
"""

from __future__ import annotations

from infrahub_sdk.exceptions import GraphQLError

from infrahub_sync import potenda

CONSTRAINT_NAME = "name-scope"
SECRET = "SYNTHETIC_SECRET_CANARY"  # noqa: S105 - a deliberate redaction canary


def uniqueness_error(*, message: str | None = None, http_status: int = 422) -> GraphQLError:
    """The server's measured answer to a duplicate on a constrained kind (G1 M8)."""
    return GraphQLError(
        [
            {
                "message": message or f"Violates uniqueness constraint '{CONSTRAINT_NAME}'",
                "extensions": {"code": "UNDEFINED_ERROR", "http_status": http_status},
                "path": ["TestingProbeUniqueUpsert"],
            }
        ],
        query=f'mutation {{ create(api_token: "{SECRET}") }}',
        variables={"api_token": SECRET},
    )


def test_the_measured_uniqueness_signature_is_classified_as_a_uniqueness_refusal() -> None:
    """The one server refusal an operator has to be able to act on."""
    summary = potenda._operational_failure_summary(uniqueness_error())

    assert "uniqueness constraint" in summary
    assert CONSTRAINT_NAME in summary


def test_the_uniqueness_summary_reports_the_server_status_and_not_the_transport_status() -> None:
    """G1 F2: the transport says 200 for a refused mutation, so 422 must come from extensions."""
    summary = potenda._operational_failure_summary(uniqueness_error())

    assert "422" in summary
    assert "200" not in summary


def test_the_uniqueness_summary_carries_no_other_server_or_request_text() -> None:
    """The classifier widens disclosure by exactly one constraint name, and no further."""
    summary = potenda._operational_failure_summary(uniqueness_error())

    assert SECRET not in summary
    assert "api_token" not in summary
    assert "mutation" not in summary


def test_a_rejection_without_the_measured_signature_is_not_classified() -> None:
    """A 200/`UNDEFINED_ERROR` response saying something else stays a category-only summary."""
    other = GraphQLError(
        [
            {
                "message": "the destination rejected this object",
                "extensions": {"code": "UNDEFINED_ERROR", "http_status": 422},
            }
        ],
        query="mutation { ... }",
    )

    summary = potenda._operational_failure_summary(other)

    assert "uniqueness constraint" not in summary
    assert "GraphQLError" in summary
    assert "the destination rejected this object" not in summary


def test_a_uniqueness_message_without_the_measured_status_is_not_classified() -> None:
    """Both halves of the signature are required: the status alone does not decide it."""
    summary = potenda._operational_failure_summary(uniqueness_error(http_status=500))

    assert "uniqueness constraint" not in summary
    assert "GraphQLError" in summary


def test_a_plain_graphql_rejection_keeps_its_category_only_summary() -> None:
    """The established behaviour for everything the classifier does not recognise."""
    summary = potenda._operational_failure_summary(GraphQLError([{"message": "boom"}], query="mutation { ... }"))

    assert summary == "a destination GraphQL rejection (GraphQLError)"
