#!/bin/sh
# The clean-host qualification driver.
#
# It runs on a host that has Docker Engine, Docker Compose, a POSIX shell, and
# ordinary checksum and archive tools. It has no product checkout, no host
# Python, and no `uv`: every command that needs either runs inside the candidate
# image, which is the artifact under qualification.
#
# There is no skip. Each row of the matrix below is a function that either
# returns or ends the run, so a row cannot pass by being unreachable and cannot
# report a result it did not produce. The rows run in order and each one leaves
# the deployment the next one expects.
set -eu

KIT=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
WORK=${CLEAN_HOST_WORK:-$KIT/work}

# Every row this gate makes a claim about, in the order they run.
MATRIX='artifact_identity
cold_start_and_idempotence
managed_execution
unkeyed_write_policy
schema_change
status
restart
recovery
ownership_and_reset
alpha_replacement
secrets'

# What the candidate workflow uploaded. Nothing here is fetched and nothing is
# taken on trust: each is checked against the record that says what it is.
CANDIDATE=${CLEAN_HOST_CANDIDATE:?the candidate artifact directory is required}
IMAGE_ARCHIVE=$CANDIDATE/image-linux-amd64.tar

EXTRACTED=$WORK/extracted
DESTINATION=$KIT/destination
CHECKS=$KIT/checks

# The pinned destination, and where this gate publishes it. Not the bundle's own
# ports: the subject of this gate runs on its shipped defaults, and the fixture
# moves out of their way.
FIXTURE_PROJECT=infrahub-sync-clean-host-destination
DESTINATION_PORT=${CLEAN_HOST_DESTINATION_PORT:-8080}
# The public development default the pinned fixture seeds. It reaches only the
# disposable containers this gate creates.
DESTINATION_TOKEN=${CLEAN_HOST_DESTINATION_TOKEN:-06438eb2-8019-4776-878c-0941b1f1d1ec}
FOREIGN_VOLUME=infrahub-sync-clean-host-foreign

# Set by the rows below as they establish them.
IMAGE=
INSTANCE=
NETWORK=
BUNDLE=
ROW=startup

# ---------------------------------------------------------------------------
# Reporting. Every refusal is a row name and a fixed sentence. No value read out
# of the deployment reaches a message, because some of them are credentials.
# ---------------------------------------------------------------------------
fail() {
    echo "clean-host: $ROW: $1" >&2
    exit 1
}

report() {
    echo "clean-host: $ROW: $1"
}

require() {
    # require <sentence> <expected> <actual>
    if [ "$2" != "$3" ]; then
        fail "$1"
    fi
}

# ---------------------------------------------------------------------------
# What this host is allowed to be
# ---------------------------------------------------------------------------
# Shims that fail, ahead of everything on PATH. Nothing in this gate may reach a
# host interpreter or package manager: the artifact under qualification carries
# its own, and a host that quietly supplied one would be qualifying a deployment
# no clean host could reproduce. The refusal is recorded when it happens, so the
# claim is about this run rather than about what this host has installed.
HOST_TOOLS='python python3 uv uvx pip pip3 pytest infrahub-sync infrahubctl'

install_refusing_shims() {
    mkdir -p "$WORK/no-host-tools"
    for tool in $HOST_TOOLS; do
        cat > "$WORK/no-host-tools/$tool" <<SHIM
#!/bin/sh
echo "\$0" >> "$WORK/host-tool-used"
echo "clean-host: this gate reached a host tool: \$0" >&2
exit 127
SHIM
        chmod 0755 "$WORK/no-host-tools/$tool"
    done
    PATH="$WORK/no-host-tools:$PATH"
    export PATH
}

require_no_host_tool_was_used() {
    if [ -s "$WORK/host-tool-used" ]; then
        fail "this gate reached a host tool: $(tr '\n' ' ' < "$WORK/host-tool-used")"
    fi
}

