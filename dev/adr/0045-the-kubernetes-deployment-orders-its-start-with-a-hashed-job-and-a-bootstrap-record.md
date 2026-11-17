# 45. The Kubernetes deployment orders its start with a hashed Job and a bootstrap record

**Status**: Accepted
**Date**: 2026-11-16
**Source**: dev/specs/018-helm-chart-kubernetes/spec.md (FR-007, FR-007b); research.md (R-2, R-3);
verification.md (T018, T021, T046 and T047)

## Context

The `infrahub-sync` Helm chart, in the `opsmill/infrahub-helm` repository, deploys the services of
the root `docker-compose.yml` ([ADR 16](0016-the-compose-deployment-is-one-tag-pinned-file.md))
into one Kubernetes namespace. Compose orders the start with `depends_on` conditions: a
short-lived `db-bootstrap` service creates the two databases and their roles, a short-lived
`sync-bootstrap` service creates the bucket, the Prefect work pool, the Prefect deployment and the
product schema, and the Sync API and the worker start only after `sync-bootstrap` completed.
Compose also rules out a bootstrap at every start, because that "would make every restart a
write" (`docker-compose.yml`).

Kubernetes has no start dependency between resources, and the chart has to give the same
guarantees:

- The Sync API and the worker of a Sync version do not serve before the bootstrap has run for that
  version and work pool, on an install and on an upgrade. An upgrade at the same version that
  changes another bootstrap setting, such as the bucket, is the exception described under
  "Upgrade at the same version" in the consequences.
- A pod restart does not run the bootstrap.
- An upgrade that changes what the bootstrap reads runs it again, without a manual step.
- An upgrade works when a tool applies the output of `helm template` instead of Helm itself.

The bootstrap also checks whether the bundled object store runs by resolving the absolute DNS
name `object-store.` (`infrahub_sync/service/bootstrap.py`). That name never resolves in
Kubernetes.

## Decision

The start is ordered by the first three mechanisms below. The third is in Infrahub Sync itself
and applies to every 3.x deployment, the Compose deployment included. The fourth keeps the
bootstrap's object store check correct. None of them needs access to the Kubernetes API.

- **Database bootstrap.** The bundled PostgreSQL runs the role and database script of the Compose
  file from `/docker-entrypoint-initdb.d`, mounted from a ConfigMap with mode `0555`, when it
  first initializes its volume. The bootstrap Job runs the same script again over TCP as the
  administrator, in an `ensure-databases` init container, as the Compose `db-bootstrap` service
  does on every start. The script creates a role or a database only when it is missing, so a
  renamed role or database, or a first start that was interrupted after the volume was
  initialized, is repaired by the next Job. The Prefect server waits for PostgreSQL with
  `pg_isready` in an init container, and restarts until its role exists.
- **Service bootstrap.** A plain Kubernetes Job, not a Helm hook, named
  `<fullname>-bootstrap-<hash>`. The hash is the first 8 characters of a SHA-256 over every input
  of the Job's pod:
    - the image reference and the environment the bootstrap receives
    - the checksums of the Secret and the CA ConfigMap that the chart creates
    - the `bootstrap` values, the pull policy and pull secrets, the security contexts, the service
      account, the CA ConfigMap name and the wait timeout
    - the init container resources, whether the bundled object store runs, and the PostgreSQL
      image, roles and databases
    - the chart version

  A change to any input gives a new name, so the upgrade creates a new Job and Helm deletes the
  Job of the previous name. No Job is ever patched. An upgrade that changes no input keeps the
  completed Job; on a converged deployment the bootstrap would change nothing. Its init
  containers wait for PostgreSQL, create the missing roles and databases, wait for the bundled
  object store's `/minio/health/ready`, and wait for the Prefect server's `/api/health`, in that
  order. The Job retries on failure.
- **Bootstrap record and start check.** As its last step, the bootstrap writes a one-row table
  `bootstrap_record` in the product database with the Sync version and the work pool name it set
  up. A repeat for the same version and pool writes nothing. The Sync API and the worker read the
  record when they start, before the product schema step. When it is missing or names another
  version or pool, they log what they found and what they expected, without any credential, and
  exit with code 1. Kubernetes restarts them with back-off until the Job has written the record.
  The Sync API and the worker have no init container. A pod restart runs the check again, which
  only reads.
