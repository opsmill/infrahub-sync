# Qualifying an internal candidate

Internal. This page tells a teammate how to obtain a pre-release Infrahub Sync
candidate from a GitHub Actions run and qualify it on their own host. It is not
on the documentation site, and the artifacts it names are not advertised: they
are unpublished pre-release bytes, and there is no registry, no package index,
and no tagged release behind them yet.

Written for someone who did not build this. You need a Linux host with Docker
and Docker Compose 2.17.3 or later, `gh` authenticated against
`opsmill/infrahub-sync`, an Infrahub instance you are authorised to write to,
and nothing else — no checkout of this repository, no Python, no `uv`.

Two things are being tested at once. One is the candidate. The other is this
document: you are the first person to follow it, so record where it was wrong.
[What to record](#what-to-record) is the last section.

## 1. Choose a run, and know which commit it built

A candidate is built by a manual dispatch of `workflow-candidate.yml` against an
exact merged commit. A pull-request run is not a candidate: GitHub tests the
merge commit while the run builds the pull request's head, so its artifacts
describe bytes nobody will ship. A pull-request run also deletes what it built
before it finishes.

List the candidate runs and pick a successful one:

```bash
gh run list --repo opsmill/infrahub-sync \
  --workflow workflow-candidate.yml \
  --json databaseId,displayTitle,headSha,status,conclusion,createdAt
```

Take the run ID, and read the commit out of the run rather than assuming it:

```bash
RUN=<the run ID>
gh run view "$RUN" --repo opsmill/infrahub-sync --json headSha,conclusion,jobs
```

The run must be `success` and **both** of its jobs must have succeeded. The
`candidate` job builds and retains the bytes; the `clean-host` job is what
qualified them on a host with no checkout. A run whose `clean-host` job failed,
was skipped, or is still going has uploads but no qualification — the retained
artifacts alone are not a result.

Write the run ID and the commit down now. Everything below has to be traceable
to one run and one commit.

## 2. Download the candidate, and check it before trusting it

Four of the seven retained artifacts are what a host needs. Download them into
one directory:

```bash
mkdir -p candidate && cd candidate

gh run download "$RUN" --repo opsmill/infrahub-sync \
  --name infrahub-sync-candidate-image     --dir image
gh run download "$RUN" --repo opsmill/infrahub-sync \
  --name infrahub-sync-candidate-bundle    --dir bundle
gh run download "$RUN" --repo opsmill/infrahub-sync \
  --name infrahub-sync-qualification-record --dir record
gh run download "$RUN" --repo opsmill/infrahub-sync \
  --name infrahub-sync-candidate-identity  --dir identity
```

The other three — `infrahub-sync-candidate-distributions`,
`infrahub-sync-candidate-sboms`, and `infrahub-sync-qualification-kit` — are the
wheel and source distribution, the bills of materials and scan reports, and the
gate's own driver. You do not need them to run the candidate.

`infrahub-sync-candidate-image` keeps the build's directory layout, so the
archive you want is `image/archives/image-linux-amd64.tar`.

Now check that these files describe the commit you chose. `identity.json` names
the revision the artifacts were built from:

```bash
python3 -c "import json;d=json.load(open('identity/identity.json'));print(d['revision'], d['version'], d['bundle'])"
```

That `revision` must equal the `headSha` you read in step 1. If it does not,
stop: you are holding bytes from another commit.

Verify the bundle against its own checksum, and against the digest the record
names. These are two different checks — a corrupted transfer breaks the first, a
bundle that is not the one the record describes breaks the second:

```bash
( cd bundle && sha256sum -c ./*.tar.gz.sha256 )

python3 - <<'PY'
import hashlib, json, pathlib
record = json.load(open("record/qualification.json"))
archive = pathlib.Path("bundle") / record["bundle"]["name"]
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
print("record says:", record["bundle"]["sha256"])
print("this file is:", digest)
assert digest == record["bundle"]["sha256"], "this is not the bundle the record names"
PY
```

Do not extract the archive until both checks pass.

## 3. Load the image and confirm what you loaded

```bash
docker load -i image/archives/image-linux-amd64.tar
```

The record names the configuration digest the qualified image has. Read it, and
compare it against what Docker now holds:

```bash
python3 -c "import json;d=json.load(open('record/qualification.json'));print(d['image']['platforms']['linux/amd64']['config'])"

docker image inspect --format '{{.Id}}' <the reference docker load printed>
```

Those two must match. That identifier — not a tag — is what you give the
deployment. A tag can be re-pointed between the qualification that trusted an
image and the run that uses it, so the bundle refuses one.

## 4. Extract the bundle and prepare a deployment

```bash
tar -xzf "bundle/$(python3 -c "import json;print(json.load(open('record/qualification.json'))['bundle']['name'])")"
cd infrahub-sync-compose-*/
./infrahub-sync-compose init
```

`init` writes `.instance`, `secrets/postgres-admin-password`, and `operator.env`
with generated passwords. Two values are yours to supply in `operator.env`:

```bash
INFRAHUB_SYNC_IMAGE=sha256:<the configuration digest from step 3>
INFRAHUB_API_TOKEN=<a token for the Infrahub you are allowed to write to>
```

Use a **disposable** Infrahub you are authorised to write to. Step 7 applies a
real write. Do not point this at anything you cannot afford to have changed.

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

## 5. Preflight, then start

```bash
./infrahub-sync-compose preflight
```

`preflight` refuses before anything is created, and each refusal is one family
name and a fixed sentence. `OPERATING.md` has the table. The ones you are most
likely to meet here: `image-not-immutable` (you gave a tag), `image-unresolvable`
(the digest is not loaded), `credentials-missing` (a value in `operator.env` is
still empty or `REPLACE-ME`), `destination-unavailable` (your Infrahub URL did
not answer), and `port-occupied` (something already holds `127.0.0.1:8000` or
`:4200`).

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
in time. Take the logs before doing anything else:

```bash
./infrahub-sync-compose logs sync-worker
./infrahub-sync-compose logs sync-api
```

## 6. Talk to the deployment

The bearer token `init` generated is in `operator.env` under
`INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`.

```bash
export INFRAHUB_SYNC_API_URL=http://127.0.0.1:8000
export INFRAHUB_SYNC_API_TOKEN=<the generated principal token>

curl -sS "$INFRAHUB_SYNC_API_URL/status"
curl -sS -H "Authorization: Bearer $INFRAHUB_SYNC_API_TOKEN" "$INFRAHUB_SYNC_API_URL/configs"
```

The CLI ships in the same image, so you never install it:

```bash
sync() {
  docker run --rm --network host \
    --env INFRAHUB_SYNC_API_URL --env INFRAHUB_SYNC_API_TOKEN \
    "$INFRAHUB_SYNC_IMAGE" infrahub-sync "$@"
}

sync configs list
```

The configuration bootstrap registered is already there. Note its identity and
version — the next step needs both.

## 7. Plan, review, then apply what you reviewed

This is the part that writes. A managed write is admitted once and applied
against the checksum of the plan you reviewed, so nothing reaches the
destination until an apply names that checksum.

Plan first. It writes nothing:

```bash
sync diff --config-id <config> --version <version> --reason "candidate qualification"
```

That prints a run ID. Read the saved plan and its checksum:

```bash
sync runs plan <run-id>
sync runs plan <run-id> --detail
```

Read what it proposes before you go on. Then apply that exact plan:

```bash
sync apply <run-id> \
  --expected-checksum <the checksum runs plan printed> \
  --reason "candidate qualification"
```

A checksum that no longer matches the saved plan is refused. That is the point:
it means the plan you read is not the plan that would be applied, and the remedy
is a new plan, not a retry.

A separate `sync` is its own admitted run and confirms up front:

```bash
sync sync --config-id <config> --version <version> --reason "candidate qualification"
```

Confirm at the destination that the change you approved is the change that
happened.

## 8. Retrieve the run's evidence

A run's artifacts are held by the deployment, not on a container filesystem:

```bash
curl -sS -H "Authorization: Bearer $INFRAHUB_SYNC_API_TOKEN" \
  "$INFRAHUB_SYNC_API_URL/runs/<run-id>"
curl -sS -H "Authorization: Bearer $INFRAHUB_SYNC_API_TOKEN" \
  "$INFRAHUB_SYNC_API_URL/runs/<run-id>/results"
curl -sS -H "Authorization: Bearer $INFRAHUB_SYNC_API_TOKEN" \
  "$INFRAHUB_SYNC_API_URL/runs/<run-id>/artifacts"
```

Each entry in that list has an `artifact_id`, a `digest`, and a `size`. Fetch one
and check what arrived against the digest the list gave you:

```bash
curl -sS -D headers.txt -H "Authorization: Bearer $INFRAHUB_SYNC_API_TOKEN" \
  "$INFRAHUB_SYNC_API_URL/runs/<run-id>/artifacts/<artifact-id>" -o artifact.bin
grep -i '^digest:' headers.txt
sha256sum artifact.bin
```

## 9. Restart, and confirm it converges

Replacing the processes must lose nothing, because no run state lives on a
container filesystem:

```bash
./infrahub-sync-compose restart
./infrahub-sync-compose status
```

`status` must return to `READY`. The worker rejoins under a new Prefect
identity; the run you applied in step 7, its plan, and its artifacts must all
still be readable through the API afterwards. Check that they are — that is the
claim being tested, not the exit code.

Starting an already-started deployment is also safe. Bootstrap converges the two
databases and their owners, the bucket, the work pool, the installed deployment
and the declared configuration, and creates none of them twice:

```bash
./infrahub-sync-compose start
```

## 10. When the outcome of a write is uncertain

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

## 11. Stop, and reset when you are done

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

When it lapses, the remedy is a new dispatch of `workflow-candidate.yml` **at the
same exact commit**. That run produces new artifacts with new IDs and new
digests, so every digest you recorded belongs to the old run and none of it
carries over: you accept the new bytes the same way you accepted these, from
step 1. The commit is what stays the same; the bytes and their identifiers do
not.

## What to record

Report all of this, whether or not it went well:

- the run ID and the full commit you built from;
- the version and revision from `identity.json`;
- the image configuration digest you loaded and confirmed;
- the bundle name and its `sha256` from the record;
- the endpoint-backed `READY` result, and the run ID of the plan you applied;
- every step where this document was wrong, incomplete, or assumed something
  you had to work out yourself;
- anything you had to install, configure, or work around that this page does not
  mention.

The last two matter most. This page has to work for someone who did not build
the system, and you are the evidence for whether it does.