# The deployment's own services may mount only what the extracted bundle gives
# them. A bind source outside it is a path this host was expected not to have.
require_no_foreign_mount() {
    owned=$(docker ps --all --quiet --filter "label=io.infrahub-sync.instance=$INSTANCE") \
        || fail "this host could not be asked which containers the deployment owns"
    [ -n "$owned" ] || fail "the deployment owns no containers, so nothing was inspected for a foreign mount"
    for container in $owned; do
        for source in $(docker inspect \
            --format '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}} {{end}}{{end}}' "$container"); do
            case "$source" in
                "$BUNDLE"/*) ;;
                *) fail "a deployed container mounts $source, which the extracted bundle does not hold" ;;
            esac
        done
    done
}

# ---------------------------------------------------------------------------
# Reading the record, and running a check inside the candidate
# ---------------------------------------------------------------------------
# The reader is the candidate image, because this host has no interpreter. Read it
# into a variable: a command substitution inside an argument discards the reader's
# own exit status, so a failed read would reach a message as an empty string.
record() {
    [ -n "$IMAGE" ] || fail "the candidate record was read before the image that reads it was loaded"
    docker run --rm --network=none \
        --volume "$CANDIDATE:/candidate:ro" \
        "$IMAGE" python -c "import json;print(json.load(open('/candidate/qualification.json'))$1)"
}

# The checks are this gate's code, mounted read-only into a throwaway container
# on the deployment's network. They are never a service of the deployment.
# The extracted bundle's declared configuration comes with them, read-only: which
# branches a run reads and writes is named there and nowhere else, and a row that
# plants a difference has to put it on the side the plan reads from.
check() {
    name=$1
    shift
    [ -n "$NETWORK" ] || fail "the check $name was run before the deployment network it needs existed"
    docker run --rm \
        --network "$NETWORK" \
        --volume "$CHECKS:/checks:ro" \
        --volume "$BUNDLE/configuration:/configuration:ro" \
        --env-file "$WORK/check.env" \
        "$IMAGE" python "/checks/$name.py" "$@"
}

# The destination is reached at a host address, so a check that only talks to it
# needs no deployment network -- and runs before one exists.
#
# The extracted bundle's declared configuration comes with it, read-only. The
# branch a managed run writes to is named in that document and nowhere else, and
# the destination has to be prepared for the branch this deployment will actually
# use rather than for one the kit names on its own.
destination_check() {
    name=$1
    shift
    docker run --rm \
        --volume "$CHECKS:/checks:ro" \
        --volume "$BUNDLE/configuration:/configuration:ro" \
        --env-file "$WORK/check.env" \
        "$IMAGE" python "/checks/$name.py" "$@"
}

compose_bundle() {
    "$BUNDLE/infrahub-sync-compose" "$@"
}

# Start the deployment and take the network it created. Every row reaches the
# deployment and the checks through that network, and a start that does not reach
# READY ends the run wherever it happens.
start_deployment() {
    compose_bundle start >"$WORK/start" 2>&1 || fail "$1"
    NETWORK=$(deployment_network)
    require "$2" READY "$(deployment_status)"
}

# A reset removes the instance state file, so the deployment has no identity
# until it is initialised again -- and the identity it takes is a new one.
reinitialise_deployment() {
    compose_bundle init >/dev/null || fail "the bundle could not initialise a deployment again"
    INSTANCE=$(instance_identity)
    configure_deployment
}

setting() {
    sed -n "s/^$1=//p" "$BUNDLE/operator.env" | tail -1
}

# The principal's token, which is a value inside the bearer document rather than
# a setting of its own. The API sees the token alone and a log could carry it
# alone, so the extraction is one function: the value the checks are given is
# then the value the sweep looks for.
bearer_token() {
    setting INFRAHUB_SYNC_SERVICE_BEARER_TOKENS | sed 's/.*"token": *"//;s/".*//'
}

# The PostgreSQL administrator password, generated by `init` into a file of its
# own because the official image reads it from one. No setting names it.
admin_password() {
    sed -n 1p "$BUNDLE/secrets/postgres-admin-password" 2>/dev/null
}

# The generated identity, read the way the entry point reads it. The state file
# holds `KEY=VALUE`, and the label the deployment carries is the value alone, so
# taking the file whole builds a filter that matches nothing and reports it as
# nothing being there.
instance_identity() {
    identity=$(sed -n "s/^INFRAHUB_SYNC_INSTANCE=//p" "$BUNDLE/.instance" | tail -1)
    [ -n "$identity" ] || fail "the initialised bundle names no instance identity"
    echo "$identity"
}

# One snapshot of everything a restart or a repeat start must not change.
durable_snapshot() {
    check durable_state
}

# ---------------------------------------------------------------------------
# The destination this gate plans and applies against
# ---------------------------------------------------------------------------
# The pinned fixture, started from the qualification kit. It is evidence, not
# bundle: a deployment reaches a real destination at a real address, and this one
# exists only so the managed rows have something to converge against.
destination_compose() {
    docker compose --project-name "$FIXTURE_PROJECT" \
        --env-file "$DESTINATION/preview.env" \
        --file "$DESTINATION/docker-compose.infrahub.yml" \
        --file "$DESTINATION/docker-compose.preview.yml" "$@"
}

