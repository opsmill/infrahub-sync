# Notes: web interface prototypes of stored credentials (T074)

T074 built several versions of each screen behind a picker. The maintainer picks one version per
screen and records it under [Choices](#choices). T075 to T078 start only after that.

## How to open the prototype

The prototype is a throwaway route of the web interface. It sends no request for credentials: the
list, the answers to each change and the refusals are sample data held in the browser tab. The page
frame around it (the header, the account menu) still reads the Sync API, so open it in the usual
development setup and sign in first.

1. From the repository root, start the web interface against a Sync service: `uv run invoke dev.frontend`.
2. Sign in on the page.
3. Open `/proto/credentials`, for example `http://localhost:3000/proto/credentials`.

The address keeps the screen and the version, so a link to one version can be shared:
`/proto/credentials?screen=form&v=2`.

Controls of the prototype, which are not part of the design:

- The dark picker at the bottom of the window changes the version. The keys `1` to `3` and the arrow
  keys change it too, while no dialog is open and no field has the focus.
- "Screen" chooses one of the four screens below.
- "Signed-in account" shows the page for an administrator or for another account.
- "List" shows five credentials, no credential, the loading state, or a Sync API that does not answer.
- "Answer to the next change" makes the next create, change or delete request answer with
  `503 credential-key-not-configured`, `422 credential-fields-invalid`, `403 forbidden`, or no answer.
- "Restore the sample data" brings back the five sample credentials.
- "Run" (screen "Run details" only) chooses the sample run.

Sample data to try:

- Deleting `netbox-lab`, `infrahub-main` or the credential with the long name answers
  `409 credential-in-use` with configurations and schedules. Deleting `nautobot-staging` or
  `peeringmanager-api` succeeds.
- Creating a credential with the name of an existing one answers `409 credential-exists`.
- One credential has a 62-character name and a description of about 500 characters, one has no
  description, and one was last updated by `Deleted account 3f9a1c2e`.

## Screens and versions

On the screens "Create and change-value forms" and "Delete confirmation", the list is the "Table"
version, so that only the form or the confirmation changes between versions. On the screen
"Credentials page: list", the form and the confirmation are the "Dialog" versions.

### Credentials page: list (`?screen=list`, the default)

Each version has the administrator view and the view of another account, the empty list, the loading
state and the error state.

| Version | Approach | When it fits | Its cost |
| --- | --- | --- | --- |
| 1. Table | One row per credential with the six columns of the contract and one "Actions" menu per row, as on the Accounts page. | Many credentials that a person scans and compares by column. | A long description is cut after two lines, and the table scrolls sideways on a narrow window. |
| 2. Cards | One card per credential with the whole description in view and each action as a button. | Few credentials whose description carries the information a person needs. | About five credentials fit in one window; columns cannot be compared. |
| 3. Names and details | A list of names on the left; the chosen credential on the right with every fact, including "Created by" and "Created at", and the line `credential: <name>` to copy into a configuration. | A person looks up one credential and then acts on it or names it in a configuration. | Only one credential's facts are in view at a time, and viewing another takes one more press. |

### Create and change-value forms (`?screen=form`)

Open "New credential", or "Change value" in the "Actions" menu of a row. Every version offers exactly
the three field choices of the contract: "Token", "Username and password", "Username and token". The
token and the password are password inputs.

| Version | Approach | When it fits | Its cost |
| --- | --- | --- | --- |
| 1. Dialog | One modal dialog; the field choice is three radio buttons above the inputs. Same pattern as "Create an account". | Consistency with the Accounts page; the whole request is in view in one step. | The dialog covers the list. |
| 2. On the page | No modal: a panel opens above the list, which stays in view; the field choice is a segmented control with one line of help. | A person who reads the list (names, descriptions) while typing. | The panel moves the list down, and a person can scroll away from an unsent form. |
| 3. Two steps | A modal dialog in two steps: first the name and the field choice as three options, each with a sentence that says which systems use it; then the values. | People who do not know which fields their system reads. | One more press for each credential, for every person. |

### Delete confirmation (`?screen=delete`)

Open "Delete" in the "Actions" menu of a row. The Sync API reports the configurations and schedules
that name a credential only when it refuses the deletion, so each version shows the listing after
the request. In every version the listing is lost when the person leaves the page. A configuration
link in the listing opens the real configuration page, which reads the Sync API; the sample
configuration IDs do not exist there.

| Version | Approach | When it fits | Its cost |
| --- | --- | --- | --- |
| 1. Dialog | A modal confirmation; a refusal replaces the question with the listing, in the same dialog. Same pattern as the deletion of an account. | Consistency with the Accounts page. | The listing covers the list and closes with the dialog. |
| 2. Type the name | A modal confirmation whose button works only after the person types the name of the credential. | Deployments where a deleted credential stops scheduled runs and a wrong press is costly. | Slower for every deletion, and the name must be typed on a phone too. |
| 3. On the page | No modal: a confirmation strip opens above the list. After a refusal the listing stays on the page until the person dismisses it. | A person who reads the listing next to the list, or opens its links in other tabs, then deletes again. | A confirmation that is not modal is easier to overlook, and it moves the list down. |

### Run details: "Credentials used" (`?screen=used`)

The section is shown between the "Details" and "Files" sections of a sample run page.

| Version | Approach | When it fits | Its cost |
| --- | --- | --- | --- |
| 1. Table | One row per read with the four columns of the contract: stage, block, name, revision. | The exact record, in the order the Sync API returns it. | A credential that three stages read takes three rows. |
| 2. By stage | One group per stage, in the layout of the "Details" section; each group lists the block, the name and the revision. | A person who reads the run stage by stage, as the timeline does. | The same credential is repeated in each stage. |
| 3. By credential | One line per credential and block, with the stages that read it. When two stages read different revisions, the line says that the value changed during the run. | A person who checks which value a run used after a credential was changed. | The stage order is less visible than in the other versions. |

## Not built

- The drop-area offer "Create credential `<name>`" (T078). Spec 025 is not merged on this branch:
  `dev/specs/` has no `025` directory and `frontend/src` has no drop area.
- A link from a schedule name in the in-use listing. The web interface has no page for one schedule,
  so the listing shows schedule names as text and configuration IDs as links.
- A link from a credential name in "Credentials used" to the Credentials page. The route
  `/credentials` does not exist before T075.
- The "Credentials" entry of the navigation. It belongs to T075.

## Decisions to confirm with the choice

- "The form is cleared after each request": the prototype clears the token, the password and the
  username after every answer, and keeps the name, the description and the field choice, so a person
  can correct a refused request. It is not known whether the contract means the whole form.
- "Edit description" is one dialog, the same in every version.
- Each sample request ignores `Idempotency-Key`. The actions of T073 (`createCredential`,
  `setCredentialValue`, `useDescribeCredential`, `useDeleteCredential`) send it.

## How to remove the prototype

Delete `frontend/src/proto/credentials/` and `frontend/src/routes/proto.credentials.tsx`, then run
`pnpm generate-routes` in `frontend/` so that `frontend/src/routeTree.gen.ts` no longer lists the
route. Nothing else imports the prototype.

Until it is removed, the route is part of the built web interface: a signed-in person who types
`/proto/credentials` views the sample page. Remove it before the branch is merged.

## Choices

The maintainer records one version per screen here, and what was rejected and why.

- Credentials page: list:
- Create and change-value forms:
- Delete confirmation:
- Run details: "Credentials used":