- **Object store name.** The bundled object store's Service is named `<fullname>-object-store`,
  never `object-store`. The chart enforces at render time, with `fail`, that `s3.endpointUrl` is
  empty when `objectStore.enabled` is true and set when it is false, because the bootstrap's own
  DNS check cannot tell the two cases apart in Kubernetes.

The record covers the Sync version and the work pool name, and no other bootstrap setting, such
as the bucket. A change to such a setting at the same version gives the Job a new name, so the
Job runs again, but the start check already passes.

## Consequences

- **Restarts are expected.** On a first install the Sync API and the worker exit and restart until
  the Job has written the record. Nothing makes them wait for PostgreSQL, so their restarts also
  include failures to reach the product database while PostgreSQL starts. Measured once on
  minikube: the Sync API restarted 3 times and was ready 23 seconds after the Job completed; the
  worker restarted 2 times. With Helm 4.2.4, `helm install --wait` alone does not wait for a Job;
  the documentation tells operators to pass `--wait --wait-for-jobs`, which returns after the Job
  completes.
- **Upgrade to another Sync version.** The new Sync API pod refuses to start until the new Job has
  written the record, and the previous pod keeps serving meanwhile. Measured once on minikube: the
  new pod restarted 2 times and was ready 5 seconds after the new Job completed.
- **Upgrade at the same version.** When an upgrade changes a bootstrap setting other than the
  version and the work pool, the new pods can start before the new Job completes. The Job still
  completes without a manual step, and `helm upgrade --wait --wait-for-jobs` returns only after it
  does.
- **Minimum Sync version.** The chart has no start ordering of its own for the Sync API and the
  worker, so it needs a Sync release that contains the start check.
- **Rendered output.** Because the name does not use `.Release.Revision`, a tool that applies
  `helm template` output still gets a new Job for a changed image, and no Job has to be deleted by
  hand.
- **Existing Secrets.** The chart cannot read a Secret it did not create, so a change to the
  content of `credentials.existingSecret` creates no Job and restarts no pod. The operator
  restarts the pods.
- **Passwords.** The database script creates a missing role with its password and leaves an
  existing role unchanged. A role password changed in the values later must be changed in the
  database too, as with Compose.
- **No Role.** No pod mounts a service account token, and the chart creates no Role or
  RoleBinding.

## Alternatives Considered

- **A Helm hook (`post-install,pre-upgrade`), as the `infrahub` chart uses for its upgrade Job.**
  Rejected. On install the Sync API and the worker serve before a `post-install` hook runs. On
  upgrade a `pre-upgrade` hook runs before Helm applies the new Secret and ConfigMap, so it runs
  with the previous credentials. A hook is not part of the release, so `helm uninstall` and
  `helm rollback` do not manage it.
- **The bootstrap as an init container of the Sync API and the worker.** Rejected: it runs at
  every restart, which the Compose file rules out.
- **An init container that waits for the Job through the Kubernetes API.** Rejected: it needs a
  Role, a RoleBinding, a service account token in the pod and a `kubectl` image.
- **The database bootstrap as a separate Job, as in Compose.** Rejected: the Prefect server would
  need a second gate. The bootstrap Job's `ensure-databases` init container runs the script
  instead, and the Prefect server restarts until its role exists. The PostgreSQL entrypoint alone
  was the first choice; it runs the script only on an empty volume, so a renamed role or an
  interrupted first start left the roles missing.
- **An init container on the Sync API and the worker that waits for the Prefect deployment**
  `infrahub-sync-service/run` in the configured work pool. Rejected after it was first built: on
  an upgrade the deployment of the previous release already exists, so the wait ends at once and
  the new pods can start before the new Job completes.
- **A Job named after `.Release.Revision`.** Rejected. A tool that applies `helm template` output
  renders the revision as 1 every time, so the Job would keep its name, receive a new pod template
  after an image change, and be refused because a Job's pod template cannot be changed.
