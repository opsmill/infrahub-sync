Fixed the service worker refusing a flow run that arrived while a routine identity
heartbeat was in progress. Prefect has already proposed `Submitting` by the time the
worker starts the child, so a refusal in that window marked the run `Crashed` for a worker
replacement that never happened — a submission valid immediately before the heartbeat and
valid immediately after it was rejected during it, with no wait and no recheck. A
submission that meets a refresh now waits for it on the lock the refresh already holds,
then revalidates against the identity the refresh left. Refusal is now reserved for an
identity that really did change: no resolved record, a moved identity generation, or a
child environment naming a different worker.
