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
DESTINATION_PORT=${CLEAN_HOST_DESTINATION_PORT:-8080}
# The public development default the pinned fixture seeds. It reaches only the
# disposable containers this gate creates.
DESTINATION_TOKEN=${CLEAN_HOST_DESTINATION_TOKEN:-06438eb2-8019-4776-878c-0941b1f1d1ec}

# Set by the rows below as they establish them.
IMAGE=
INSTANCE=
NETWORK=
BUNDLE=
ROW=startup

# The stable name this run gives everything it creates that carries no instance
# label of its own: the destination fixture's Compose project, the proxy, row 8's
# check container, and row 9's foreign volume. It is the first identity the bundle
# generated for this run, which is a value no other run on this host can hold.
#
# Stable rather than current: rows 9 and 10 reset the deployment and it takes a
# new identity, while the fixture and the proxy carry on. A name recomputed from
# the current identity would leave the old one owned by nothing.
RUN_IDENTITY=
# Every identity this run has finished with. Their absence was proven when they
# were replaced; teardown asks again, because a later row cannot re-establish it.
PRIOR_INSTANCES=
FIXTURE_PROJECT=

# Row 9's foreign volume, and whether this run is the thing that created it.
# Removing a name this run did not create would be this gate destroying state it
# came to prove it leaves alone.
FOREIGN_VOLUME=
FOREIGN_VOLUME_CREATED=

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
    #
    # An empty expectation is not an expectation. Two values that both failed to
    # be produced compare equal, and the row then reports a property it never
    # observed -- so the comparison refuses before it is made. Every caller's
    # expected side is a literal or a value some helper produced, and an empty one
    # is a failure upstream of here.
    [ -n "$2" ] || fail "$1, and nothing was produced to compare it against"
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
    # An absent key already exits non-zero, and a key whose value is empty prints
    # nothing and exits 0 -- which reaches a comparison as a value that equals any
    # other missing one. Both are refusals here, told apart by their messages.
    docker run --rm --network=none \
        --volume "$CANDIDATE:/candidate:ro" \
        "$IMAGE" python -c "$(printf '%s\n' \
            "import json, sys" \
            "held = json.load(open('/candidate/qualification.json'))$1" \
            "if held is None or str(held) == '':" \
            "    sys.exit('the candidate record holds an empty value where one is required')" \
            "print(held)")"
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

# ---------------------------------------------------------------------------
# Row 8's coordination: the proxy every destination write goes through
# ---------------------------------------------------------------------------
# Row 8 interrupts an actual destination write. That needs a moment in which the
# destination has completed a write and the worker does not yet know it, and there
# is exactly one such moment per write: the response on the wire. A run phase
# leaves `planned` before any byte is sent, a log line appears after the answer
# arrived, and the configuration write guard blocks before the SDK is called at
# all -- so none of them can be that moment.
#
# So the deployment is configured with an address like any other, and what is at
# that address is this gate's own reverse proxy in front of the pinned
# destination. For every row but row 8 it forwards and nothing about it shows. For
# row 8 the check arms it, and the proxy claims one mutation, forwards it, requires
# the destination to have completed it, records that durably, and withholds the
# answer while the driver kills the worker.
#
# Every state below is a file `checks/destination_proxy.py` declares. Two spellings
# of one handshake is a handshake that never completes, so these are that module's
# names and not this driver's.
PROXY_READY=proxy-ready
PROXY_ACCEPTED_AT=proxy-accepted-at
PROXY_RELEASE=proxy-release
PROXY_ACKNOWLEDGED=proxy-acknowledged
PROXY_EXPIRED=proxy-expired

PROXY_CONTROL=
PROXY_CONTAINER=
PROXY_HOST_PORT=
PROXY_URL=
ROW8_CHECK_CONTAINER=
# Two addresses, and which side gets which is the whole point. The deployment is
# given the proxy, because that is the only way a destination write of its own can
# be held. Every check that reads the destination is given the destination, because
# a proof read through the thing whose behaviour a row is arranging is no proof.
PROXY_ADDRESS=
DESTINATION_URL=
# The proxy's own port inside its container. The host port is the engine's to
# choose: a fixed one is a collision with whatever else this host publishes.
PROXY_CONTAINER_PORT=8080
# The user the candidate image runs as (`Dockerfile`: `USER 10001:10001`). A
# directory this host created is not writable by it, and the two containers that
# coordinate through row 8's control directory both run as that user.
CANDIDATE_UID=10001

# The one budget for row 8's coordination is `PROXY_BUDGET_SECONDS` in
# `checks/destination_proxy.py`, held by the proxy because the proxy is the only
# party that knows the instant it accepted the mutation. This is not a second
# budget: it is the backstop for a proxy that died without recording anything, and
# it is deliberately longer so the proxy's own expiry is what this row reports.
ROW8_ACK_TIMEOUT=30
PROXY_READY_TIMEOUT=60

container_name_taken() {
    # container_name_taken <name>
    #
    # Asked rather than answered by force. A `docker rm --force` on a name this
    # gate derived would delete a container this run did not create, on a host
    # that happens to hold one -- and every claim this gate makes rests on it
    # reaching nothing it did not make. So the name is proven free and the run
    # refuses if it is not.
    #
    # A refused query is not a free name: the question ending the run is the only
    # answer that cannot be wrong. The full name has to match, because Docker's
    # name filter is a substring.
    docker ps --all --format '{{.Names}}' --filter "name=$1" > "$WORK/name-taken" \
        || fail "this host could not be asked whether the container name $1 was free"
    grep -qxF -- "$1" "$WORK/name-taken"
}

create_proxy_control() {
    # The only writable path this gate hands a container, and it is handed to two:
    # the proxy and row 8's check. `check` mounts everything read-only, and row 2
    # asserts the deployment mounts nothing from outside its bundle.
    #
    # Made writable for the candidate image's user explicitly, and for this one
    # directory. A mode applied further up would widen what this gate gives away.
    PROXY_CONTROL=$WORK/row8-control
    rm -rf "$PROXY_CONTROL"
    mkdir -p "$PROXY_CONTROL" || fail "this host could not create the directory row 8 coordinates through"
    chmod 0700 "$PROXY_CONTROL" || fail "row 8's control directory could not be given a mode of its own"
    chown "$CANDIDATE_UID:$CANDIDATE_UID" "$PROXY_CONTROL" 2>/dev/null \
        || chmod 0777 "$PROXY_CONTROL" \
        || fail "row 8's control directory could not be made writable by the candidate image's user"
}

