# Notes: web interface prototypes of stored credentials (T074)

T074 built three versions of each screen behind a picker. The maintainer picks one version per
screen and records it under [Choices](#choices). T075 to T078 start only after that.

This is the second round. The first round placed the prototype inside the signed-in page frame,
which reads the Sync API, so it could not be opened without a running Sync service. The second round
keeps three versions from the first round and replaces the other five; see
[Versions kept from the first round](#versions-kept-from-the-first-round).

## How to open the prototype

The prototype is a page of its own that only the Vite development server serves. It needs no Sync
API and no sign-in, and it sends no request: the list, the answers to each change and the refusals
are sample data held in the browser tab.

1. In `frontend/`, start the development server on a free port, for example
   `pnpm exec vite dev --port 5310 --strictPort`.
2. Open `http://localhost:5310/proto/credentials`.

The address keeps the screen, the version and what is open, so a link to one state can be shared,
for example `/proto/credentials?screen=form&v=2&open=value:netbox-lab&answer=key`.

Controls of the prototype, which are not part of the design:

- The dark picker at the bottom of the window changes the version. The keys `1` to `3` and the arrow
  keys change it too, while no field has the focus. The picker works while a dialog or a sheet is
  open, and the next version opens the same form or confirmation.
- "Screen" chooses one of the four screens below.
- "Signed-in account" shows the page for an administrator or for another account.
- "List" shows five credentials, no credential, the loading state, or a Sync API that does not answer.
- "Open" opens a form or a confirmation: a new credential, a change of value, a deletion, or a
  description.
- "Answer to the next change" makes the next create, change or delete request answer with
  `503 credential-key-not-configured`, `422 credential-fields-invalid`, `403 forbidden`, or no answer.
- "Restore the sample data" brings back the five sample credentials.
- "Run" (screen "Run details" only) chooses the sample run: the same revisions in each stage, a value
  that changed during the run, or no stored credential.

Sample data to try:

- Deleting `netbox-lab`, `infrahub-main` or the credential with the long name answers
  `409 credential-in-use` with configurations and schedules. Deleting `nautobot-staging` or
  `peeringmanager-api` succeeds.
- Creating a credential with the name of an existing one answers `409 credential-exists`.
- One credential has a 62-character name and a description of about 500 characters, one has no
  description, one was last updated by an account with a 35-character name, and one was last
  updated by `Deleted account 3f9a1c2e`.

## Screens and versions

On the screens "Create and change-value forms" and "Delete confirmation", the list is the "Table"
version, so that only the form or the confirmation changes between versions. On the screen
"Credentials page: list", the form and the confirmation are the "Dialog" versions. "Edit
description" is one dialog, the same in every version.

### Credentials page: list (`?screen=list`, the default)

Each version has the administrator view and the view of another account, the empty list, the loading
state and the error state.

| Version | Axis | When it fits | Its cost |
| --- | --- | --- | --- |
| 1. Table | Columns to compare: the six columns of the contract and one "Actions" menu per row, as on the Accounts page. A long description shows two lines and a "Show all" button. | Many credentials that a person scans and compares by column, for example by revision or by the date of the last change. | The table scrolls sideways on a narrow window, and a long name takes four lines. |
| 2. Rows that open | One line per credential; pressing the line opens the whole description, the six facts, the line `credential: <name>` with a copy button, and the actions as buttons. | A person who looks up one credential, or copies its name into a configuration. | Columns cannot be compared, and each look at the facts takes one press. |
| 3. Find | A search field over the name, the description and the account; the fields set as a filter with counts; two orders, by name or last changed first. Each row says when and by whom the value last changed. | Tens of credentials, or a person who checks which values changed recently. | The description shows one line; the rest is only in the tooltip of the line, which a touch screen does not show. |

### Create and change-value forms (`?screen=form`)

Every version offers exactly the three field choices of the contract: "Token", "Username and
password", "Username and token". The token and the password are password inputs. After each answer
the token, the password and the username are cleared.

| Version | Axis | When it fits | Its cost |
| --- | --- | --- | --- |
| 1. Dialog | Modal, one step: the field choice as radio buttons above the inputs. Same pattern as "Create an account". | Consistency with the Accounts page; the whole request is in view at once. | The dialog covers the list. |
| 2. Side sheet | A sheet on the right; the list stays partly in view on the left. Below the inputs, "After you save" states the stored field names, the revision after the change, and the line `credential: <name>` as the name is typed. | A person who wants to check what the request will change before sending it, for example the revision a change of value creates. | A taller form, and on a phone the sheet covers the whole page. |
| 3. In the row | No overlay: the form takes the place of the row it changes, or opens above the first row for a new credential. Escape cancels. | A person who changes the value of several credentials one after the other and keeps the other rows in view. | The rows below move down while the form is open, and the form is less visible than a dialog. |

### Delete confirmation (`?screen=delete`)

The Sync API reports the configurations and schedules that name a credential only when it refuses
the deletion (`DELETE /credentials/{name}`, `409 credential-in-use`), so each version shows the
listing after the request. In every version the listing is lost when the person leaves the page. A
configuration link in the listing leads to the configuration page of the web interface, which needs
a signed-in session and a Sync API; the sample configuration IDs do not exist there.

| Version | Axis | When it fits | Its cost |
| --- | --- | --- | --- |
| 1. Dialog | Modal; a refusal replaces the question with the listing, in the same dialog. Same pattern as the deletion of an account. | Consistency with the Accounts page. | The listing covers the list and closes with the dialog. |
| 2. In the row | No overlay: the row turns into the question, then into the refusal with the listing. The listing stays in the row until the person dismisses it, and "Try again" sends the deletion again. | A person who opens the listed configurations in other tabs, fixes them, and comes back to the same row. | A confirmation that is not modal is easier to overlook, and it moves the rows below. |
| 3. Guided sheet | A sheet that first shows the facts of the credential (description, fields set, revision, last change) to confirm it is the right one; a refusal becomes numbered steps, one per configuration and schedule, the time of the refusal, and "Try again". | Deployments where credentials have similar names, or where the person deleting it is not the person who created it. | More reading for each deletion, and the steps repeat what the listing says. |

### Run details: "Credentials used" (`?screen=used`)

The section is shown on a sample run page, between "Details" and "Files".

| Version | Axis | When it fits | Its cost |
| --- | --- | --- | --- |
| 1. Table | One row per read with the four columns of the contract: stage, block, name, revision. | The exact record, in the order the Sync API returns it. | A credential that three stages read takes three rows, and a change of revision must be found by reading. |
| 2. In the details | No separate section: a "Credentials used" part at the end of the "Details" card, one line per block with the revisions and the stages that read them. A change of revision is stated in words. | A person who reads the source and the destination of a run together with their credentials. | The stages are a list of words rather than columns; the contract names a section, and this is part of an existing one. |
| 3. Stage grid | One row per block and credential, one column per stage, the revision in each cell. A revision that differs from the one the first stage read is marked. | A person who checks whether a value changed between the plan and the apply. | The grid is wider with each stage, and a stage that read nothing shows a dash. |

## Versions kept from the first round

- Credentials page: "Table". It is the pattern of the Accounts page, so it is the version to compare
  the others against.
- Create and change-value forms: "Dialog". It is the pattern of "Create an account".
- Delete confirmation: "Dialog". It is the pattern of the deletion of an account.
- Run details: "Table". It shows the record of the contract without any grouping.

Replaced: "Cards" and "Names and details" (list), "On the page" and "Two steps" (forms), "Type the
name" and "On the page" (delete), "By stage" and "By credential" (run details).

## Not built

- The drop-area offer "Create credential `<name>`" (T078). Spec 025 is not merged on this branch:
  `dev/specs/` has no `025` directory and `frontend/src` has no drop area.
- A link from a schedule name in the in-use listing. The web interface has no page for one schedule,
  so the listing shows schedule names as text and configuration IDs as links.
- A link from a credential name in "Credentials used" to the Credentials page. The route
  `/credentials` does not exist before T075.
- The "Credentials" entry of the navigation. It belongs to T075. The prototype draws a copy of the
  page frame with that entry, and its other links lead to the real pages.

## Decisions to confirm with the choice

- "The form is cleared after each request": the prototype clears the token, the password and the
  username after every answer, and keeps the name, the description and the field choice, so a person
  can correct a refused request. It is not known whether the contract means the whole form.
- Each sample request ignores `Idempotency-Key`. The actions of T073 (`createCredential`,
  `setCredentialValue`, `useDescribeCredential`, `useDeleteCredential`) send it.

## How to remove the prototype

Delete `frontend/proto/` and `frontend/src/proto/credentials/`. Nothing else imports them, and no
route of the web interface lists them. `pnpm build` builds `index.html` only, so the prototype is
not part of the built web interface.

## Choices

The maintainer records one version per screen here, and what was rejected and why.

- Credentials page: list:
- Create and change-value forms:
- Delete confirmation:
- Run details: "Credentials used":
