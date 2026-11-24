# Web interface contract: Credentials page

- **Navigation**: "Credentials" for every signed-in account.
- **List**: name, description, fields set, revision, updated by, updated at. No value.
- **Administrator actions**: "New credential", "Change value", "Edit description", "Delete". Hidden
  for other accounts, which view the note "Ask an administrator to create or change credentials."
- **Forms**: the token and the password are password inputs. The form is cleared after each request,
  success or refusal. The field choice is one of "Token", "Username and password", "Username and
  token".
- **Delete**: asks for confirmation. On `credential-in-use`, lists the configurations and schedules
  that name the credential, with links to them.
- **Run details**: a "Credentials used" section lists stage, block, name and revision.
- **Drop area** (depends on spec 025): for each `credential-not-found` finding, an administrator views
  "Create credential `<name>`", which opens the form with the name filled in; another account views
  "Ask an administrator to create the credential `<name>`."
- Account names follow the `AGENTS.md` rule for the Sync frontend.
