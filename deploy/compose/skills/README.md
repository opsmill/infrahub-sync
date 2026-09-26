# Agent skills

This directory contains optional agent skills for operating Infrahub Sync. The
human procedures remain authoritative; the skills help an agent choose the
right procedure, gather the missing facts, and stop before an operator-only
action.

Whether these skills are included depends on your candidate. Check your
candidate's release notes for whether they are included; if they are, they
ship inside the Compose bundle at this directory.

## Available skills

- [Infrahub Sync deployment](./infrahub-sync-deployment/SKILL.md) routes Compose
  deployment checks, refusals, logs, and saved-plan review.
- [Infrahub Sync configuration](./infrahub-sync-configuration/SKILL.md) helps
  draft declared configuration packages without registering them or handling
  credential values.

The complete operator procedure is [OPERATING.md](../OPERATING.md).

## Install

Find the skill directory supported by your agent. Copy the whole directory for
each skill you want; do not copy only `SKILL.md` or combine the two directories.
For an agent whose documented skill directory is in `AGENT_SKILLS_DIR`, run
the entire block as one unit from the extracted Compose bundle:

```sh
(
  set -eu
  : "${AGENT_SKILLS_DIR:?set AGENT_SKILLS_DIR to the existing agent skill directory}"
  test -d "$AGENT_SKILLS_DIR"
  deployment="$AGENT_SKILLS_DIR/infrahub-sync-deployment"
  configuration="$AGENT_SKILLS_DIR/infrahub-sync-configuration"
  deployment_created=
  configuration_created=

  cleanup() {
    exit_status=$?
    trap - 0 HUP INT TERM
    test -z "$configuration_created" || rm -rf "$configuration"
    test -z "$deployment_created" || rm -rf "$deployment"
    exit "$exit_status"
  }
  trap cleanup 0
  trap 'exit 1' HUP INT TERM

  mkdir "$deployment"
  deployment_created=1
  mkdir "$configuration"
  configuration_created=1

  cp -R skills/infrahub-sync-deployment/. "$deployment/"
  cp -R skills/infrahub-sync-configuration/. "$configuration/"

  trap - 0 HUP INT TERM
)
```

The recipe claims both destination directories before it copies any files. If
either destination already exists or another installer claims it first, the
recipe preserves that directory and removes only a directory it created. A copy
failure also removes both directories claimed by that invocation. Follow your
agent's documented update procedure instead of overwriting an installed skill.
Start a new agent session if that agent discovers skills only at session start.

Installing a skill grants no permission to change a deployment, registry,
source, or destination. The operator still issues every state-changing command.
