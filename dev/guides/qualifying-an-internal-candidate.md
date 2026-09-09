# Qualifying an internal candidate

Internal. This page tells a teammate how to obtain a pre-release Infrahub Sync
candidate from a GitHub Actions run and qualify it on their own host. It is not
on the documentation site, and the artifacts it names are not advertised: they
are unpublished pre-release bytes, and there is no registry, no package index,
and no tagged release behind them yet.

Written for someone who did not build this.

## What the host needs

| Needs | Why |
| --- | --- |
| Linux on `amd64` | The qualified platform. `arm64` is built and smoked under emulation; that is not a qualification. |
| Docker, and Docker Compose 2.17.3 or later | The deployment. The entry point refuses an older Compose. |
| `gh`, authenticated against `opsmill/infrahub-sync` | The artifacts are unpublished, so the Actions API is the only way to them. |
| `jq` | Every check below reads JSON the run produced. |
| `sha256sum`, `awk`, `date` | Coreutils. `date -u -d` is used to read expiry windows. |
| An Infrahub you are **authorised to write to**, and can throw away | Step 8 applies a real write. |

It does **not** need Python, `uv`, or a checkout of this repository. Nothing
below installs an interpreter; the Sync CLI runs from the candidate image.

```bash
REPO=opsmill/infrahub-sync
```