start_destination() {
    destination_compose up --detach --wait --wait-timeout 600 infrahub-server task-worker \
        || fail "the pinned destination fixture did not start"
}

seed_destination() {
    destination_check seed_destination || fail "the pinned destination fixture could not be seeded"
}

stop_destination() {
    destination_compose down --remove-orphans --volumes >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Row 1 — artifact identity
# ---------------------------------------------------------------------------
row_artifact_identity() {
    # The image first, because the record is JSON and reading it needs the
    # interpreter the image carries. Loading is not trusting: the identifier a
    # load produces is the configuration digest, which is what the record names.
    loaded=$(docker load --input "$IMAGE_ARCHIVE" | sed -n 's/^Loaded image: //p')
    [ -n "$loaded" ] || fail "the candidate image archive loaded no image"
    IMAGE=$(docker image inspect --format '{{.Id}}' "$loaded")
    recorded=$(record "['image']['platforms']['linux/amd64']['config']") \
        || fail "the candidate record does not name a linux/amd64 configuration digest"
    require "the loaded image is not the linux/amd64 candidate the record names" "$recorded" "$IMAGE"
    tag=$(record "['identity']['tag']") || fail "the candidate record names no release tag"
    report "the loaded image is the recorded linux/amd64 candidate of $tag"

    # The checksum with the host's own tool, in the form the candidate wrote it.
    bundle_name=$(record "['bundle']['name']") || fail "the candidate record names no bundle"
    ( cd "$CANDIDATE" && sha256sum -c "$bundle_name.sha256" >/dev/null 2>&1 ) \
        || fail "the deployment bundle does not match the checksum the record names"
    report "the bundle matches the checksum the record names"

    # The bundle root is wherever the entry point is. The archive's own top-level
    # directory is not part of what the record promises, so it is found rather
    # than derived from a filename.
    mkdir -p "$EXTRACTED"
    tar -xzf "$CANDIDATE/$bundle_name" -C "$EXTRACTED"
    entry=$(find "$EXTRACTED" -type f -name infrahub-sync-compose | head -1)
    [ -n "$entry" ] || fail "the bundle archive holds no lifecycle entry point"
    [ -x "$entry" ] || fail "the bundle's lifecycle entry point is present but not executable"
    BUNDLE=$(dirname "$entry")
    report "the bundle extracted and its entry point arrived executable"

    compose_bundle init >/dev/null || fail "the extracted bundle could not initialise a deployment"
    INSTANCE=$(instance_identity)
}

# A tag names whatever it points at today, so the bundle has to refuse one before
# it starts anything. Two things decide whether this tests that at all: the
# reference is read from the operator's settings file, which is the only channel
# `check_image` consults, and preflight reaches that check only once every
# required setting has a value -- credentials are checked first and refuse for
# their own reason.
refuse_a_tag_only_image() {
    digest=$(setting INFRAHUB_SYNC_IMAGE)
    set_setting INFRAHUB_SYNC_IMAGE "infrahub-sync:latest"
    if compose_bundle preflight >"$WORK/tag-refusal" 2>&1; then
        set_setting INFRAHUB_SYNC_IMAGE "$digest"
        fail "the bundle accepted a tag-only image reference"
    fi
    set_setting INFRAHUB_SYNC_IMAGE "$digest"
    grep -q "image-not-immutable" "$WORK/tag-refusal" \
        || fail "the bundle refused a tag-only reference, but not for being mutable"
    report "a tag-only image reference is refused before anything starts"
}

# ---------------------------------------------------------------------------
# Pointing the deployment at the destination
# ---------------------------------------------------------------------------
# A deployment's destination is a real address it resolves like any other, so the
# gate hands it one: the runner's own address, with the fixture published there.
# The shipped topology carries no route to a host and needs none.
host_address() {
    address=$(ip -4 route get 1 2>/dev/null | awk '{print $7; exit}' || true)
    [ -n "$address" ] || address=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
    [ -n "$address" ] || fail "this host has no routable address for the destination to be reached at"
    echo "$address"
}

set_setting() {
    # set_setting <key> <value>, in the operator's own file.
    grep -v "^$1=" "$BUNDLE/operator.env" > "$WORK/operator.next" || true
    printf '%s=%s\n' "$1" "$2" >> "$WORK/operator.next"
    mv "$WORK/operator.next" "$BUNDLE/operator.env"
}

# The declared configuration is edited after the archive's checksum has already
# been verified, and the record says so: the checksum is a claim about the
# archive as shipped, never about the tree an operator then edits. Only the two
# destination addresses change. The declared name is left alone, because
# registering different content under a name already registered is refused.
point_configuration_at_destination() {
    address=$1
    sed "s#url: \"http://[^\"]*\"#url: \"http://$address:$DESTINATION_PORT\"#" \
        "$BUNDLE/configuration/qualification.yaml" > "$WORK/configuration.next"
    mv "$WORK/configuration.next" "$BUNDLE/configuration/qualification.yaml"
    grep -c "http://$address:$DESTINATION_PORT" "$BUNDLE/configuration/qualification.yaml" | grep -qx 2 \
        || fail "the declared configuration does not name the destination twice"
}

configure_deployment() {
    address=$(host_address)
    set_setting INFRAHUB_SYNC_IMAGE "$IMAGE"
    set_setting INFRAHUB_API_TOKEN "$DESTINATION_TOKEN"
    point_configuration_at_destination "$address"
    # The checks reach the deployment through the product's own client, which is
    # the surface an operator has. Negative destination-state assertions are read
    # from the destination, PostgreSQL, and the object store, independently of
    # that client: a client that misreads a response would otherwise confirm a
    # negative in the same direction as the bug that caused it.
    cat > "$WORK/check.env" <<ENV
INFRAHUB_SYNC_API_URL=http://sync-api:8000
INFRAHUB_SYNC_API_TOKEN=$(bearer_token)
INFRAHUB_SYNC_DATABASE_URL=$(setting INFRAHUB_SYNC_DATABASE_URL)
INFRAHUB_SYNC_S3_ENDPOINT_URL=http://object-store:9000
INFRAHUB_SYNC_S3_BUCKET=$(sed -n 's/^INFRAHUB_SYNC_S3_BUCKET=//p' "$BUNDLE/defaults.conf")
AWS_ACCESS_KEY_ID=$(setting INFRAHUB_SYNC_S3_ACCESS_KEY)
AWS_SECRET_ACCESS_KEY=$(setting INFRAHUB_SYNC_S3_SECRET_KEY)
PREFECT_API_URL=http://prefect-server:4200/api
INFRAHUB_DESTINATION_URL=http://$address:$DESTINATION_PORT
INFRAHUB_DESTINATION_TOKEN=$DESTINATION_TOKEN
INFRAHUB_ADDRESS=http://$address:$DESTINATION_PORT
INFRAHUB_API_TOKEN=$DESTINATION_TOKEN
CLEAN_HOST_SCHEMA=/checks/infra_device.yml
ENV
}

deployment_container() {
    docker ps --all --quiet \
        --filter "label=io.infrahub-sync.instance=$INSTANCE" \
        --filter "name=$1" > "$WORK/containers" \
        || fail "this host could not be asked which containers the deployment owns"
    container=$(head -1 "$WORK/containers")
    [ -n "$container" ] || fail "the deployment owns no $1 container"
    echo "$container"
}

deployment_network() {
    docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' \
        "$(deployment_container sync-api)"
}

# The entry point reports the deployment's state as its exit status. Reading it
# needs the errexit shell option lifted for exactly one command, because a
# non-zero status here is an answer rather than a failure.
deployment_status() {
    set +e
    compose_bundle status >"$WORK/status" 2>&1
    verdict=$?
    set -e
    case $verdict in
        0) echo READY ;;
        3) echo DEGRADED ;;
        4) echo STOPPED ;;
        *) echo UNKNOWN ;;
    esac
}

