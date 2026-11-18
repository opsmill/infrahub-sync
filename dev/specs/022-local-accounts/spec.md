# Feature Specification: Sign in to Sync with a local account

**Feature Branch**: `022-local-accounts`

**Created**: 2026-11-17

**Status**: Draft

**Input**: The idea brief "Local accounts and stored credentials for Sync", which the maintainer
and an agent wrote in an interview on 2026-11-17. This specification covers the first of the
brief's two changes, the local accounts. The second change, credentials for source and destination
systems that Sync stores, gets its own specification later.

Sources:

- The decisions of the interview, listed under "Decisions".
- [`infrahub_sync/service/auth.py`](../../../infrahub_sync/service/auth.py): how the Sync API
  resolves a token today.
- [`docs/docs/compose-deployment.mdx`](../../../docs/docs/compose-deployment.mdx): how an operator
  defines the tokens today.
- [Spec 017](../017-serve-web-interface/spec.md), decision D-2: how a person gives a token to the
  web interface today.
- [ADR 0037](../../adr/0037-an-approval-is-a-record-on-the-run.md): an approval is a record on the
  run.

## Background

### How a person signs in today

- An operator writes a JSON object into `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, in the environment
  of the `sync-api` service. Each entry is a name, a token and an `administrator` flag.
- A person or a script presents one of these tokens. The CLI reads it from
  `INFRAHUB_SYNC_API_TOKEN`. The web interface asks for it in a "Sync API token" field and keeps
  it for the browser tab.
- The Sync API records the entry's name as the `actor` of each run, approval and audit event.

### What a team cannot do today

- **Give a person access without changing the deployment.** To add a person, an operator edits
  `.env` and recreates `sync-api`. To remove a person, the operator does the same.
- **Tell which token is which.** A token has no visible owner and no visible origin. A person can
  take a Sync API token for an Infrahub token, because nothing in the product says otherwise.
- **Give a script its own access.** A script uses a token from the same JSON object, which only
  an operator can create or remove.

### What does not change

Sign-in to Sync does not depend on any Infrahub instance, today or after this feature. An Infrahub
token is a credential that a worker uses to read or write an Infrahub instance. It is never what a
person signs in with.

## Decisions

The maintainer made these decisions in the interview of 2026-11-17.

- **D-1, local accounts replace the tokens of the environment.** An account is stored by Sync. A
  team adds or removes a person through the CLI, the API or the web interface.
- **D-2, the `admin` account is defined by the environment at all times.** The deployment sets
  its password, and optionally one token, in the environment. The Sync API applies them at each
  start. Nothing else can change, disable or delete this account. To recover or rotate, an
  operator edits the variable and restarts `sync-api`.
- **D-3, `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` is removed in the same release, with no transition
  period.** This rests on the assumption that V3 has no production deployment.
- **D-4, an account creates its own API tokens.** The CLI and scripts keep presenting a token in
  `INFRAHUB_SYNC_API_TOKEN`.
- **D-5, two levels only.** An account is an administrator or it is not. Roles, groups and
  read-only access are out of scope.
- **D-6, an administrator can disable an account and can delete an account.** A disabled account
  keeps its name in past records and can be enabled again. A deleted account is shown as "Deleted
  account" with a short identifier, and cannot be restored.
- **D-7, the web interface names accounts.** The rule in `AGENTS.md` that forbids naming a user
  in the web interface is replaced as part of User Story 2.
- **D-8, "account" is the single term.** The API field `actor` keeps its name and holds the
  account name.
- **D-9, four sign-in rules**, listed in FR-020 to FR-023.

The agent proposed this decision during the critique of 2026-11-17. The maintainer confirmed it
on 2026-11-18.

- **D-10, a record refers to an account, not to its name.** Each run, approval and audit event
  refers to the account that acted. Sync turns the reference into a name when it displays the
  record. So a deletion changes no past record, and an approval is never rewritten.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sign in with a local account through the CLI and the API (Priority: P1)

An administrator gives a colleague access to Sync without touching the deployment. The colleague
signs in with the CLI under their own name, replaces the password the administrator chose, runs
the usual commands, and creates a token for a script. Each action is recorded with the
colleague's account. The administrator can replace a lost password and can disable the account.

**Why this priority**: this removes the problem the feature exists for. A team adds a person in
one command, and each record names the person who acted. The API and the CLI are enough to deliver
it, and every other story needs them.

**Independent Test**: on a new deployment that sets only the `admin` password, create an account,
sign in as that account with the CLI, change its password, list runs, create a token and call
the CLI with that token from a script. Then disable the account and check that it is refused. No
deployment file changes and no service restarts.

**Acceptance Scenarios**:

1. **Given** a new deployment whose environment sets the `admin` password, **When** the operator
   starts the deployment and signs in as `admin` with a CLI installed on a workstation, **Then**
   the Sync API accepts the sign-in and `infrahub-sync whoami` returns `admin` as an
   administrator. On the Compose deployment, whose `cli` service keeps no file, the operator
   sets the `admin` token and the `cli` service presents it, with the same result.
2. **Given** a signed-in administrator and a person, Alice, with no account, **When** the
   administrator creates the account `alice` with an initial password, **Then** Alice can sign in
   with the CLI using that name and password, and `infrahub-sync whoami` returns `alice`.
3. **Given** Alice is signed in with the CLI, **When** she runs `infrahub-sync runs list` and then
   creates a plan run, **Then** the Sync API accepts both commands and the run record holds
   `alice` as its `actor`.
4. **Given** Alice is signed in, **When** she creates a token named `ci-nightly`, **Then** the CLI
   displays the token value once, and her list of tokens shows `ci-nightly` with its creation
   date, and never the value.
5. **Given** a script that sets `INFRAHUB_SYNC_API_TOKEN` to the value of `ci-nightly`, **When**
   the script creates a plan run, **Then** the Sync API accepts it and the run record holds
   `alice` as its `actor`.
6. **Given** a deployment that still sets `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`, **When** a person
   presents a token that only that variable defines, **Then** the Sync API refuses the request.
7. **Given** a deployment whose environment does not set the `admin` password, **When** the
   operator starts the deployment, **Then** the Sync API does not start and the message names the
   missing variable.
8. **Given** the scenarios above, **When** a reader searches the CLI output, the API responses and
   the service logs, **Then** no password appears, and the token value appears only in the answer
   to its creation.
9. **Given** Alice is signed in, **When** she changes her password and gives her current
   password, **Then** her next sign-in needs the new password, her other sessions end, and her
   tokens still work.
10. **Given** Alice forgot her password, **When** an administrator sets a new password for her
    account, **Then** she signs in with the new password and every session she had ends.
11. **Given** Alice has a session and a token, **When** an administrator disables her account,
    **Then** the next request with her password, her session or her token is refused, and her
    past runs still display `alice`.
12. **Given** Alice's account is disabled, **When** an administrator enables it, **Then** Alice
    can sign in with her password, and the tokens she had before stay refused.
13. **Given** five failed sign-in attempts for `alice` within 15 minutes, **When** a person tries
    again with the right password, **Then** the attempt is refused with the same message as a
    wrong password, her token `ci-nightly` still works, and an administrator can end the refusal.

---

### User Story 2 - Sign in through the web interface (Priority: P2)

A person opens the web interface, enters their account name and password, and works as
themselves. The interface shows which account is signed in, and which account started a run or
approved a plan.

**Why this priority**: the web interface is the second place where a person signs in. Until this
story is delivered, a person can still use the web interface with an account token (FR-015), so
the story is not needed for the first release.

**Independent Test**: open the web interface in a browser with no saved state, sign in as
`alice` with her password, view a run she started and a run another account started, then sign
out.

**Acceptance Scenarios**:

1. **Given** a browser that is not signed in, **When** a person opens any page of the web
   interface, **Then** the interface displays a sign-in form with an account name and a password,
   and does not ask for a token.
2. **Given** the sign-in form, **When** Alice enters her name and password, **Then** the interface
   opens the page she asked for and displays `alice` as the signed-in account.
3. **Given** the sign-in form, **When** a person enters a wrong password, **Then** the interface
   displays one refusal message that does not say whether the account exists.
4. **Given** Alice is signed in, **When** she opens a run that `bob` started and approved,
   **Then** the page displays `bob` as the account that started the run and as the account that
   approved the plan.
5. **Given** Alice is signed in, **When** she signs out, **Then** the next page she opens displays
   the sign-in form, and the session she had no longer opens any page.

---

### User Story 3 - Delete an account, and manage another account (Priority: P3)

When a person's name must be removed, an administrator deletes the account, and past records
show "Deleted account" in place of the name. An administrator also changes the level of an
account, and views and deletes the tokens of another account.

**Why this priority**: disabling an account (User Story 1) already removes a person's access. A
deletion is needed only when the name itself must go, and the other actions are for a team that
has used accounts for a while.

**Independent Test**: create `alice`, let her start a run, approve a plan and create a token.
Delete her account, view her past records, then create a new account named `alice` and try to
act on the old run.

**Acceptance Scenarios**:

1. **Given** one of Alice's runs is writing to the destination, **When** an administrator
   disables or deletes her account, **Then** the run continues to its end, and an administrator
   can cancel it.
2. **Given** Alice approved a plan that is not applied yet, **When** an administrator deletes her
   account, **Then** the approval stays valid and unchanged, and an administrator can apply the
   plan.
3. **Given** Alice's account is deleted, **When** a person views her past runs, approvals and
   audit events, **Then** each record displays "Deleted account" and the same short identifier,
   and no record displays `alice`.
4. **Given** Alice's account is deleted, **When** an administrator creates a new account named
   `alice`, **Then** the new account cannot apply, approve, cancel or resume a run that the
   deleted account started, and no past record displays it.
5. **Given** an account that is not an administrator, **When** an administrator sets its level
   to administrator, **Then** its next request has administrator rights, with no new sign-in.
6. **Given** Alice has a token, **When** an administrator lists her tokens and deletes it,
   **Then** the next request with that token is refused.
7. **Given** the `admin` account, **When** any account tries to change its password, create or
   delete one of its tokens, change its level, disable it or delete it, **Then** the Sync API
   refuses and the message names the environment variable to edit.

---

### User Story 4 - Manage accounts and tokens in the web interface (Priority: P4)

An administrator creates, disables, enables and deletes accounts in the web interface. Each person
changes their password and manages their tokens there.

**Why this priority**: the CLI already offers every action. The web interface makes them available
to a person who does not use the CLI.

**Independent Test**: with only a browser, an administrator creates an account, the new person
signs in, changes their password and creates a token, and the administrator disables the account.

**Acceptance Scenarios**:

1. **Given** a signed-in administrator, **When** they open the list of accounts, **Then** the
   page displays each account with its level and its state, and can display active accounts only.
2. **Given** a signed-in administrator, **When** they create an account, disable it, enable it
   and delete it, **Then** each action has the same result as the same action in the CLI.
3. **Given** a signed-in account that is not an administrator, **When** it opens the web
   interface, **Then** no page offers an action on another account.
4. **Given** a signed-in person, **When** they create a token, **Then** the page displays the
   value once, and the list of tokens displays the name, the creation date, the expiry date and
   the date of last use.

---

### Edge Cases

- **A person leaves while a script uses their token.** The script's next call is refused with an
  authentication error. The team gives the script a token of another account.
- **Two administrators act on the same account at the same time.** One disables it while the
  other deletes it. The account ends deleted, and neither action returns an unexpected error.
- **An administrator removes their own administrator level, or disables or deletes their own
  account.** The action is allowed, because the `admin` account always exists and can restore
  access.
- **An account is created with the name `admin`, or with the name of an existing account.** The
  Sync API refuses. Names are compared without regard to letter case.
- **An account is created with a name the product reserves.** The Sync API refuses `admin`,
  `unknown`, `unauthenticated` and a name that contains a space, so no name can be equal to the
  placeholder of a deleted account.
- **A request of an account is still in progress when the account is deleted.** What the request
  writes refers to the deleted account and displays the placeholder. It gives no right to a new
  account with the same name.
- **A token expires while a script uses it.** The next call is refused with an authentication
  error whose message says the token has expired.
- **A person signs in many times with a wrong password.** Further attempts for that name are
  refused for 15 minutes, with the same message as a wrong password. The account is not
  disabled, so the person signs in normally afterwards.
- **A stranger fails on purpose to keep a person out.** An attempt made while the name is refused
  does not extend the refusal. An administrator can end the refusal, a restart of `sync-api` ends
  it for `admin`, and tokens keep working. The stranger can still cause a new refusal in each
  15-minute window (see "Assumptions").
- **Many people sign in at the same moment.** The Sync API checks a limited number of passwords
  at a time. A sign-in that waits too long answers "service unavailable", and the person tries
  again.
- **A person pastes a token into a reason.** The Sync API refuses the request, as it refuses a
  reason that contains a secret today.
- **The `admin` password in the environment changes between two starts.** After the restart, the
  new password opens the account and the old one does not. Sessions of `admin` that were open
  before the restart end.
- **The `admin` token in the environment is removed between two starts.** After the restart, the
  old token is refused.
- **Text a person typed.** A reason that names an account keeps that name after the account is
  deleted. Sync does not rewrite reasons or log lines. The documentation says so.
- **Records made before this feature.** They hold the name of a principal of the removed
  variable. They display that name, and no account has rights over them through that name.
- **The Sync API cannot reach its database.** A sign-in fails with the existing "service
  unavailable" answer, not with a refusal of the password.

## Requirements *(mandatory)*

### Functional Requirements

#### Accounts and sign-in

- **FR-001**: The system MUST let a person sign in with an account name and a password that Sync
  stores.
- **FR-002**: The system MUST store a password only in a form from which the password cannot be
  recovered.
- **FR-003**: The system MUST refuse a sign-in for a wrong name, a wrong password or a disabled
  account with one message that does not reveal which of the three it was.
- **FR-004**: The system MUST NOT depend on an Infrahub instance to sign a person in.
- **FR-005**: The system MUST keep the way it identifies the caller of a request replaceable, so
  that another sign-in method can be added later without changing the routes of the Sync API.

#### The `admin` account

- **FR-006**: The system MUST define one account named `admin`, with the administrator level,
  from the deployment's environment: a password, which is required and has no default value, and
  one token, which is optional.
- **FR-007**: The Sync API MUST apply the password and the token of `admin` from the environment
  at each start, and MUST refuse to start, naming the variable, when the password is not set or
  is shorter than the minimum of FR-020.
- **FR-008**: The system MUST refuse every request that changes the password of `admin`, creates
  or deletes a token of `admin`, changes its level, disables it or deletes it, and the refusal
  MUST name the environment variable to edit.
- **FR-009**: The system MUST NOT read `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`. The shipped
  deployment files, the development stack and the documentation MUST no longer name it.

#### Managing accounts

- **FR-010**: An administrator MUST be able to create an account with a name, an initial password
  and a level, list accounts, set the level of an account, disable an account, enable an account
  and delete an account.
- **FR-011**: An account that is not an administrator MUST NOT be able to perform any action of
  FR-010, and MUST NOT be able to change its own level.
- **FR-012**: An account name MUST be unique without regard to letter case among the accounts
  that are not deleted, MUST NOT contain a space, and MUST NOT be `admin`, `unknown` or
  `unauthenticated`.
- **FR-013**: Each account MUST be able to change its own password by giving its current
  password. An administrator MUST be able to set a new password for another account.

#### API tokens

- **FR-014**: Each account MUST be able to create a token with a name and an optional expiry
  date, list its own tokens and delete one of its own tokens. An administrator MUST be able to
  list and delete the tokens of another account.
- **FR-015**: The system MUST accept a token of an account wherever it accepts a token today: in
  the `Authorization` header of the Sync API, in `INFRAHUB_SYNC_API_TOKEN` for the CLI, and in the
  token field of the web interface until User Story 2 replaces that field.
- **FR-016**: The system MUST display the value of a token once, in the answer to its creation,
  and MUST store it only in a form from which the value cannot be recovered.
- **FR-017**: A list of tokens MUST show, for each token, its name, its creation date, its expiry
  date when it has one, and the date of its last use.
- **FR-018**: A token MUST give the rights of its account, no more and no fewer.
- **FR-019**: The system MUST refuse a token after its expiry date and after its deletion.
  Disabling an account deletes its tokens (FR-029).

#### Sign-in rules

- **FR-020**: The system MUST refuse a password shorter than 12 characters. It MUST NOT require
  particular kinds of characters, and MUST NOT force a periodic change.
- **FR-021**: After 5 failed sign-in attempts for one account name within 15 minutes, the system
  MUST refuse further sign-in attempts for that name for 15 minutes, with the message of FR-003,
  and MUST NOT disable the account.
    - A wrong current password in a password change counts as a failed attempt.
    - An attempt made during the refusal MUST NOT extend it.
    - Requests with a token are not affected.
    - An administrator MUST be able to end the refusal for another account, and a restart of the
      Sync API MUST end it for `admin`.
- **FR-021a**: The system MUST limit the number of passwords it checks at the same time, and
  MUST answer "service unavailable" to a sign-in that waits too long, so that sign-in requests
  cannot exhaust the memory of the Sync API.
- **FR-022**: A session that a person opened with a password MUST end after a period without
  activity. The period is a setting of the deployment, with a default of 8 hours.
- **FR-023**: A person MUST be able to end their session (sign out) from the CLI and from the web
  interface.

#### Records

- **FR-024**: The system MUST record each run, approval and audit event with a reference to the
  account that acted, including when the request carried a token. The API field `actor` keeps
  its name and displays the account name.
- **FR-024a**: The system MUST refuse a request whose free text (a reason) contains an API token
  or a session token, and MUST limit the length of a reason.
- **FR-025**: The system MUST write an audit event for each account action: create, set level,
  disable, enable, delete, change of password, creation and deletion of a token. The event names
  the account that acted and the account acted on, and never contains a password or a token
  value.
- **FR-026**: The existing rights MUST stay as they are, applied to accounts:
    - any account views configurations, runs, plans and results, and creates runs;
    - an account applies, approves, cancels or resumes only a run it started;
    - only an administrator registers a configuration or a version, or acts on a run that another
      account started.

#### Disabling and deleting

- **FR-027**: After an account is disabled or deleted, the system MUST refuse the next request
  made with its password, with a session it opened, or with any of its tokens.
- **FR-028**: Disabling or deleting an account MUST NOT stop a run in progress, and MUST NOT
  change the validity of an approval or of a saved plan.
- **FR-029**: A disabled account MUST keep its name in every record. Enabling it MUST restore
  sign-in with its password and MUST NOT restore its earlier tokens.
- **FR-030**: Deleting an account MUST remove its name, its password and its tokens from the
  account data that Sync stores, and MUST NOT be reversible. Text that a person typed, such as a
  reason, and log lines are not changed.
- **FR-031**: After a deletion, every run, approval, audit event and configuration registration
  of the deleted account MUST display "Deleted account" followed by one short identifier. The
  identifier MUST be the same on every record of that account and different from the identifier
  of any other deleted account.
- **FR-032**: A new account that takes the name of a deleted account MUST have no right over the
  runs of the deleted account, and no record of the deleted account MUST display the new account.
  This MUST hold for a record that a request in progress writes after the deletion.
- **FR-032a**: A deletion MUST NOT change a past run, approval or audit event. Only the way the
  account is displayed changes.
- **FR-033**: No action MUST delete the runs, approvals or audit events of an account.

#### The web interface

- **FR-034**: (User Story 2) The web interface MUST ask for an account name and a password, MUST
  display the signed-in account on every page, and MUST offer a sign-out action.
- **FR-035**: (User Story 2) The web interface MUST display the account that started a run, the
  account that approved a plan and the account that registered a configuration version, or
  "Deleted account" with its identifier.
- **FR-036**: (User Story 2) The rule "Never name a user in the interface" in `AGENTS.md` MUST be
  replaced with a rule that states where the web interface names an account.
- **FR-037**: (User Story 4) The web interface MUST offer the account actions of FR-010, FR-013
  and FR-014 to the accounts that may perform them, and MUST NOT offer them to the others.

#### The CLI and the documentation

- **FR-038**: The CLI MUST offer commands to sign in, sign out, manage accounts, change a
  password and manage tokens. A command that takes a password MUST read it from a prompt or from
  standard input, and MUST NOT accept it as a command argument.
- **FR-040**: The constitution's principle VI MUST gain one sentence that allows Sync to display
  a token value once, to the account that created it.
- **FR-039**: The documentation of the Compose deployment and of the development stack MUST
  describe the `admin` variables, the first sign-in, how to add a person, how to give a script a
  token, the difference between disabling and deleting, and how to recover the `admin` account.

### Key Entities

- **Account**: a person or a service that signs in to Sync. It has a unique name, a password, a
  level (administrator or not) and a state (active or disabled). Records refer to the account,
  not to its name. A deleted account keeps no name and no password, only what past records need
  to refer to it and a short identifier for display.
- **The `admin` account**: the one account whose password and optional token come from the
  deployment's environment. It always exists and is always an active administrator.
- **API token**: a secret that belongs to one account and gives its rights. It has a name, a
  creation date, an optional expiry date and a date of last use.
- **Session**: the result of a sign-in with a password, in the CLI or the web interface. It ends
  at sign-out, after a period without activity, or when its account is disabled or deleted.
- **Run, approval, audit event, configuration version**: existing records. Each one refers to
  the account that acted.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An administrator gives a new person access, and that person completes a first
  command, in under 2 minutes, with no edit to a deployment file and no restart of a service.
- **SC-002**: After an administrator disables or deletes an account, zero requests made with its
  password, its sessions or its tokens are accepted.
- **SC-003**: 100% of the run, approval and audit records created after the release display the
  account that acted, or "Deleted account" with its identifier.
- **SC-004**: A deployment holds zero sign-in secrets in its deployment files other than the
  password and the optional token of `admin`.
- **SC-005**: A search of every API response, CLI output, log line, run artifact and audit record
  that the test suite produces finds zero passwords, and zero token values outside the answer to
  a token's creation.
- **SC-006**: A script that worked before the release works after it with one change only: the
  value of `INFRAHUB_SYNC_API_TOKEN`.
- **SC-007**: After a deletion, a search of everything Sync stores finds zero occurrences of the
  deleted account's name, outside text that a person typed.

## Assumptions

- **No production deployment of V3 exists.** This is why `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS` is
  removed with no transition period. The source is the Decision Register of the V3 solution
  brief, last edited on 2026-09-16. If a production deployment exists at release time, the
  removal needs a transition period, which this specification does not describe.
- **Access to the host or the cluster equals administrator access to Sync.** A person who can
  read or edit the deployment's environment can sign in as `admin`. This is already true today.
- **The colleague who builds the web interface agrees to User Stories 2 and 4.** The agreement
  was not given at the time of writing. User Stories 1 and 3 do not depend on it.
- **The timing against other work is not known**: the recovery work, and the Helm chart of spec
  018, which also defines the deployment's variables and must name the `admin` variables instead
  of `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`.
- **The values 5 attempts, 15 minutes and 8 hours** in FR-021 and FR-022 are defaults that the
  agent chose. The interview fixed the rules, not the values.
- **A person who knows an account name can delay that account's sign-in with a password.** By
  failing 5 times in each 15-minute window, they keep the name refused. FR-021 limits the effect
  and gives ways to end a refusal, but does not remove it. The alternative, a delay that grows
  with each failure and never refuses outright, was not chosen in the interview. The maintainer
  confirmed the 15-minute refusal on 2026-11-18.
- **Records made before this feature keep the name of their principal.** Because no production
  deployment exists, only development databases hold such records.
- **A person receives their initial password from the administrator outside Sync.** Sync sends no
  email and no invitation.

## Out of Scope

- Credentials for source and destination systems that Sync stores. This is the second change of
  the idea brief and gets its own specification.
- Sign-in through an external identity provider (SSO), and through the Infrahub host.
- Roles, groups, read-only accounts and read-only tokens.
- A second sign-in factor, rules on password reuse, and checks against lists of leaked passwords.
- A delete action that also removes the records of an account.
- A recovery command. Recovery uses the `admin` variables.
- A forced change of the initial password at the first sign-in. A person can change it at any
  time.
- A change to the Infrahub account that a run writes as (the import account of ADR 0031).
