# Contract: Missing permission and refused token

Evidence is in [research.md](../research.md), R-5.

The HTTP status is read from the `httpx.HTTPStatusError` that the SDK chains as `__cause__`.

| Infrahub response | SDK 1.23.2 raises | Run records |
|---|---|---|
| HTTP 401 | `AuthenticationError` | refused token |
| HTTP 403 with "You do not have the following permission: P" | `AuthenticationError` | missing permission `P` |
| HTTP 403 with "You do not have one of the following permissions: P1 \| P2" | `AuthenticationError` | missing permission, one of `P1`, `P2` |
| HTTP 200 with a GraphQL error starting "You do not have" | `GraphQLError` | missing permission, quoted as above |
| HTTP 403 with any other message | `AuthenticationError` | missing permission, quoting the server message |

- A 403 is never recorded as a refused token (FR-008, SC-006).
- The recorded cause quotes only the permission text, never the token or request headers
  (constitution principle VI).