# ---------------------------------------------------------------------------
# Row 2 — cold start and idempotence
# ---------------------------------------------------------------------------
row_cold_start_and_idempotence() {
    start_destination
    configure_deployment
    seed_destination
    refuse_a_tag_only_image

    start_deployment "the extracted bundle did not reach a ready deployment from empty state" \
        "the deployment did not report READY after a cold start"
    require_no_foreign_mount
    report "empty state reached READY, with nothing mounted from outside the bundle"

    before=$(durable_snapshot)
    compose_bundle start >"$WORK/second-start" 2>&1 \
        || fail "a second start of the same deployment did not succeed"
    require "a second start changed a durable object" "$before" "$(durable_snapshot)"
    report "a second start changed no durable object"
}

# ---------------------------------------------------------------------------
# Row 3 — managed execution
# ---------------------------------------------------------------------------
row_managed_execution() {
    check managed_execution \
        || fail "a managed run did not complete plan, retrieve, review, verify, apply, and sync"
    report "plan, retrieve, review, verify, confirmed apply, and a separate sync ran through the worker"
}

# ---------------------------------------------------------------------------
# Row 4 — unkeyed write policy
# ---------------------------------------------------------------------------
row_unkeyed_write_policy() {
    check unkeyed_write_policy \
        || fail "an unkeyed operation was not refused before its own mutation"
    report "an operation whose key cannot be rendered was refused with nothing written"
}

