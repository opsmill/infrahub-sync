# Contract: web interface

This contract covers User Stories 2 and 4. On 2026-11-18 the maintainer instructed that both are
built now, to replace the token field from the start (spec "Assumptions"). They no longer wait for
the agreement of the colleague who builds the web interface.

## Before User Story 2

No change to the pages. The "Sync API token" field accepts an API token of an account, because
the API accepts it as a bearer value (FR-015).

## User Story 2: sign-in

- **The sign-in form** replaces the token field. It has an account name, a password and one
  refusal message for every refused sign-in.
- **The session token** from `POST /api/auth/sign-in` is kept where the token is kept today: in
  `sessionStorage`, for the browser tab. No cookie (spec 017, D-2).
- **A `401` for the held session** returns the person to the sign-in form, as a refused token
  does today.
- **The signed-in account** is displayed in the page header, with a "Sign out" action that calls
  `POST /api/auth/sign-out` and removes the session token. `GET /api/me` returns the account name
  in `actor`, and `administrator`. The answer of the sign-in also holds the account
  (`SessionResource.account`) and the idle period in seconds (`idle_seconds`).
- **Accounts on records**:
    - a run displays the account that started it;
    - an approved plan displays the account that approved it;
    - a configuration version displays the account that registered it;
    - `Deleted account <short identifier>` is displayed as the API returns it.
- **The rule in `AGENTS.md`** becomes: "Name the account where it explains who acted: the
  signed-in account, the account that started a run, approved a plan or registered a version."

## User Story 4: accounts and tokens

- **"Accounts" page**, for administrators: the list with level and state, a filter on active
  accounts, and the actions create, set level, set password, disable, enable, unlock and delete.
  Unlock (`POST /api/accounts/{name}/unlock`) is shown for an account whose
  `sign_in_refused_until` is in the future. Delete asks for confirmation and says it cannot be
  reversed.
- **"My account" page**, for every account: change the password, list the tokens, create a token
  and delete a token. A created token value is displayed once, with a copy action.
- **The `admin` account** is displayed with a note that the deployment's environment defines it.
  Its only action is unlock, shown when its `sign_in_refused_until` is in the future. The API
  refuses every other action on `admin` with `409`, code `environment-managed-account`.
- **An account that is not an administrator** is not offered the "Accounts" page.

Updated 2026-11-18: unlock was missing from the action list, although task T064 lists it. The
maintainer decided that the page offers it, and that it is the one action offered for `admin`.

## Checks

The frontend has no test runner. Each story is checked with `pnpm check`, `pnpm typecheck`,
`pnpm build`, regenerated API types, and the manual scenarios of [quickstart.md](../quickstart.md).
