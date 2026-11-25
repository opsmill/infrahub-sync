# Notes: web interface prototypes of stored credentials (T074)

T074 built three versions of each screen behind a picker. The maintainer picked one version per
screen; [Choices](#choices) records them. T075 to T077 built the chosen versions, T078 is recorded
as a follow-up of spec 025, and the prototype is removed.

This is the second round. The first round placed the prototype inside the signed-in page frame,
which reads the Sync API, so it could not be opened without a running Sync service. The second round
keeps four versions from the first round and replaces the other eight; see
[Versions kept from the first round](#versions-kept-from-the-first-round).

## How the prototype was opened

The prototype is removed; see [How to remove the prototype](#how-to-remove-the-prototype). It was a
page of its own that only the Vite development server served, at `/proto/credentials`. It needed no
Sync API and no sign-in, and it sent no request: the list, the answers to each change and the
refusals were sample data held in the browser tab.

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

Three of the new versions continue a direction of the first round. The maintainer can ask for a
replacement if a version is too close to the one it continues:

- Forms, "In the row", continues "On the page": both keep the form on the page without an overlay.
  The first round opened a panel above the list; this round puts the form in place of the row.
- Delete, "In the row", continues "On the page": both use a confirmation that is not modal and keep
  the listing until it is dismissed. This round puts it in place of the row and adds "Try again".
- Run details, "In the details", continues "By credential": both group the reads by credential and
  block and say when the value changed during the run. This round moves them into the "Details"
  card instead of a section of their own.

## Not built in the prototype

- The drop-area offer "Create credential `<name>`" (T078). Spec 025 is not merged on this branch:
  `dev/specs/` has no `025` directory and `frontend/src` has no drop area. See
  [Follow-up of spec 025](#follow-up-of-spec-025-the-drop-area-offer-t078).
- A link from a schedule name in the in-use listing. The web interface has no page for one schedule,
  so the listing shows schedule names as text and configuration IDs as links. This is still the case
  in the built page.
- A link from a credential name in "Credentials used" to the Credentials page, and the "Credentials"
  entry of the navigation. T075 and T077 added both.

## Decisions that the maintainer did not confirm

- "The form is cleared after each request": the built form clears the token, the password and the
  username after every answer, and keeps the name, the description and the field choice, so a person
  can correct a refused request. It is not known whether the contract means the whole form.
- Each create or change-value request, and each press of "Delete" or "Try again", sends a new
  `Idempotency-Key`: the values are cleared after every answer, so each request is a new intent, and
  "Try again" must not replay a stored refusal. "Edit description" keeps one key until the
  description changes.
- The list opens with the order "By name", as the prototype did.

## How to remove the prototype

The prototype was removed after the maintainer picked the versions below. `frontend/proto/` and
`frontend/src/proto/credentials/` are deleted, and its development server is stopped. No Vite
setting served it, so no configuration changed. The sections above describe what the prototype
offered; the chosen versions are built in `frontend/src/features/credentials/`,
`frontend/src/routes/credentials.tsx` and `frontend/src/features/runs/CredentialsUsed.tsx`.

## Choices

The maintainer picked one version per screen. The maintainer gave no reason for a rejection; each
rejected version below names the cost that the tables above record for it.

- Credentials page: list: version 3, "Find".
    - A search field over the name, the description and the account that last changed the value.
    - The fields set as a filter, with the count of each set: "All", "Token", "Username and
      password", "Username and token".
    - Two orders: "By name" and "Last changed first". The page opens with "By name", as the
      prototype did; the maintainer did not say which order the page opens with.
    - One row per credential: the name, the description on one line with the whole text in the
      tooltip of the line, then the fields set, the revision, and when and by whom the value last
      changed.
    - An administrator views one "Actions" menu per row: "Change value", "Edit description",
      "Delete".
    - Rejected: "Table", which scrolls sideways on a narrow window and where a long name takes four
      lines; "Rows that open", where columns cannot be compared and each look at the facts takes one
      press. Reason not given by the maintainer.
- Create and change-value forms: version 3, "In the row".
    - No overlay: the form takes the place of the row it changes, or opens above the first row for a
      new credential. Escape cancels.
    - The field choice is one line of three radio buttons: "Token", "Username and password",
      "Username and token". The token and the password are password inputs.
    - After each answer the token, the password and the username are cleared; the name, the
      description and the field choice stay.
    - Rejected: "Dialog", which covers the list; "Side sheet", a taller form whose sheet covers the
      whole page on a phone. Reason not given by the maintainer.
- Delete confirmation: version 2, "In the row".
    - No overlay: the row turns into the question "Delete `<name>`?", then, on
      `409 credential-in-use`, into the refusal with the listing of configurations and schedules.
    - The listing stays in the row until the person presses "Dismiss"; "Try again" sends the
      deletion again.
    - Rejected: "Dialog", where the listing covers the list and closes with the dialog; "Guided
      sheet", which asks for more reading for each deletion and whose steps repeat the listing.
      Reason not given by the maintainer.
- Run details: "Credentials used": version 3, "Stage grid".
    - One row per block and credential, one column per stage, the revision in each cell.
    - A revision that differs from the one the first stage read is marked, and a stage that read
      nothing shows a dash.
    - Rejected: "Table", where a credential that three stages read takes three rows and a change of
      revision must be found by reading; "In the details", where the stages are a list of words and
      the reads are part of the "Details" section instead of a section of their own. Reason not
      given by the maintainer.

## Follow-up of spec 025: the drop-area offer (T078)

When spec 027 was implemented, spec 025 was not merged on the base of its branch, so the offer
of contracts/web-interface.md was recorded here as a follow-up. The drop area and the package
review exist on `feature/v3-develop` now, and the offer is built as the next section describes.

### Built: the refused registration in the package review

Decided on 2026-11-25 by the maintainer, from a prototype with two versions at
`/proto/credential-refusal.html`. The prototype is deleted.

- Chosen: "In the refusal". The notice next to "Register" lists each refused block with the name
  of the stored credential, the reason, and what the signed-in account can do.
    - `credential-not-found`: "`<name>` does not exist. The block needs `<fields>`."
    - `credential-field-missing`: "`<name>` lacks `<fields>`."
    - An administrator views "Create credential `<name>`" or "Change the value of `<name>`". The
      link opens the Credentials page in a new tab with the form open
      (`/credentials?new=<name>` or `/credentials?value=<name>`), so the package stays open.
    - Another account views "Ask an administrator to create the credential `<name>`." or
      "Ask an administrator to change the value of the credential `<name>`."
    - The notice reads the stored credentials again while it is open. A finding whose credential
      exists with every field shows "Exists now" or "Holds the field now".
- Rejected: "In the credentials list", where the "Stored credentials" section of the review
  holds the reason and the link for each block, and the notice next to "Register" is one sentence
  that points to that section. Reason not given by the maintainer. Its cost in the prototype: the
  remedy is away from the "Register" button that the user pressed.
- The check that runs before registration reports the same two codes as findings and disables
  "Register", so the refusal above appears only when, between the check and the registration, a
  credential is deleted or its value is replaced by one that lacks a field the block needs. Each of these findings of the check therefore shows the same
  link or the same sentence under its message, and the notice of the findings has a
  "Validate again" button. This part was not in the prototype and the maintainer has not
  reviewed it.
- Code: `frontend/src/features/configurations/CredentialRefusal.tsx`, with the text in
  `frontend/src/features/configurations/package-review.ts`.