# ---------------------------------------------------------------------------
# Row 5 — schema change
# ---------------------------------------------------------------------------
row_schema_change() {
    check schema_change \
        || fail "a runtime schema change did not behave as the artifact contract requires"
    report "a compatible change needed no restart; an incompatible one refused before any write"
}

# ---------------------------------------------------------------------------
# Row 6 — status
# ---------------------------------------------------------------------------
row_status() {
    check busy_worker_stays_ready || fail "a busy worker did not leave the deployment READY"
    report "a busy worker leaves the deployment READY"

    worker=$(deployment_container sync-worker)
    docker pause "$worker" >/dev/null
    if ! wait_for_state DEGRADED; then
        docker unpause "$worker" >/dev/null
        fail "a paused worker did not age the deployment into DEGRADED"
    fi
    docker unpause "$worker" >/dev/null
    wait_for_state READY || fail "the deployment did not return to READY once its worker resumed"
    report "a paused worker ages into DEGRADED while its container still runs"

    docker stop "$worker" >/dev/null
    wait_for_state DEGRADED || fail "a stopped worker did not age the deployment into DEGRADED"
    docker start "$worker" >/dev/null
    wait_for_state READY || fail "the deployment did not return to READY once its worker restarted"
    report "a stopped worker ages into DEGRADED while the API stays reachable"

    compose_bundle stop >/dev/null || fail "the deployment could not be stopped"
    require "a stopped deployment did not report STOPPED" STOPPED "$(deployment_status)"
    start_deployment "a stopped deployment did not start again" \
        "the deployment did not return to READY"
    report "a stopped deployment reports STOPPED and starts again"
}

wait_for_state() {
    wanted=$1
    waited=0
    while [ "$waited" -lt 240 ]; do
        [ "$(deployment_status)" = "$wanted" ] && return 0
        sleep 5
        waited=$((waited + 5))
    done
    return 1
}

# ---------------------------------------------------------------------------
# Row 7 — restart
# ---------------------------------------------------------------------------
row_restart() {
    before_worker=$(deployment_container sync-worker)
    before_state=$(durable_snapshot)

    compose_bundle restart >/dev/null || fail "the deployment could not be restarted"
    require "the deployment did not return to READY after a restart" READY "$(deployment_status)"

    after_worker=$(deployment_container sync-worker)
    [ "$before_worker" != "$after_worker" ] \
        || fail "a restart left the same worker container, so nothing was replaced"
    require "a restart changed a durable record" "$before_state" "$(durable_snapshot)"
    report "the worker was replaced while every durable record stayed equal"
}

# ---------------------------------------------------------------------------
# Row 8 — recovery
# ---------------------------------------------------------------------------
row_recovery() {
    # The interruption is a host action, so the precondition is a check of its
    # own: it returns only once the run has reached its write, and a worker
    # killed before that leaves nothing ambiguous to reconcile.
    check start_apply > "$WORK/interrupted" \
        || fail "no confirmed write reached the point where interrupting it means anything"
    interrupted=$(tail -1 "$WORK/interrupted")
    [ -n "$interrupted" ] || fail "the interrupted run was never named"
    docker kill "$(deployment_container sync-worker)" >/dev/null
    start_deployment "the deployment did not come back after its worker was killed" \
        "the deployment did not return to READY after its worker was killed"
    check recovery "$interrupted" \
        || fail "an interrupted write did not leave durable reconciliation state a fresh plan could follow"
    report "an interrupted write was not retried, and a fresh plan followed its durable state"
}