proxy_host_port() {
    # The engine chose it, so it is discovered. One container publishes one port
    # on both address families, which is two lines for one answer -- and two ports
    # would be two answers, so the count decides rather than the first line.
    docker port "$PROXY_CONTAINER" "$PROXY_CONTAINER_PORT/tcp" > "$WORK/proxy-port" \
        || fail "this host could not be asked which port the destination proxy was published on"
    mapped=$(sed 's/.*://' "$WORK/proxy-port" | sort -u)
    [ -n "$mapped" ] || fail "the destination proxy was published on no host port"
    published=$(printf '%s\n' "$mapped" | wc -l | tr -d ' ')
    [ "$published" -eq 1 ] \
        || fail "the destination proxy was published on $published host ports, so which one carries the traffic is undecided"
    printf '%s\n' "$mapped"
}

start_destination_proxy() {
    [ -n "$IMAGE" ] || fail "the destination proxy was started before the image that runs it was loaded"
    [ -n "$RUN_IDENTITY" ] || fail "the destination proxy was started before this run had an identity to name it with"
    PROXY_ADDRESS=$(host_address)
    DESTINATION_URL=http://$PROXY_ADDRESS:$DESTINATION_PORT
    create_proxy_control
    PROXY_CONTAINER=clean-host-proxy-$RUN_IDENTITY
    # Proven free, never cleared by force. Custody is given up again if the name
    # is taken, so teardown does not go on to reach a container this run did not
    # create -- which is the one thing this gate may never do.
    if container_name_taken "$PROXY_CONTAINER"; then
        PROXY_CONTAINER=
        fail "a container already carries the name this run would give its destination proxy"
    fi
    # Written down before the container can exist. An interrupted creation is the
    # case this is for: a `docker run` container carries none of the deployment's
    # instance labels, so an unrecorded identity is one no teardown will ever name.
    printf '%s\n' "$PROXY_CONTAINER" >> "$WORK/created"
    docker run --detach \
        --name "$PROXY_CONTAINER" \
        --publish "$PROXY_CONTAINER_PORT" \
        --volume "$CHECKS:/checks:ro" \
        --volume "$PROXY_CONTROL:/control" \
        --env "CLEAN_HOST_PROXY_UPSTREAM=$DESTINATION_URL" \
        --env "CLEAN_HOST_PROXY_PORT=$PROXY_CONTAINER_PORT" \
        "$IMAGE" python /checks/destination_proxy.py >/dev/null \
        || fail "the destination proxy the deployment's writes go through did not start"
    PROXY_HOST_PORT=$(proxy_host_port)
    PROXY_URL=http://$PROXY_ADDRESS:$PROXY_HOST_PORT
    await_proxy "$PROXY_READY" "$PROXY_READY_TIMEOUT" \
        || fail "the destination proxy never reported that it was listening, so nothing could be routed through it"
    report "every destination write this deployment makes goes through this gate's own proxy"
}

await_proxy() {
    # await_proxy <state> <seconds>. One wait, and it never restarts its bound: a
    # deadline per step obeys every individual bound and still outlives the one
    # the worker's own SDK timeout imposes.
    #
    # Answers 2 when the proxy recorded that the one budget expired. The proxy
    # holds that budget, so its record is what this reads rather than this timing
    # the same interval a second time and reporting a different reason for it.
    waited=0
    while [ ! -f "$PROXY_CONTROL/$1" ]; do
        if [ "$1" != "$PROXY_EXPIRED" ] && [ -f "$PROXY_CONTROL/$PROXY_EXPIRED" ]; then
            return 2
        fi
        if [ "$waited" -ge "$2" ]; then
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 0
}

release_proxy_hold() {
    # Idempotent, and reached from the teardown as well as from the row. A worker
    # blocked on a held response answers nothing -- not a diagnostic, and not the
    # teardown after it -- so this is the first thing the teardown does.
    [ -n "$PROXY_CONTROL" ] || return 0
    [ -d "$PROXY_CONTROL" ] || return 0
    : > "$PROXY_CONTROL/$PROXY_RELEASE" 2>/dev/null || true
}

stop_row8_containers() {
    # By exact name, because neither carries an instance label and `owned_resources`
    # therefore cannot see either. Nothing broader: a sweep of unlabelled containers
    # would reach containers this gate does not own.
    for named in $ROW8_CHECK_CONTAINER $PROXY_CONTAINER; do
        docker rm --force "$named" >/dev/null 2>&1 || true
    done
}

discard_control_state() {
    # The handshake's files, and the private directory they were the only content
    # of. Nothing in them is evidence: each one is a state that has already been
    # read by whichever side was waiting for it.
    [ -n "$PROXY_CONTROL" ] || return 0
    rm -rf "$PROXY_CONTROL" 2>/dev/null || true
}

row8_check() {
    # Row 8's check, and the second of the two containers this gate gives the
    # private control directory to. Named, because it is created by `docker run`
    # and carries no instance label, so this name is the exact identity teardown
    # removes.
    name=$1
    shift
    [ -n "$NETWORK" ] || fail "the check $name was run before the deployment network it needs existed"
    [ -n "$PROXY_CONTROL" ] || fail "the check $name was run before the directory it coordinates through existed"
    ROW8_CHECK_CONTAINER=clean-host-row8-$RUN_IDENTITY
    if container_name_taken "$ROW8_CHECK_CONTAINER"; then
        ROW8_CHECK_CONTAINER=
        fail "a container already carries the name this row would give its check"
    fi
    printf '%s\n' "$ROW8_CHECK_CONTAINER" >> "$WORK/created"
    docker run --rm \
        --name "$ROW8_CHECK_CONTAINER" \
        --network "$NETWORK" \
        --volume "$CHECKS:/checks:ro" \
        --volume "$BUNDLE/configuration:/configuration:ro" \
        --volume "$PROXY_CONTROL:/control" \
        --env-file "$WORK/check.env" \
        "$IMAGE" python "/checks/$name.py" "$@"
}

