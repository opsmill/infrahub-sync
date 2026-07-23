---
description: Validate Jira/JPD ticket reference and create a feature branch
---


<!-- Extension: jpd -->
<!-- Config: .specify/extensions/jpd/ -->
# Create OpsMill Feature Branch

Create and switch to a new git feature branch for the given specification, enforcing an OpsMill Jira or JPD ticket reference as the branch name suffix.

## User Input

```text
$ARGUMENTS
```

## Prerequisites

- Verify Git is available by running `git rev-parse --is-inside-work-tree 2>/dev/null`
- If Git is not available, warn the user and skip branch creation

## Step 0: Check Current Branch

Run `git rev-parse --abbrev-ref HEAD`. If the current branch already matches
`-(infp|ifc)-[0-9]+$` AND either no ticket ID is present in `$ARGUMENTS`, or
the branch's ticket suffix equals the normalized ticket ID from `$ARGUMENTS`,
output:

> "✓ Reusing feature branch: <branch>"

and stop — do not call the script. Only fall through to Step 1 and branch
creation otherwise.

## Step 1: Resolve Ticket Reference

Parse `$ARGUMENTS` for a ticket ID matching either of these formats, **case-insensitively** — tickets are usually written uppercase (`INFP-646`), but any case is valid input:

- JPD format: `\b(?:infp)-[0-9]+\b` (e.g., `INFP-646`, `infp-646`)
- Jira epic format: `\b(?:ifc)-[0-9]+\b` (e.g., `IFC-2140`, `ifc-2140`)

Both patterns are word-bounded: a ticket embedded in a longer token (e.g.
`notinfp-646x`) must be **rejected**, not extracted.

Normalize the matched ticket ID to **lowercase** before using it anywhere: the branch suffix is always lowercase (input `INFP-646` -> suffix `infp-646`), matching git branch conventions and the patterns `speckit.git.validate` accepts.

If no ticket ID is found in the arguments, prompt the user:

> "Please provide a Jira or JPD reference for this feature (e.g., `infp-646` for a JPD item or `ifc-2140` for a Jira epic):"

Do not proceed until a valid ticket ID is provided. Never invent or skip it.

## Step 2: Generate Short Name

Generate a concise short name (2-4 words) from the feature description:

- Use action-noun format when possible (e.g., `user-auth`, `fix-payment-timeout`)
- Preserve technical terms and acronyms (OAuth2, API, JWT, etc.)
- Examples:
  - "Add user authentication" → `user-auth`
  - "Implement OAuth2 integration for the API" → `oauth2-api-integration`
  - "Fix payment processing timeout bug" → `fix-payment-timeout`

## Step 3: Create Branch

Construct the branch name as `<short-name>-<ticket-id>` — all lowercase, using the normalized ticket ID (e.g., `embeddable-python-library-infp-646`), then pass it as `GIT_BRANCH_NAME` to bypass the script's automatic numbering:

```bash
GIT_BRANCH_NAME="<short-name>-<ticket-id>" .specify/extensions/git/scripts/bash/create-new-feature.sh --json "<feature description>"
```

Example:

```bash
GIT_BRANCH_NAME="embeddable-python-library-infp-646" .specify/extensions/git/scripts/bash/create-new-feature.sh --json "Create embeddable Python library"
```

**IMPORTANT**:

- Always construct `GIT_BRANCH_NAME` as `<short-name>-<ticket-id>` — ticket ID is the suffix
- Always include `--json` so output can be parsed reliably
- Run this script at most once per feature; skip when Step 0 reuses the branch
- For single quotes in args, use escape syntax: `'I'\''m Groot'`

## Output

The script outputs JSON with:

- `BRANCH_NAME`: The branch name (e.g., `embeddable-python-library-infp-646`)
- `FEATURE_NUM`: For non-numeric branch names like these, the script echoes the full branch name here, not the bare ticket ID. SpecKit 0.11.9 does not derive the spec directory from this value; treat `BRANCH_NAME` as the identifier.