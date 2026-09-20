# Agent skills

This directory contains optional agent skills for operating Infrahub Sync. The
human procedures remain authoritative; the skills help an agent choose the
right procedure, gather the missing facts, and stop before an operator-only
action.

These skills are unreleased source content. They are not in Candidate 4
(`3.0.0a4`). They ship only in a later Compose bundle built from a revision that
contains these files.

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
  test ! -e "$AGENT_SKILLS_DIR/infrahub-sync-deployment"
  test ! -e "$AGENT_SKILLS_DIR/infrahub-sync-configuration"
  cp -R skills/infrahub-sync-deployment skills/infrahub-sync-configuration "$AGENT_SKILLS_DIR/"
)
```

The checks deliberately stop if either target already exists. Follow your
agent's documented update procedure instead of overwriting an installed skill.
Start a new agent session if that agent discovers skills only at session start.

Installing a skill grants no permission to change a deployment, registry,
source, or destination. The operator still issues every state-changing command.