# ---------------------------------------------------------------------------
# Row 9 — ownership and reset
# ---------------------------------------------------------------------------
row_ownership_and_reset() {
    docker volume create "$FOREIGN_VOLUME" >/dev/null
    docker volume inspect "$FOREIGN_VOLUME" >/dev/null || fail "the foreign volume was not created"

    # A reset that did not have to repeat the exact identity would be a reset
    # anyone could run against a deployment they had not read the name of.
    if compose_bundle reset "not-this-instance" >"$WORK/reset-refusal" 2>&1; then
        fail "the deployment reset without being given its own identity"
    fi
    grep -q "confirmation-required" "$WORK/reset-refusal" \
        || fail "the deployment refused the reset, but not for the identity it was given"
    report "a reset without the exact instance identity is refused"

    compose_bundle reset "$INSTANCE" >/dev/null || fail "the deployment could not be reset"
    docker volume inspect "$FOREIGN_VOLUME" >/dev/null \
        || fail "the reset removed a volume this deployment did not own"
    [ -z "$(docker ps --all --quiet --filter "label=io.infrahub-sync.instance=$INSTANCE")" ] \
        || fail "the reset left a container this deployment owned"
    report "the reset removed only what this instance owned, and the foreign volume survived"

    reinitialise_deployment
    start_deployment "the deployment did not start again after a reset" \
        "the deployment did not return to READY after a reset"
    check cold_bootstrap || fail "the start after a reset was not a cold bootstrap"
    report "the next start after a reset is cold"

    docker volume rm "$FOREIGN_VOLUME" >/dev/null
}

# ---------------------------------------------------------------------------
# Row 10 — alpha replacement
# ---------------------------------------------------------------------------
row_alpha_replacement() {
    # This alpha promises no in-place state migration. Its replacement procedure
    # is the documented one: reset, then deploy the same exact bundle and image
    # again. Nothing here upgrades anything, and that is the claim being made.
    check seed_disposable_state || fail "disposable state could not be created before replacement"
    before=$(durable_snapshot)
    [ -n "$before" ] || fail "there was no disposable state to replace"

    compose_bundle reset "$INSTANCE" >/dev/null || fail "the documented replacement could not reset"
    reinitialise_deployment
    start_deployment "the documented replacement could not deploy again" \
        "the replaced deployment did not report READY"

    [ "$before" != "$(durable_snapshot)" ] \
        || fail "the replacement kept the prior state, which this alpha does not promise"
    version=$(check served_version) || fail "the replaced deployment did not report a version"
    recorded=$(record "['identity']['version']") || fail "the candidate record names no version"
    require "the replaced deployment does not serve the recorded version" "$recorded" "$version"
    report "reset and redeploy replaced prior disposable state with the recorded version"
}

# ---------------------------------------------------------------------------
# Row 11 — secrets
# ---------------------------------------------------------------------------
# The canaries are the credentials this run generated, not planted values: they
# are per-run by construction, and they are the ones that would actually hurt.
# Everything the gate leaves behind is swept, including the kit itself, because
# the kit is evidence and the contract names evidence.
#
# What this row does not sweep for, as stated scope rather than omission: the
# destination fixture's API token. It is a published development constant that
# ships inside this kit, in the fixture's own environment file, so a sweep for it
# would match the kit on every run and never be able to report anything else. The
# claim here is about credentials this run generated -- values that exist because
# `init` made them, and that no published artifact could already contain.
SECRET_SETTINGS='INFRAHUB_SYNC_PRODUCT_PASSWORD
INFRAHUB_SYNC_PREFECT_PASSWORD
INFRAHUB_SYNC_S3_ACCESS_KEY
INFRAHUB_SYNC_S3_SECRET_KEY
INFRAHUB_SYNC_SERVICE_BEARER_TOKENS'

# A value that holds no generated credential is not a value to sweep for, and a
# sweep that cannot name what it looks for clears nothing -- so the failure to
# record one ends the attempt rather than shortening the list.
record_canary() {
    # record_canary <destination> <what it is> <value>
    case $3 in
        ''|*'${'*) printf '%s\n' "$2" > "$WORK/canary-missing"; return 1 ;;
    esac
    printf '%s\n' "$3" >> "$1"
}

# The list every sweep looks for, written where a sweep can read it. Most of what
# `init` generates is a setting; two are values inside something else, and those
# two are exactly the ones an API or a database log would carry. A sweep that
# looked only at what a setting is called would clear both.
write_canaries() {
    # write_canaries <destination>; one generated credential per line.
    : > "$1"
    for name in $SECRET_SETTINGS; do
        record_canary "$1" "$name" "$(setting "$name")" || return 1
    done
    record_canary "$1" "the principal's bearer token" "$(bearer_token)" || return 1
    record_canary "$1" "the PostgreSQL administrator password" "$(admin_password)" || return 1
}