Two things are being tested at once. One is the candidate. The other is this
page: you are the first person to follow it, so record where it was wrong.
[What to record](#what-to-record) is the last section.

## 1. Choose a run, and separate the two commits it refers to

A candidate is built by a manual dispatch of `workflow-candidate.yml` against an
exact merged commit. A pull-request run is not a candidate: GitHub tests the
merge commit while the run builds the pull request's head, so its artifacts
describe bytes nobody will ship. A pull-request run also deletes what it built
before it finishes.

**A dispatched run refers to two different commits, and they are not
interchangeable.**

| | What it is | Where to read it |
| --- | --- | --- |
| Workflow revision | The tip of the ref the run was started against. It decides which version of the workflow definition ran. It is `head_sha`. | `gh run view --json headSha` |
| Candidate commit | The commit the run was told to build, and the only one the artifacts describe. It is the `sha` input. | the run's own title |

They are equal only while the branch has not moved since the merge, and the
route is built for the case where it has: a window that lapses is answered by
rebuilding the *same commit* from a much later tip. So `head_sha` is provenance
for the workflow, and the candidate commit is the build identity. Never use one
where the other is meant.

List the candidate runs:

```bash
gh run list --repo "$REPO" --workflow workflow-candidate.yml \
  --json databaseId,displayTitle,headSha,status,conclusion,createdAt \
  --jq '.[] | [.databaseId, .conclusion, .displayTitle, .headSha] | @tsv'
```

Pick a successful one and read both commits out of it:

```bash
RUN=<the run ID>

CANDIDATE_SHA=$(gh run view "$RUN" --repo "$REPO" --json displayTitle \
  --jq '.displayTitle | sub("^Candidate ";"")')
WORKFLOW_REVISION=$(gh run view "$RUN" --repo "$REPO" --json headSha --jq '.headSha')

printf 'candidate commit:  %s\nworkflow revision: %s\n' "$CANDIDATE_SHA" "$WORKFLOW_REVISION"
```

`CANDIDATE_SHA` must be an exact 40-character commit. Check it rather than
assume it — everything below compares against it:

```bash
printf '%s' "$CANDIDATE_SHA" | grep -Eq '^[0-9a-f]{40}$' \
  && echo "OK: an exact commit" \
  || echo "STOP: the run title did not yield one"
```

If the two commits differ, that is normal and not a problem. If they are equal,
that is also fine. Neither tells you anything is wrong.

Now confirm the run really qualified something:

```bash
gh run view "$RUN" --repo "$REPO" --json conclusion,jobs \
  --jq '{conclusion, jobs: [.jobs[] | {name, conclusion}]}'
```

The run must be `success` and **both** jobs must have succeeded. The `candidate`
job builds and retains the bytes; the `clean-host` job is what qualified them on
a host with no checkout. A run whose `clean-host` job failed, was skipped, or is
still going has uploads but no qualification — retained artifacts alone are not
a result.

## 2. Check what the service is holding, before downloading any of it

The run retains seven artifact groups. Read the service's own inventory first:
it is the only place the identifiers and the granted expiry exist, and the
qualification record cannot contain its own.

```bash
gh api --paginate "repos/$REPO/actions/runs/$RUN/artifacts" \
  --jq '.artifacts[] | [.name, (.id|tostring), .digest, .created_at, .expires_at, (.expired|tostring)] | @tsv' \
  | sort > inventory.tsv
cat inventory.tsv
```

Check that all seven are present, none has expired, and each was granted exactly
30 days. Asking for a window is not being given one, so this reads what the
service actually returned:

```bash
for name in \
  infrahub-sync-candidate-image \
  infrahub-sync-candidate-identity \
  infrahub-sync-candidate-distributions \
  infrahub-sync-candidate-bundle \
  infrahub-sync-candidate-sboms \
  infrahub-sync-qualification-kit \
  infrahub-sync-qualification-record
do
  entry=$(awk -F'\t' -v n="$name" '$1 == n {print; exit}' inventory.tsv)
  if [ -z "$entry" ]; then
    printf 'MISSING  %s\n' "$name"
    continue
  fi
  created=$(printf '%s' "$entry" | cut -f4)
  expires=$(printf '%s' "$entry" | cut -f5)
  expired=$(printf '%s' "$entry" | cut -f6)
  granted=$(( ( $(date -u -d "$expires" +%s) - $(date -u -d "$created" +%s) ) / 86400 ))
  if [ "$granted" -eq 30 ] && [ "$expired" = "false" ]; then
    printf 'OK       %-42s granted %sd, expires %s\n' "$name" "$granted" "$expires"
  else
    printf 'WRONG    %-42s granted %sd, expired=%s\n' "$name" "$granted" "$expired"
  fi
done
```

Anything other than seven `OK` lines means this run is not an acceptable
candidate. Record what you saw and stop.

## 3. Download what the host needs

Four of the seven are what a host runs:

```bash
mkdir -p candidate && cd candidate

gh run download "$RUN" --repo "$REPO" --name infrahub-sync-candidate-image      --dir image
gh run download "$RUN" --repo "$REPO" --name infrahub-sync-candidate-bundle     --dir bundle
gh run download "$RUN" --repo "$REPO" --name infrahub-sync-qualification-record --dir record
gh run download "$RUN" --repo "$REPO" --name infrahub-sync-candidate-identity   --dir identity
```

The other three — `infrahub-sync-candidate-distributions`,
`infrahub-sync-candidate-sboms`, and `infrahub-sync-qualification-kit` — are the
wheel and source distribution, the bills of materials and scan reports, and the
gate's own driver. You do not need them to run the candidate, but they are part
of the inventory you just checked.

`infrahub-sync-candidate-image` keeps the build's directory layout, so the
archive you want is `image/archives/image-linux-amd64.tar`.

## 4. Bind what arrived to the commit you chose

`identity.json` names the revision the artifacts were built from. It must equal
the **candidate commit**, not the workflow revision:

```bash
BUILT_FROM=$(jq -r '.revision' identity/identity.json)
jq -r '"version " + .version + ", bundle " + .bundle' identity/identity.json

test "$BUILT_FROM" = "$CANDIDATE_SHA" \
  && echo "OK: these bytes were built from the candidate commit" \
  || printf 'STOP: built from %s, not the candidate %s\n' "$BUILT_FROM" "$CANDIDATE_SHA"
```

If that fails, stop. You are holding bytes from another commit.

### Three digests, and they are not the same thing

Confusing them is the easiest way to believe a check passed that did not.

| Digest | Names | Read from | Checked with |
| --- | --- | --- | --- |
| Service transport digest | the artifact as the Actions service stores it | `inventory.tsv`, and `.artifacts[].digest` in the record | the comparison below |
| Bundle file digest | the bundle archive's own bytes | `.bundle.sha256` in the record | `sha256sum` |
| Image configuration digest | the loaded image's configuration | `.image.platforms["linux/amd64"].config` in the record | `docker image inspect` |

Compare the six the record stores against what the service holds. The record's
digests may or may not carry a `sha256:` prefix depending on which side wrote
them, so both are normalised before comparison:

```bash
jq -r '.artifacts | to_entries[]
       | [.key, (.value.id|tostring), (.value.digest|sub("^sha256:";""))] | @tsv' \
  record/qualification.json | sort > recorded.tsv

while IFS=$'\t' read -r name id digest; do
  entry=$(awk -F'\t' -v n="$name" '$1 == n {print; exit}' inventory.tsv)
  held_id=$(printf '%s' "$entry" | cut -f2)
  held_digest=$(printf '%s' "$entry" | cut -f3 | sed 's/^sha256://')
  if [ "$id" = "$held_id" ] && [ "$digest" = "$held_digest" ]; then
    printf 'OK     %-42s id %s\n' "$name" "$id"
  else
    printf 'WRONG  %-42s record %s/%s, service %s/%s\n' "$name" "$id" "$digest" "$held_id" "$held_digest"
  fi
done < recorded.tsv
```

That covers six groups. The seventh — the qualification record itself — cannot
appear in its own `artifacts` map, because a document cannot carry the digest of
the upload that contains it. Take its identifiers from the service and record
them by hand:

```bash
awk -F'\t' '$1 == "infrahub-sync-qualification-record" {printf "record artifact id %s digest %s\n", $2, $3}' \
  inventory.tsv
```

### Then the bundle's own bytes

Two checks, and they fail for different reasons: a corrupted transfer breaks the
first, a bundle that is not the one the record describes breaks the second.

```bash
( cd bundle && sha256sum -c ./*.tar.gz.sha256 )

BUNDLE_NAME=$(jq -r '.bundle.name' record/qualification.json)
RECORDED_BUNDLE=$(jq -r '.bundle.sha256' record/qualification.json)
ACTUAL_BUNDLE=$(sha256sum "bundle/$BUNDLE_NAME" | cut -d' ' -f1)

test "$RECORDED_BUNDLE" = "$ACTUAL_BUNDLE" \
  && echo "OK: this is the bundle the record names" \
  || printf 'STOP: record %s, file %s\n' "$RECORDED_BUNDLE" "$ACTUAL_BUNDLE"
```

Do not extract the archive until both pass.

## 5. Load the image and bind it to the record

Read the configuration digest the record names, load the archive, and confirm
Docker holds that exact configuration. Export it now: it is what the deployment
and the CLI are both given, and a tag would not be accepted anyway.

```bash
INFRAHUB_SYNC_IMAGE=$(jq -r '.image.platforms["linux/amd64"].config' record/qualification.json)
export INFRAHUB_SYNC_IMAGE

LOADED=$(docker load -i image/archives/image-linux-amd64.tar | sed -n 's/^Loaded image[^:]*: //p')
printf 'loaded %s\n' "$LOADED"

test "$(docker image inspect --format '{{.Id}}' "$LOADED")" = "$INFRAHUB_SYNC_IMAGE" \
  && echo "OK: the loaded image is the qualified configuration" \
  || echo "STOP: the loaded image is not the one the record names"
```

From here on, `$INFRAHUB_SYNC_IMAGE` is the verified configuration digest and
nothing else. A tag can be re-pointed between the qualification that trusted an
image and the run that uses it, which is why the bundle refuses one.

## 6. Extract the bundle and prepare a deployment

```bash
tar -xzf "bundle/$BUNDLE_NAME"
cd "${BUNDLE_NAME%.tar.gz}"
./infrahub-sync-compose init
```

`init` writes `.instance`, `secrets/postgres-admin-password`, and `operator.env`
with generated passwords. Two values are yours to supply. Set the image to the
digest you just verified:

```bash
sed -i "s|^INFRAHUB_SYNC_IMAGE=.*|INFRAHUB_SYNC_IMAGE=${INFRAHUB_SYNC_IMAGE}|" operator.env
sed -i "s|^INFRAHUB_API_TOKEN=.*|INFRAHUB_API_TOKEN=<your Infrahub token>|" operator.env
grep -E '^(INFRAHUB_SYNC_IMAGE|INFRAHUB_API_TOKEN)=' operator.env
```

Use a **disposable** Infrahub you are **authorised to write to**. Step 8 applies
a real write. Do not point this at anything you cannot afford to have changed.

Then point the declared configuration at that Infrahub. Edit
`configuration/qualification.yaml` so its `url` names your instance, and leave
the credential as a reference — a configuration package holds credential
*references*, never values:

```yaml
token:
  $credential: infrahub-token
credentials:
  infrahub-token:
    provider: env
    identifier: INFRAHUB_API_TOKEN
```

Edit it before the first start. Bootstrap recognises a configuration by its
declared name and checksums its content, so different content under a name it
has already registered is refused.

The extracted bundle carries `OPERATING.md`, the same procedure written for
whoever runs the deployment. Read it when this page runs out.

## 7. Preflight, start, and reach READY

```bash
./infrahub-sync-compose preflight
```

`preflight` refuses before anything is created, and each refusal is one family
name and a fixed sentence. `OPERATING.md` has the whole table. The ones you are
most likely to meet here: `image-not-immutable` (a tag rather than a digest),
`image-unresolvable` (the digest is not loaded), `credentials-missing` (a value
in `operator.env` is still empty or `REPLACE-ME`), `destination-unavailable`
(your Infrahub URL did not answer), and `port-occupied` (something already holds
`127.0.0.1:8000` or `:4200`).

When preflight passes:

```bash
./infrahub-sync-compose start
./infrahub-sync-compose status
```

`status` must print `READY` and exit 0. `READY` is endpoint-backed: dependencies
answer, the API answers, and a registered worker is heartbeating. `DEGRADED`
(exit 3) means something owned exists but not all of that is true; `STOPPED`
(exit 4) means no container of this instance is running. Container health alone
is not readiness — a hung worker looks healthy to Docker and still reaches
`DEGRADED`.

If `start` returns `not-ready`, the deployment came up and no worker registered
in time. Take the logs before anything else:

```bash
./infrahub-sync-compose logs sync-worker
./infrahub-sync-compose logs sync-api
```

## 8. Plan, review the saved plan, then apply that exact plan

The API principal `init` generated lives in `operator.env` as a JSON document.
Read the token out of it without sourcing the file — it holds every other
credential too:

```bash
export INFRAHUB_SYNC_API_URL=http://127.0.0.1:8000
INFRAHUB_SYNC_API_TOKEN=$(grep '^INFRAHUB_SYNC_SERVICE_BEARER_TOKENS=' operator.env \
  | cut -d= -f2- | jq -r '.operator.token')
export INFRAHUB_SYNC_API_TOKEN

curl -sS "$INFRAHUB_SYNC_API_URL/status" | jq '.worker.state'
curl -sS -H "Authorization: Bearer $INFRAHUB_SYNC_API_TOKEN" \
  "$INFRAHUB_SYNC_API_URL/configs" | jq '.'
```

The CLI ships in the candidate image, so you never install it. It runs under the
same verified digest you exported in step 5:

```bash
sync() {
  docker run --rm --network host \
    --env INFRAHUB_SYNC_API_URL --env INFRAHUB_SYNC_API_TOKEN \
    "$INFRAHUB_SYNC_IMAGE" infrahub-sync "$@"
}

sync configs list
```

Note the configuration's identity and version — the next step needs both.

Qualification is one write, taken the managed way: plan, read the saved plan,
then apply the plan you read by its checksum. **Do not use `sync sync` here.** It
is a real capability, and it is a single confirmed write with no reviewed plan
in between, so it proves nothing about the admission path this deployment
exists to enforce.

Plan first. It writes nothing:

```bash
sync diff --config-id <config> --version <version> --reason "candidate qualification"
```

That prints a run ID. Read the saved plan and its checksum:

```bash
sync runs plan <run-id>
sync runs plan <run-id> --detail
```

Read what it proposes before going on. Then apply that exact plan:

```bash
sync apply <run-id> \
  --expected-checksum <the checksum runs plan printed> \
  --reason "candidate qualification"
```

A checksum that no longer matches the saved plan is refused, and that is the
property being qualified: it means the plan you read is not the plan that would
be applied, and the remedy is a new plan rather than a retry.

Then observe. Confirm at the destination that the change you approved is the
change that happened, and that nothing else did.

## 9. Retrieve the run's evidence

A run's artifacts are held by the deployment, not on a container filesystem:

```bash
RUN_ID=<the run you applied>
AUTH="Authorization: Bearer $INFRAHUB_SYNC_API_TOKEN"

curl -sS -H "$AUTH" "$INFRAHUB_SYNC_API_URL/runs/$RUN_ID" | jq '.'
curl -sS -H "$AUTH" "$INFRAHUB_SYNC_API_URL/runs/$RUN_ID/results" | jq '.'
curl -sS -H "$AUTH" "$INFRAHUB_SYNC_API_URL/runs/$RUN_ID/artifacts" | jq '.'
```

Each entry has an `artifact_id`, a `digest`, and a `size`. Fetch one and check
what arrived against the digest the list gave you — this is a fourth, separate
digest, over the deployment's own artifact, and has nothing to do with the three
in step 4:

```bash
ARTIFACT=<an artifact_id from the list>
EXPECTED=$(curl -sS -H "$AUTH" "$INFRAHUB_SYNC_API_URL/runs/$RUN_ID/artifacts" \
  | jq -r --arg a "$ARTIFACT" '.artifacts[] | select(.artifact_id == $a) | .digest')

curl -sS -D headers.txt -H "$AUTH" \
  "$INFRAHUB_SYNC_API_URL/runs/$RUN_ID/artifacts/$ARTIFACT" -o artifact.bin
grep -i '^digest:' headers.txt
printf 'expected %s\nactual   %s\n' "$EXPECTED" "$(sha256sum artifact.bin | cut -d' ' -f1)"
```

## 10. Restart, and confirm it converges

Replacing the processes must lose nothing, because no run state lives on a
container filesystem:

```bash
./infrahub-sync-compose restart
./infrahub-sync-compose status
```

`status` must return to `READY`. The worker rejoins under a new Prefect
identity; the run you applied in step 8, its plan, and its artifacts must all
still be readable through the API afterwards. Check that they are — that is the
claim being tested, not the exit code.

Starting an already-started deployment is also safe. Bootstrap converges the two
databases and their owners, the bucket, the work pool, the installed deployment
and the declared configuration, and creates none of them twice:

```bash
./infrahub-sync-compose start
```

## 11. When the outcome of a write is uncertain

If a write ends without proving what reached the destination, the deployment
does not retry it. Repeating a write whose outcome is unknown is the one thing
that could turn an uncertain state into a wrong one, so the run is left terminal.

Read the run itself. `reconciliation_required` is a field of the run, so you
never have to parse failure evidence to find out whether a run needs attention.

Two records mean an uncertain write:

- A write execution that ended without reporting records phase `interrupted`
  and outcome `ambiguous`, with `reconciliation_required` set.
- An apply that began writing and then failed records phase `apply-failed` and
  outcome `failed`, with `summary.may_have_partially_written` set and
  `results.apply_failure` naming the stage, the error type, the operations
  already applied, and the one that failed.

`reconciliation_required` is a write-only verdict — an interrupted `plan` or
`verify` cannot have written — and nothing sets it back to false.

In both cases: read the record, inspect the destination, and then take a fresh
plan run. A new plan reads the destination as it now is, so what it proposes is
what is still outstanding. The terminal run is never reopened and no later run
inherits its admission.

## 12. Stop, and reset when you are done

```bash
./infrahub-sync-compose stop      # processes down, every volume untouched
./infrahub-sync-compose status    # STOPPED, exit 4
```

`stop` keeps every volume, so a later `start` resumes. To remove this instance
and its data:

```bash
./infrahub-sync-compose reset <the instance identity it displays>
```

`reset` makes you repeat the identity it shows, and there is no forcing flag. It
refuses any resource carrying another instance's label and stops before its
first mutation. It leaves `operator.env` and `secrets/` in place.

Reset the deployment when you are finished. This alpha promises no in-place
state migration and no backup or restore, so a reset is also how it is replaced:
`reset`, then `init` for a new identity, then `start`. That start is a cold
bootstrap and prior run history, retained plans, and artifacts do not survive it.

## Expiry cannot be extended

The candidate artifacts are retained for exactly 30 days from their upload. That
window cannot be extended, and there is no way to refresh it in place.

When it lapses, the remedy is a new dispatch of `workflow-candidate.yml` at the
**same exact commit** — the `CANDIDATE_SHA` you recorded, not the branch tip.
The new run will almost certainly have a different workflow revision, and that
is expected: `head_sha` moves, the candidate commit does not.

Nothing else carries over. The new run produces new artifacts with new service
IDs and new transport digests, so every identifier you recorded belongs to the
old run. You accept the new bytes exactly as you accepted these, from step 1,
including the seven-group inventory and the granted 30 days.

## What to record

Report all of this, whether or not it went well.

**Provenance, both commits:**

- the run ID;
- the candidate commit (`CANDIDATE_SHA`) — the commit the artifacts describe;
- the workflow revision (`head_sha`) — which workflow definition ran;
- the `revision` and `version` from `identity.json`, and that the revision
  equalled the candidate commit.

**Transport acceptance, all seven groups:**

- for each of the seven: the service artifact ID, the transport digest, and the
  granted window in days;
- that all seven were present, unexpired, and granted exactly 30 days;
- that the six identifiers in `qualification.json` matched the service
  inventory, and the qualification record's own ID and digest, recorded by hand
  because it cannot contain them.

**The three other digests, kept separate:**

- the bundle file digest from the record, and the `sha256sum` you computed;
- the image configuration digest from the record, and what
  `docker image inspect` reported;
- any deployment artifact digest you verified in step 9.

**The deployment:**

- the endpoint-backed `READY` result;
- the run ID of the plan you read, its checksum, and the apply that bound it;
- what you observed at the destination afterwards.

**And the part that matters most:**

- every step where this page was wrong, incomplete, or assumed something you had
  to work out yourself;
- anything you had to install, configure, or work around that it does not
  mention.

This page has to work for someone who did not build the system, and you are the
evidence for whether it does.