# ---------------------------------------------------------------------------
# Row 6's coordination, and the other writable channel this gate gives a check
# ---------------------------------------------------------------------------
# `busy` is a positive scheduled queue depth, and one worker with no concurrency
# limit claims everything it sees, so both halves of "a run executing with
# another queued behind it" are intervals that no poll can be relied on to catch.
# Neither is sampled. The check holds the deployment's own configuration write
# guard, which keeps its first run claimed and blocked; this driver stops the
# worker's parent process, which keeps the second run scheduled. Each side then
# waits for a file the other writes, and a written file stays written.
ROW6_CONTROL=
ROW6_WORKER=
ROW6_CHECK_PID=
ROW6_CHECK_CONTAINER=
# Waiting for the first run to be claimed happens before any of the product's
# clocks are running, so it may be generous. Everything after it happens while
# the check holds the write guard and three thirty-second clocks are counting
# from that claim -- the guard's own lock_timeout, the liveness stall threshold,
# and the live-worker freshness window.
#
# The check bounds that whole stretch with one budget of its own, and this is
# deliberately shorter than it. Giving up first means this driver resumes the
# parent and answers while the check still has budget left to notice, so a
# driver-side stall ends as this row's named timeout rather than as both sides
# expiring at once. A bound at or above the check's budget would invert that.
ROW6_SETUP_TIMEOUT=180
ROW6_HELD_TIMEOUT=12

coordinated_check() {
    # `check` mounts everything read-only, and row 2 asserts that the deployment
    # has nothing mounted from outside its bundle. This row needs a channel back,
    # so it gets one of its own -- created here, removed with the row, and handed
    # to no other check.
    name=$1
    [ -n "$NETWORK" ] || fail "the check $name was run before the deployment network it needs existed"
    ROW6_CONTROL=$WORK/row6-control
    rm -rf "$ROW6_CONTROL"
    mkdir -p "$ROW6_CONTROL" || fail "this host could not create the control directory row 6 coordinates through"
    # Writable by the user the candidate image runs as, which a directory this
    # host created is not. Row 6 is a precondition path to rows 8 to 11: a check
    # that cannot record a state the driver waits for takes the whole matrix down
    # with it, and the failure would be a filesystem error about this harness.
    chmod 0700 "$ROW6_CONTROL" || fail "row 6's control directory could not be given a mode of its own"
    chown "$CANDIDATE_UID:$CANDIDATE_UID" "$ROW6_CONTROL" 2>/dev/null \
        || chmod 0777 "$ROW6_CONTROL" \
        || fail "row 6's control directory could not be made writable by the candidate image's user"
    # Named, because this is the only check this gate backgrounds and therefore
    # the only one that could outlive its row. A `docker run` check carries none
    # of the deployment's instance labels, so `owned_resources` cannot see it and
    # teardown would report a clean host with this still running. The name is the
    # exact identity teardown removes -- nothing broader is touched.
    ROW6_CHECK_CONTAINER=clean-host-row6-$INSTANCE
    # Proven free, never cleared by force: a `docker rm --force` on a derived name
    # would delete a container this run did not create. Row 6's container is part
    # of the same global cleanup proof as row 8's, and the same rule holds for it.
    if container_name_taken "$ROW6_CHECK_CONTAINER"; then
        ROW6_CHECK_CONTAINER=
        fail "a container already carries the name this row would give its check"
    fi
    # Written down before it can exist, so teardown checks this identity for
    # absence even after a removal that could not land cleared the variable.
    printf '%s\n' "$ROW6_CHECK_CONTAINER" >> "$WORK/created"
    docker run --rm \
        --name "$ROW6_CHECK_CONTAINER" \
        --network "$NETWORK" \
        --volume "$CHECKS:/checks:ro" \
        --volume "$BUNDLE/configuration:/configuration:ro" \
        --volume "$ROW6_CONTROL:/control" \
        --env-file "$WORK/check.env" \
        "$IMAGE" python "/checks/$name.py" &
    ROW6_CHECK_PID=$!
}

stop_row6_check() {
    # Removing the container is what ends the process, and dropping its
    # PostgreSQL session is what releases the write guard if the check never got
    # to. Reached from the teardown trap, so a row that failed anywhere cannot
    # leave a container running against a deployment that is about to be removed.
    [ -n "$ROW6_CHECK_CONTAINER" ] || return 0
    docker rm --force "$ROW6_CHECK_CONTAINER" >/dev/null 2>&1 || true
    ROW6_CHECK_CONTAINER=
    if [ -n "$ROW6_CHECK_PID" ]; then
        wait "$ROW6_CHECK_PID" >/dev/null 2>&1 || true
        ROW6_CHECK_PID=
    fi
}

await_control() {
    # await_control <state> <seconds>. Waits for one state the check records, and
    # gives up when the check has finished without reaching it -- a refusing check
    # must not cost this driver its whole bound before it resumes a worker it
    # stopped.
    waited=0
    while [ ! -f "$ROW6_CONTROL/$1" ]; do
        if [ -f "$ROW6_CONTROL/done" ]; then
            return 1
        fi
        if [ "$waited" -ge "$2" ]; then
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 0
}