# The one primitive both sweeps use, so neither can look for something else.
carries_a_canary() {
    # carries_a_canary <file> <canary list>; true when the file holds one of them.
    while read -r canary; do
        [ -n "$canary" ] || continue
        if grep -qF -- "$canary" "$1" 2>/dev/null; then
            return 0
        fi
    done < "$2"
    return 1
}

row_secrets() {
    write_canaries "$WORK/canaries" \
        || fail "$(sed -n 1p "$WORK/canary-missing") holds no generated value for this run to sweep for"

    compose_bundle logs > "$WORK/deployment.log" 2>&1 || true
    docker image history --no-trunc --format '{{.CreatedBy}}' "$IMAGE" > "$WORK/image.history"
    check reported_failures > "$WORK/reported.failures" 2>&1 || true

    bundle_name=$(record "['bundle']['name']") || fail "the candidate record names no bundle to sweep"
    # The shipped bytes, not the compressed container of them: a plaintext search
    # of a gzip stream cannot match, so it would report success without looking.
    gzip -dc "$CANDIDATE/$bundle_name" > "$WORK/bundle.tar" \
        || fail "the deployment bundle could not be decompressed to be swept"
    for target in "$WORK/deployment.log" "$WORK/image.history" "$WORK/reported.failures" "$WORK/bundle.tar"; do
        if carries_a_canary "$target" "$WORK/canaries"; then
            fail "a credential this run generated reached $(basename "$target")"
        fi
    done
    # The kit is evidence, and evidence is named by the contract too. Its own
    # working directory is excluded: that is where this sweep keeps the list.
    while read -r canary; do
        [ -n "$canary" ] || continue
        if grep -rqF --exclude-dir=work -- "$canary" "$KIT" 2>/dev/null; then
            fail "a credential this run generated reached the qualification kit"
        fi
    done < "$WORK/canaries"
    report "no credential this run generated reached the bundle, image history, logs, failures, or the kit"
}

# ---------------------------------------------------------------------------
# What a failed row leaves behind to be read
# ---------------------------------------------------------------------------
# A failure is diagnosed from state the deployment still holds, and the teardown
# below removes that state by design. So the account is taken first, and from two
# places that do not depend on each other: the run records in PostgreSQL, read
# directly rather than through the client whose verdict is the thing in question,
# and a bounded tail of what the API and the worker said.
#
# It is kept beside the kit's working files, which is outside the extracted
# bundle and outside every deployment volume -- the two things teardown removes.
DIAGNOSTIC=$WORK/diagnostic.txt
DIAGNOSTIC_LINES=200
DIAGNOSTIC_SERVICES='sync-api sync-worker'
# The destination's own service, because a destination rejection is reported to
# the deployment as whatever the destination chose to say -- and Infrahub's
# GraphQL wrapper chooses to say almost nothing. Its side of the exchange is on
# its side. Only the server: the fixture's database, cache and task worker have
# no part in answering a mutation this gate sent.
DIAGNOSTIC_DESTINATION_SERVICE=infrahub-server