resume_worker() {
    # Idempotent, and reached from the teardown trap as well as from the row. Row
    # 7 restarts this deployment and row 8 kills a worker mid-write; both begin by
    # expecting one that answers, so a parent left stopped would turn one row's
    # failure into every later row's.
    #
    # Reports whether the parent is actually running again, and keeps custody of
    # it when it is not. Clearing on a failed signal would discard the only
    # identity anything holds for that container, so the trap could not try again
    # -- and the row would go on to tell the check the parent had resumed while it
    # was still stopped.
    [ -n "$ROW6_WORKER" ] || return 0
    if docker kill --signal CONT "$ROW6_WORKER" >/dev/null 2>&1; then
        ROW6_WORKER=
        return 0
    fi
    return 1
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
#
# Before the replacement, not after: every removal this gate makes is keyed on an
# identity it is holding, so whatever the old one still owned becomes a resource
# no teardown will ever name the moment this variable changes. The proof happens
# while custody is still held, and the old identity is only let go once it is a
# name nothing answers to.
reinitialise_deployment() {
    require_instance_gone "$INSTANCE" "the reset this row performed"
    PRIOR_INSTANCES="$PRIOR_INSTANCES $INSTANCE"
    compose_bundle init >/dev/null || fail "the bundle could not initialise a deployment again"
    INSTANCE=$(instance_identity)
    configure_deployment
}

# What one identity still owns, read from three queries rather than two: a network
# is created by a start and removed by a reset just as a volume is.
#
# A query this host refused to answer is not an empty answer, so each lands in a
# file whose own status decides, and a refusal puts a sentence in the list --
# which reads as something still being present, because nothing here can know
# that it is not.
instance_resources() {
    # instance_resources <identity>
    docker ps --all --quiet --filter "label=io.infrahub-sync.instance=$1" > "$WORK/instance-containers" \
        || echo "the containers $1 owns could not be listed"
    docker volume ls --quiet --filter "label=io.infrahub-sync.instance=$1" > "$WORK/instance-volumes" \
        || echo "the volumes $1 owns could not be listed"
    docker network ls --quiet --filter "label=io.infrahub-sync.instance=$1" > "$WORK/instance-networks" \
        || echo "the networks $1 owns could not be listed"
    tr -s '[:space:]' '\n' < "$WORK/instance-containers"
    tr -s '[:space:]' '\n' < "$WORK/instance-volumes"
    tr -s '[:space:]' '\n' < "$WORK/instance-networks"
}

require_instance_gone() {
    # require_instance_gone <identity> <what was supposed to have removed it>
    [ -n "$1" ] || fail "nothing named the identity $2 was supposed to have removed"
    remaining=$(instance_resources "$1")
    [ -z "$remaining" ] \
        || fail "$2 left resources this deployment owned: $(printf '%s' "$remaining" | tr '\n' ' ')"
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

# Every deployment this run started, kept whole rather than as a tail, and read
# together with what the deployment reports about its own failures.
#
# The secrets row's claim is about what the gate leaves behind, and three things
# narrow it if nothing is done: the lifecycle command's log is a bounded tail by
# design, the deployments that carried most of the matrix are destroyed by the
# ownership and replacement rows before that row runs, and its claim covers the
# product's own failure evidence and what an orchestration console shows -- which
# live in the deployment rather than in its log. Sweeping harder later cannot
# recover a container that no longer exists, so all of it is taken immediately
# before the thing that destroys it.
LOG_DIR=$WORK/logs
# The raw failure documents and orchestration logs, kept apart from the logs
# because they exist to be swept and for nothing else. Retained, they would be
# exactly the artifact row 11 exists to say this gate does not leave behind.
EVIDENCE_DIR=$WORK/failures

capture_deployment_evidence() {
    # capture_deployment_evidence <what is about to happen to this deployment>
    #
    # Fails closed, both halves. A capture that could not be taken is not a
    # shorter list of bytes to sweep: it is a claim row 11 would then be making
    # about evidence nobody ever read. Nothing reaches the terminal, because these
    # are the unredacted bytes and a terminal is not something a sweep can clear.
    mkdir -p "$LOG_DIR" "$EVIDENCE_DIR"
    INFRAHUB_SYNC_LOG_LINES=all compose_bundle logs > "$LOG_DIR/$INSTANCE-$1.log" 2>&1 \
        || fail "the whole log of this deployment could not be captured at $1, so no sweep could clear it"
    check reported_failures > "$EVIDENCE_DIR/$INSTANCE-$1.failures" 2>>"$EVIDENCE_DIR/$INSTANCE-$1.failures" \
        || fail "what this deployment reports about its own failures could not be collected at $1"
}

discard_raw_evidence() {
    # The collected failure documents, once they have been swept or once the run
    # has ended without sweeping them. Either way they do not stay on this host.
    rm -rf "$EVIDENCE_DIR" 2>/dev/null || true
}

# One snapshot of everything a restart or a repeat start must not change.
durable_snapshot() {
    check durable_state
}

# What the snapshot says one table holds. The snapshot is never empty -- it
# carries one line per table, count and all -- so its non-emptiness says nothing
# about there being state to preserve, and two empty deployments compare equal
# just as happily as two identical full ones.
snapshot_count() {
    # snapshot_count <snapshot> <table>
    printf '%s\n' "$1" | sed -n "s/^table $2 //p" | tail -1
}

# Named per row rather than generic: "some table is non-zero" drifts back to the
# same weakness the moment the schema gains a table populated for an unrelated
# reason. Each row names the thing whose survival it is actually about.
require_snapshot_holds() {
    # require_snapshot_holds <snapshot> <table> <sentence>
    held=$(snapshot_count "$1" "$2")
    case ${held:-} in
        ''|*[!0-9]*) fail "the deployment reported no $2 count at all, so $3" ;;
        0) fail "the deployment holds no $2, so $3" ;;
    esac
}