# Assemble, then sweep the exact bytes that would be kept: redacting each part
# and trusting the whole is how a concatenation leaks. Anything found withholds
# all of it rather than some, because a file that has left is not recoverable.
capture_diagnostic() {
    # capture_diagnostic <the row that failed>
    if [ -z "$INSTANCE" ] || [ -z "$BUNDLE" ]; then
        echo "clean-host: teardown: no diagnostic: this run reached no deployment to read one from" >&2
        return 0
    fi
    assembled=$WORK/diagnostic.next
    {
        printf 'row %s\ninstance %s\nstate %s\n\n' "$1" "$INSTANCE" "$(deployment_status)"
        echo "--- run records, read from the store rather than from the API ---"
        # The reader runs on the deployment's own network. A start that never
        # reached READY is the failure this account matters most for, and it has
        # no network -- so the logs below are taken either way and this section
        # says what it could not reach.
        if [ -n "$NETWORK" ]; then
            check diagnostics 2>&1 || echo "the run records could not be read"
        else
            echo "this run failed before the deployment had a network to read them over"
        fi
        for service in $DIAGNOSTIC_SERVICES; do
            printf '\n--- %s (last %s lines) ---\n' "$service" "$DIAGNOSTIC_LINES"
            INFRAHUB_SYNC_LOG_LINES=$DIAGNOSTIC_LINES compose_bundle logs "$service" 2>&1 \
                || echo "this service reported no log"
        done
        printf '\n--- destination %s (last %s lines) ---\n' \
            "$DIAGNOSTIC_DESTINATION_SERVICE" "$DIAGNOSTIC_LINES"
        # The fixture is this gate's own evidence, so its log is read the same
        # way and swept with everything else below. No generated credential of
        # this deployment ever reaches it: the destination is given the published
        # development token and nothing else this run made.
        destination_compose logs --no-color --tail "$DIAGNOSTIC_LINES" \
            "$DIAGNOSTIC_DESTINATION_SERVICE" 2>&1 \
            || echo "the destination fixture reported no log"
    } > "$assembled" 2>/dev/null || true
    if ! write_canaries "$WORK/diagnostic.canaries"; then
        rm -f "$assembled"
        echo "clean-host: teardown: diagnostic withheld: this run named no credential to sweep its bytes for" >&2
        return 0
    fi
    if carries_a_canary "$assembled" "$WORK/diagnostic.canaries"; then
        rm -f "$assembled"
        echo "clean-host: teardown: diagnostic withheld: its bytes carry a credential this run generated" >&2
        return 0
    fi
    mv "$assembled" "$DIAGNOSTIC"
    echo "clean-host: teardown: diagnostic written to $DIAGNOSTIC" >&2
}

# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------
# Every container, volume, and network this run created carries the identity it
# generated, so teardown names that identity and can reach nothing else on the
# host. A prefix sweep would take deployments this run never made.
#
# A query this host refused to answer is not an empty answer. Each one lands in a
# file so its own status is what decides, and a refusal puts a sentence in the
# list -- which reads as something still being present, because nothing here can
# know that it is not.
owned_resources() {
    [ -n "$INSTANCE" ] || return 0
    docker ps --all --quiet --filter "label=io.infrahub-sync.instance=$INSTANCE" > "$WORK/owned-containers" \
        || echo "the containers this run owns could not be listed"
    docker volume ls --quiet --filter "label=io.infrahub-sync.instance=$INSTANCE" > "$WORK/owned-volumes" \
        || echo "the volumes this run owns could not be listed"
    tr -s '[:space:]' '\n' < "$WORK/owned-containers"
    tr -s '[:space:]' '\n' < "$WORK/owned-volumes"
}

# The failure that caused the exit is the one re-raised: teardown never replaces
# a diagnosis. What it could not remove is reported, because a deployment left
# running is the next run's "empty state" -- and a run that finished cleanly and
# then failed to tear down has not left the host as it found it either.
cleanup() {
    status=$?
    failed_row=$ROW
    ROW=teardown
    # Before anything is removed, because after it there is nothing to read.
    if [ "$status" -ne 0 ]; then
        capture_diagnostic "$failed_row"
    fi
    held=$(owned_resources | wc -w | tr -d ' ')
    if [ -n "$INSTANCE" ]; then
        # The entry point first, because it is the operator path. Its own project,
        # by exact identity, is the fallback for a reset it refuses -- after a row
        # has already removed the instance state the reset needs.
        compose_bundle reset "$INSTANCE" >"$WORK/teardown" 2>&1 \
            || docker compose --project-name "infrahub-sync-$INSTANCE" \
                down --volumes --remove-orphans >>"$WORK/teardown" 2>&1 \
            || true
    fi
    stop_destination
    docker volume rm "$FOREIGN_VOLUME" >/dev/null 2>&1 || true
    # Complete or incomplete, said out loud either way: a silent teardown and one
    # that removed nothing read the same, and that is how a deployment left
    # running becomes the next run's empty state.
    if [ -n "$(owned_resources)" ]; then
        echo "clean-host: teardown: incomplete: resources this run owns are still present; see $WORK/teardown" >&2
        [ "$status" -ne 0 ] || status=1
    else
        report "complete: the $held containers and volumes this run owned are gone, and the destination fixture with them"
    fi
    exit "$status"
}

main() {
    mkdir -p "$WORK"
    : > "$WORK/host-tool-used"
    install_refusing_shims
    trap cleanup EXIT INT TERM

    report "clean-host qualification of the candidate in $CANDIDATE"
    for row in $MATRIX; do
        ROW=$row
        "row_$row"
    done

    ROW=closing
    require_no_host_tool_was_used
    report "every row of the mandatory matrix passed"
}

main "$@"