# ---------------------------------------------------------------------------
# The destination this gate plans and applies against
# ---------------------------------------------------------------------------
# The pinned fixture, started from the qualification kit. It is evidence, not
# bundle: a deployment reaches a real destination at a real address, and this one
# exists only so the managed rows have something to converge against.
destination_compose() {
    # Reported rather than fatal here. The teardown reads the fixture's log from
    # inside a grouped redirect, and `fail` exits -- which would end the run there
    # and skip the removals the rest of the teardown still has to make.
    if [ -z "$FIXTURE_PROJECT" ]; then
        echo "clean-host: $ROW: the destination fixture was reached before this run named a project for it" >&2
        return 1
    fi
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
    # Not best-effort. A fixture left running is the next run's destination,
    # already seeded by this one -- and its "empty state" would be state. Its own
    # status is what this reports, and the teardown acts on it.
    [ -n "$FIXTURE_PROJECT" ] || return 0
    destination_compose down --remove-orphans --volumes >"$WORK/destination-teardown" 2>&1
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
    IMAGE=$(docker image inspect --format '{{.Id}}' "$loaded") \
        || fail "this host could not be asked what the loaded image is"
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
    tar -xzf "$CANDIDATE/$bundle_name" -C "$EXTRACTED" \
        || fail "the deployment bundle archive could not be extracted"
    entry=$(find "$EXTRACTED" -type f -name infrahub-sync-compose | head -1)
    [ -n "$entry" ] || fail "the bundle archive holds no lifecycle entry point"
    [ -x "$entry" ] || fail "the bundle's lifecycle entry point is present but not executable"
    BUNDLE=$(dirname "$entry")
    report "the bundle extracted and its entry point arrived executable"

    compose_bundle init >/dev/null || fail "the extracted bundle could not initialise a deployment"
    INSTANCE=$(instance_identity)
    # The one identity this run keeps. Everything this gate creates that carries
    # no instance label of its own is named from it: the destination fixture's
    # Compose project, the proxy, row 8's check container, row 9's foreign volume.
    # Fixed names would collide with another run on this host, and `down
    # --volumes` on a colliding project is this gate destroying someone else's
    # deployment.
    RUN_IDENTITY=$INSTANCE
    FIXTURE_PROJECT=infrahub-sync-clean-host-destination-$RUN_IDENTITY
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
#
# What they change to is this gate's proxy rather than the destination behind it.
# An address is an address as far as the product is concerned; the difference is
# that a write to this one can be held open while it is in flight, which is what
# row 8's claim requires and what nothing else can produce.
point_configuration_at_destination() {
    base=$1
    sed "s#url: \"http://[^\"]*\"#url: \"$base\"#" \
        "$BUNDLE/configuration/qualification.yaml" > "$WORK/configuration.next"
    mv "$WORK/configuration.next" "$BUNDLE/configuration/qualification.yaml"
    grep -c "$base" "$BUNDLE/configuration/qualification.yaml" | grep -qx 2 \
        || fail "the declared configuration does not name the destination twice"
}

configure_deployment() {
    [ -n "$DESTINATION_URL" ] || fail "the deployment was configured before this host knew where the destination is"
    [ -n "$PROXY_URL" ] || fail "the deployment was configured before the proxy its writes go through existed"
    set_setting INFRAHUB_SYNC_IMAGE "$IMAGE"
    set_setting INFRAHUB_API_TOKEN "$DESTINATION_TOKEN"
    point_configuration_at_destination "$PROXY_URL"
    # The checks reach the deployment through the product's own client, which is
    # the surface an operator has. Negative destination-state assertions are read
    # from the destination, PostgreSQL, and the object store, independently of
    # that client: a client that misreads a response would otherwise confirm a
    # negative in the same direction as the bug that caused it.
    #
    # And read from the destination itself rather than through the proxy the
    # deployment is pointed at. Row 8 proves a held write landed by reading the
    # destination directly; read through the proxy, that proof would be a
    # statement about the party arranging the hold.
    cat > "$WORK/check.env" <<ENV
INFRAHUB_SYNC_API_URL=http://sync-api:8000
INFRAHUB_SYNC_API_TOKEN=$(bearer_token)
INFRAHUB_SYNC_DATABASE_URL=$(setting INFRAHUB_SYNC_DATABASE_URL)
INFRAHUB_SYNC_S3_ENDPOINT_URL=http://object-store:9000
INFRAHUB_SYNC_S3_BUCKET=$(sed -n 's/^INFRAHUB_SYNC_S3_BUCKET=//p' "$BUNDLE/defaults.conf")
INFRAHUB_SYNC_WORK_POOL=$(sed -n 's/^INFRAHUB_SYNC_WORK_POOL=//p' "$BUNDLE/defaults.conf")
AWS_ACCESS_KEY_ID=$(setting INFRAHUB_SYNC_S3_ACCESS_KEY)
AWS_SECRET_ACCESS_KEY=$(setting INFRAHUB_SYNC_S3_SECRET_KEY)
PREFECT_API_URL=http://prefect-server:4200/api
INFRAHUB_DESTINATION_URL=$DESTINATION_URL
INFRAHUB_DESTINATION_TOKEN=$DESTINATION_TOKEN
INFRAHUB_ADDRESS=$DESTINATION_URL
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
    # Before the deployment is configured, because the address it is configured
    # with is this proxy's. Every destination write the deployment makes for the
    # whole matrix goes through it; only row 8 arms it, and only then does it
    # behave as anything other than a wire.
    start_destination_proxy
    configure_deployment
    seed_destination
    refuse_a_tag_only_image

    start_deployment "the extracted bundle did not reach a ready deployment from empty state" \
        "the deployment did not report READY after a cold start"
    require_no_foreign_mount
    report "empty state reached READY, with nothing mounted from outside the bundle"

    before=$(durable_snapshot)
    # No run has happened yet, so `product_runs` is legitimately zero here. What
    # bootstrap did create is a registered configuration, and that is what a
    # repeat start has to leave alone -- without this the equality below is
    # satisfied by there having been no durable object at all.
    require_snapshot_holds "$before" configuration_versions "a repeat start changing nothing would demonstrate nothing"
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
    ROW6_WORKER=$(deployment_container sync-worker)
    coordinated_check busy_worker_stays_ready

    row6_observed=1
    if await_control executing "$ROW6_SETUP_TIMEOUT"; then
        # PID 1 only, so the flow keeps running while its parent stops claiming.
        # `docker pause` would freeze the child with the parent, and a frozen run
        # is not an executing one: the property would then hold over a deployment
        # doing nothing, which is the failure this row exists to exclude.
        #
        # Not best-effort. A signal that did not land would leave the parent
        # claiming, and the check would then submit its second run and assert the
        # property against a deployment nothing was holding open. Only the resume
        # in the trap may be best-effort, because there the alternative is worse.
        docker kill --signal STOP "$ROW6_WORKER" >/dev/null 2>&1 \
            || fail "the deployment's worker parent could not be stopped, so no queue could form behind it"
        : > "$ROW6_CONTROL/stopped"
        if await_control observed "$ROW6_HELD_TIMEOUT"; then
            row6_observed=0
        else
            row6_observed=1
        fi
        # Whatever happened above, in this order. A check still waiting on this
        # state would spend its own bound holding the write guard, and the
        # product's thirty-second clocks would reach its first run and replace a
        # coordination failure with a stalled or contended run.
        #
        # Required, and required before the answer. Telling the check the parent
        # resumed when it did not would release its guard against a deployment
        # that can still not finish either run, and it would then hang in
        # settlement rather than report anything.
        resume_worker \
            || fail "the deployment's worker parent could not be resumed, so neither of this row's runs could finish"
        if [ -d "$ROW6_CONTROL" ]; then
            : > "$ROW6_CONTROL/resumed"
        fi
    fi
    # Before the verdict is read: the check cannot finish while the parent it is
    # waiting on is stopped. Best-effort here only because the check's own
    # sentence is about to be read and the trap still holds custody to retry.
    resume_worker || true
    if wait "$ROW6_CHECK_PID"; then
        row6_verdict=0
    else
        row6_verdict=1
    fi
    ROW6_CHECK_PID=
    stop_row6_check
    # The check's own sentence has already reached the log, so the row reports the
    # row. A driver-side timeout is named only when the check did not fail, which
    # is the one case its stderr says nothing about.
    [ "$row6_verdict" -eq 0 ] || fail "a busy worker did not leave the deployment READY"
    [ "$row6_observed" -eq 0 ] \
        || fail "the check never reported reading the deployment's status while its worker parent was stopped"
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
# How long a replacement worker is given to register under a name of its own. The
# figure the Compose lifecycle gate proved: the departing worker's record lingers
# ONLINE for a while, so what is waited for is a name that was not there before
# rather than a change in how many there are.
WORKER_REPLACEMENT_SECONDS=180

# The identity a restart replaces is the Prefect worker name, not the container.
# `restart` runs `docker compose restart sync-api sync-worker`, which restarts the
# process inside the container it already has -- so a container identity that
# changed would mean the product had stopped doing what `restart` means.
wait_for_replacement_worker() {
    # wait_for_replacement_worker <file holding the names seen before>
    waited=0
    while [ "$waited" -lt "$WORKER_REPLACEMENT_SECONDS" ]; do
        check worker_identity > "$WORK/workers-after" 2>/dev/null || : > "$WORK/workers-after"
        if grep -vxF -f "$1" "$WORK/workers-after" | grep -q .; then
            return 0
        fi
        sleep 5
        waited=$((waited + 5))
    done
    return 1
}

row_restart() {
    check worker_identity > "$WORK/workers-before" \
        || fail "the deployment's own Prefect server could not be asked which workers are online"
    # A restart replacing nothing would demonstrate nothing, and an empty set
    # before it makes any name afterwards look like a replacement.
    [ -s "$WORK/workers-before" ] \
        || fail "the deployment reported no online worker before a restart, so there was none to replace"
    before_state=$(durable_snapshot)
    require_snapshot_holds "$before_state" product_runs "a restart preserving it would demonstrate nothing"

    compose_bundle restart >/dev/null || fail "the deployment could not be restarted"
    require "the deployment did not return to READY after a restart" READY "$(deployment_status)"

    wait_for_replacement_worker "$WORK/workers-before" \
        || fail "no worker registered under a new identity after a restart; see $WORK/workers-before and $WORK/workers-after"
    require "a restart changed a durable record" "$before_state" "$(durable_snapshot)"
    report "the worker returned under a new identity while every durable record stayed equal"
}

# ---------------------------------------------------------------------------
# Row 8 — recovery
# ---------------------------------------------------------------------------
row_recovery() {
    # The interruption is a host action, and what makes it mean anything is that a
    # real destination write is in flight when it lands. So the write is held open
    # rather than looked for: the check arms this gate's proxy, the proxy claims
    # the run's mutation, forwards it to the pinned destination, requires the
    # destination to have completed it, and withholds the answer. The check then
    # reads the destination itself and proves the planted value is there before it
    # names the run -- naming the run is this driver's licence to kill a worker.
    row8_check start_apply > "$WORK/interrupted" \
        || fail "no confirmed write reached the destination with its answer held, so interrupting it would mean nothing"
    interrupted=$(tail -1 "$WORK/interrupted")
    [ -n "$interrupted" ] || fail "the interrupted run was never named"

    # Killed while the answer is still held, so the write happened and the worker
    # never learned its outcome. The exact container this deployment owns, and
    # required rather than best-effort: a signal that did not land would leave the
    # worker to receive the released answer, and the row would then read a run
    # that completed normally as an ambiguous one.
    worker=$(deployment_container sync-worker)
    docker kill "$worker" >/dev/null \
        || fail "the worker holding the destination write could not be killed, so no write was interrupted"

    # Released and disarmed in one step, because the proxy carries on serving the
    # whole matrix: an arm that survived its claim would hold the recovery checks'
    # own traffic below. One wait, bounded once, and the proxy's own record of the
    # budget expiring is what this reads rather than a second clock over it.
    : > "$PROXY_CONTROL/$PROXY_RELEASE"
    # Not a bare call: a non-zero status from it is this row's answer rather than
    # its failure, and errexit would end the run before the answer was read.
    acknowledged=0
    await_proxy "$PROXY_ACKNOWLEDGED" "$ROW8_ACK_TIMEOUT" || acknowledged=$?
    [ "$acknowledged" -ne 2 ] \
        || fail "the coordination budget that began when the destination proxy accepted this run's mutation ran out"
    [ "$acknowledged" -eq 0 ] \
        || fail "the destination proxy never acknowledged releasing and disarming the write it was holding"

    start_deployment "the deployment did not come back after its worker was killed" \
        "the deployment did not return to READY after its worker was killed"
    check recovery "$interrupted" \
        || fail "an interrupted write did not leave durable reconciliation state a fresh plan could follow"
    report "an interrupted write was not retried, and a fresh plan followed its durable state"
}

# ---------------------------------------------------------------------------
# Row 9 — ownership and reset
# ---------------------------------------------------------------------------
create_foreign_volume() {
    # Named for the identity this deployment generated, not for a constant. Row 9's
    # claim is that a reset leaves what it does not own alone, and a fixed name is
    # some other run's volume or an operator's -- its survival would then be a
    # statement about a volume this run never made, and teardown would remove it.
    #
    # Absence first. A name already taken makes the survival of that name prove
    # nothing, and the creation would silently be a no-op.
    FOREIGN_VOLUME=infrahub-sync-clean-host-foreign-$INSTANCE
    if docker volume inspect "$FOREIGN_VOLUME" >/dev/null 2>&1; then
        FOREIGN_VOLUME=
        fail "a volume already carries the name this row would create, so surviving a reset would prove nothing"
    fi
    docker volume create "$FOREIGN_VOLUME" >/dev/null \
        || fail "the foreign volume this row proves a reset leaves alone could not be created"
    # Recorded, because it is what licenses the removal. Nothing removes this name
    # without this run having been the thing that created it.
    FOREIGN_VOLUME_CREATED=yes
    docker volume inspect "$FOREIGN_VOLUME" >/dev/null || fail "the foreign volume was not created"
}

remove_foreign_volume() {
    # Only what this run created, and reached from the teardown as well as from
    # the row. Removing a name this run did not create would be this gate
    # destroying the very kind of foreign state it came to prove it preserves.
    [ -n "$FOREIGN_VOLUME_CREATED" ] || return 0
    docker volume rm "$FOREIGN_VOLUME" >/dev/null 2>&1 || return 1
    FOREIGN_VOLUME_CREATED=
    return 0
}

row_ownership_and_reset() {
    create_foreign_volume

    # A reset that did not have to repeat the exact identity would be a reset
    # anyone could run against a deployment they had not read the name of.
    if compose_bundle reset "not-this-instance" >"$WORK/reset-refusal" 2>&1; then
        fail "the deployment reset without being given its own identity"
    fi
    grep -q "confirmation-required" "$WORK/reset-refusal" \
        || fail "the deployment refused the reset, but not for the identity it was given"
    report "a reset without the exact instance identity is refused"

    capture_deployment_evidence before-reset
    compose_bundle reset "$INSTANCE" >/dev/null || fail "the deployment could not be reset"
    docker volume inspect "$FOREIGN_VOLUME" >/dev/null \
        || fail "the reset removed a volume this deployment did not own"
    docker ps --all --quiet --filter "label=io.infrahub-sync.instance=$INSTANCE" > "$WORK/after-reset" \
        || fail "this host could not be asked what the reset left behind"
    [ ! -s "$WORK/after-reset" ] || fail "the reset left a container this deployment owned"
    report "the reset removed only what this instance owned, and the foreign volume survived"

    reinitialise_deployment
    start_deployment "the deployment did not start again after a reset" \
        "the deployment did not return to READY after a reset"
    check cold_bootstrap || fail "the start after a reset was not a cold bootstrap"
    report "the next start after a reset is cold"

    remove_foreign_volume || fail "the foreign volume this row created could not be removed"
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
    require_snapshot_holds "$before" product_runs "there is nothing for a replacement to replace"

    capture_deployment_evidence before-replacement
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

    # The deployment that is still running, read the same way the destroyed ones
    # were: its whole log, its own failure evidence, and what its Prefect server
    # shows for the executions behind it.
    capture_deployment_evidence final
    docker image history --no-trunc --format '{{.CreatedBy}}' "$IMAGE" > "$WORK/image.history"

    bundle_name=$(record "['bundle']['name']") || fail "the candidate record names no bundle to sweep"
    # The shipped bytes, not the compressed container of them: a plaintext search
    # of a gzip stream cannot match, so it would report success without looking.
    gzip -dc "$CANDIDATE/$bundle_name" > "$WORK/bundle.tar" \
        || fail "the deployment bundle could not be decompressed to be swept"
    # A glob that matched nothing expands to itself, and a file that is not there
    # clears exactly as much as a clean one: nothing. So each target has to be
    # there before it can be read, and an absent one ends the row.
    for target in "$WORK/image.history" "$WORK/bundle.tar" "$LOG_DIR"/*.log "$EVIDENCE_DIR"/*.failures; do
        if [ ! -f "$target" ]; then
            discard_raw_evidence
            fail "this run collected no $(basename "$target") to sweep, so the claim would cover bytes nobody read"
        fi
        if carries_a_canary "$target" "$WORK/canaries"; then
            discard_raw_evidence
            fail "a credential this run generated reached $(basename "$target")"
        fi
    done
    # The kit is evidence, and evidence is named by the contract too. Its own
    # working directory is excluded: that is where this sweep keeps the list.
    while read -r canary; do
        [ -n "$canary" ] || continue
        if grep -rqF --exclude-dir=work -- "$canary" "$KIT" 2>/dev/null; then
            discard_raw_evidence
            fail "a credential this run generated reached the qualification kit"
        fi
    done < "$WORK/canaries"
    # Swept, and now gone. These are the unredacted failure documents and the
    # orchestration logs behind them; they existed to be searched and a retained
    # copy of them would be the artifact this row says the gate does not leave.
    discard_raw_evidence
    report "no credential this run generated reached the bundle, image history, any deployment's whole log, its recorded failures, what its orchestration shows, or the kit"
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
# Not only the deployment's own labelled containers and volumes. This run also
# creates a network with every start, two containers that carry no instance label
# at all, a published host port, a Compose project for the destination fixture,
# and a volume it named itself -- and it discards an identity every time a row
# resets. Each of those is a resource, and a completeness verdict that did not ask
# about it is a clean host reported without looking.
remaining_resources() {
    for identity in $PRIOR_INSTANCES $INSTANCE; do
        instance_resources "$identity"
    done
    # The containers this gate creates with `docker run`: the proxy, row 8's check,
    # and row 6's backgrounded one. No label makes any of them visible above, so
    # each is asked about by its exact name -- an unlabelled-container sweep would
    # reach containers this gate does not own.
    #
    # Read from the record each creator wrote before it created anything, not from
    # the variables that hold them: a removal that could not land clears its
    # variable and the identity would then be checked by nobody. The full name has
    # to match, because Docker's name filter is a substring.
    if [ -f "$WORK/created" ]; then
        while read -r named; do
            [ -n "$named" ] || continue
            docker ps --all --format '{{.Names}}' --filter "name=$named" > "$WORK/tracked-containers" \
                || echo "this host could not be asked whether $named is still present"
            if grep -qxF -- "$named" "$WORK/tracked-containers" 2>/dev/null; then
                echo "the container $named this run created is still present"
            fi
        done < "$WORK/created"
    fi
    if [ -n "$FOREIGN_VOLUME_CREATED" ]; then
        echo "the volume $FOREIGN_VOLUME this run created is still present"
    fi
    if [ -n "$PROXY_HOST_PORT" ]; then
        docker ps --all --quiet --filter "publish=$PROXY_HOST_PORT" > "$WORK/tracked-port" \
            || echo "this host could not be asked whether the proxy's published port was given back"
        tr -s '[:space:]' '\n' < "$WORK/tracked-port"
    fi
    if [ -n "$FIXTURE_PROJECT" ]; then
        docker ps --all --quiet --filter "label=com.docker.compose.project=$FIXTURE_PROJECT" > "$WORK/fixture-containers" \
            || echo "the destination fixture's containers could not be listed"
        docker volume ls --quiet --filter "label=com.docker.compose.project=$FIXTURE_PROJECT" > "$WORK/fixture-volumes" \
            || echo "the destination fixture's volumes could not be listed"
        docker network ls --quiet --filter "label=com.docker.compose.project=$FIXTURE_PROJECT" > "$WORK/fixture-networks" \
            || echo "the destination fixture's networks could not be listed"
        tr -s '[:space:]' '\n' < "$WORK/fixture-containers"
        tr -s '[:space:]' '\n' < "$WORK/fixture-volumes"
        tr -s '[:space:]' '\n' < "$WORK/fixture-networks"
    fi
}

# The failure that caused the exit is the one re-raised: teardown never replaces
# a diagnosis. What it could not remove is reported, because a deployment left
# running is the next run's "empty state" -- and a run that finished cleanly and
# then failed to tear down has not left the host as it found it either.
# Run once. The signal handler below ends the run by exiting, which runs this trap
# in turn, so without a guard the second entry would read its own `$?` and answer
# for the run.
CLEANED=

cleanup() {
    status=$?
    [ -z "$CLEANED" ] || exit "$status"
    CLEANED=1
    failed_row=$ROW
    ROW=teardown
    # First, and in this order, because each unblocks the next. A worker holding a
    # response this gate withheld is stopped inside an HTTP call and answers
    # nothing; a worker whose parent is stopped answers nothing either -- not the
    # diagnostic below, and not the teardown after it.
    #
    # Neither is destructive, so both come before the account is taken.
    release_proxy_hold
    resume_worker || true
    stop_row6_check
    # Before anything is removed, because after it there is nothing to read.
    if [ "$status" -ne 0 ]; then
        capture_diagnostic "$failed_row"
    fi
    # The raw failure documents, whether or not row 11 reached them. Unswept they
    # are the artifact that row exists to say this gate does not leave behind.
    discard_raw_evidence
    held=$(remaining_resources | wc -w | tr -d ' ')
    # Row 8's two containers, and then the handshake's own files. Both by exact
    # name: neither container carries an instance label, so nothing below can see
    # them and a host with them still running is not the host this gate found.
    stop_row8_containers
    discard_control_state
    if [ -n "$INSTANCE" ]; then
        # The entry point first, because it is the operator path. Its own project,
        # by exact identity, is the fallback for a reset it refuses -- after a row
        # has already removed the instance state the reset needs.
        compose_bundle reset "$INSTANCE" >"$WORK/teardown" 2>&1 \
            || docker compose --project-name "infrahub-sync-$INSTANCE" \
                down --volumes --remove-orphans >>"$WORK/teardown" 2>&1 \
            || true
    fi
    # Not suppressed. A fixture left running is the next run's destination, and
    # this run seeded it -- so its "empty state" would be state. A cleanup defect
    # may turn a passing run into a failure; it may never replace one.
    if ! stop_destination; then
        echo "clean-host: teardown: the destination fixture could not be stopped; see $WORK/destination-teardown" >&2
        [ "$status" -ne 0 ] || status=1
    fi
    if ! remove_foreign_volume; then
        echo "clean-host: teardown: the volume this run created beside the deployment could not be removed" >&2
        [ "$status" -ne 0 ] || status=1
    fi
    # Complete or incomplete, said out loud either way: a silent teardown and one
    # that removed nothing read the same, and that is how a deployment left
    # running becomes the next run's empty state.
    if [ -n "$(remaining_resources)" ]; then
        echo "clean-host: teardown: incomplete: resources this run owns are still present; see $WORK/teardown" >&2
        [ "$status" -ne 0 ] || status=1
    else
        report "complete: the $held resources this run owned are gone, and the destination fixture with them"
    fi
    exit "$status"
}

# Separated from the exit trap, because that trap reads the status of whatever ran
# last. On an interrupt that is routinely zero, and a gate that was killed part of
# the way through its matrix would report having passed it. A signal ends this run
# with a status of its own, and the exit trap then preserves it.
on_signal() {
    # on_signal <status>
    ROW=${ROW:-startup}
    echo "clean-host: $ROW: this run was signalled and did not finish its matrix" >&2
    exit "$1"
}

main() {
    mkdir -p "$WORK"
    : > "$WORK/host-tool-used"
    install_refusing_shims
    trap cleanup EXIT
    trap 'on_signal 130' INT
    trap 'on_signal 143' TERM

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
