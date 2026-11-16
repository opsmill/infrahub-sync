# 43. The Kubernetes deployment orders its start with a hashed Job and gates

**Status**: Accepted
**Date**: 2026-11-16
**Source**: dev/specs/018-helm-chart-kubernetes/research.md (R-2, R-3); verification.md (T018, T021)

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

- The Sync API and the worker do not serve before the bootstrap has created the Prefect
  deployment.
- A pod restart does not run the bootstrap.
- An upgrade that changes what the bootstrap reads runs it again, without a manual step.
- An upgrade works when a tool applies the output of `helm template` instead of Helm itself.

The bootstrap also decides whether the bundled object store runs by resolving the absolute DNS
name `object-store.` (`infrahub_sync/service/bootstrap.py`). That name never resolves in
Kubernetes.

## Decision

The chart orders the start with the first three mechanisms below. The fourth keeps the
bootstrap's object store check correct. None of them needs access to the Kubernetes API.

- **Database bootstrap.** The bundled PostgreSQL runs the role and database script of the Compose
  file from `/docker-entrypoint-initdb.d`, mounted from a ConfigMap with mode `0555`, when it
  first initializes its volume. The PostgreSQL pod is not ready until the script has run. The
  Prefect server waits for PostgreSQL with `pg_isready` in an init container.
- **Service bootstrap.** A plain Kubernetes Job, not a Helm hook, named
  `<fullname>-bootstrap-<hash>`. The hash is the first 8 characters of a SHA-256 over every input
  of the Job's pod:
    - the image reference and the environment the bootstrap receives
    - the checksums of the Secret and the CA ConfigMap that the chart creates
    - the `bootstrap` values, the pull policy and pull secrets, the security contexts, the service
      account, the CA ConfigMap name and the wait timeout
    - the chart version

  A change to any input gives a new name, so the upgrade creates a new Job and Helm deletes the
  Job of the previous name. No Job is ever patched. An upgrade that changes no input keeps the
  completed Job; on a converged deployment the bootstrap would change nothing. The Job waits for
  the Prefect server's `/api/health` in an init container and retries on failure.
- **Gate on the Sync API and the worker.** Each has an init container, from the Sync image, that
  reads the Prefect deployment `infrahub-sync-service/run` with the Prefect client and waits until
  it exists in the configured work pool. The bootstrap creates that deployment after the bucket
  and the work pool, so its presence means those steps completed. The Sync API and the worker
  create the product schema themselves at start, so they do not wait for that step. A pod restart
  runs the init container again, which only reads.
- **Object store name.** The bundled object store's Service is named `<fullname>-object-store`,
  never `object-store`. The chart enforces at render time, with `fail`, that `s3.endpointUrl` is
  empty when `objectStore.enabled` is true and set when it is false, because the bootstrap's own
  DNS check cannot tell the two cases apart in Kubernetes.

## Consequences

- **Install.** On a first install the Sync API and the worker are not ready until the bootstrap
  has created the Prefect deployment. With Helm 4.2.4, `helm install --wait` alone does not wait
  for a Job; the documentation tells operators to pass `--wait --wait-for-jobs`, which returns
  after the Job completes.
- **Upgrade gap.** On an upgrade the Prefect deployment of the previous release already exists,
  so the gate passes at once. The Sync API and the worker of the new release can start before the
  new bootstrap Job completes. The Job still completes without a manual step, and
  `helm upgrade --wait --wait-for-jobs` returns only after it does. An exact gate needs the Sync
  image to report that the bootstrap of its own version has run; the image has no such check, so
  this is a follow-up.
- **Rendered output.** Because the name does not use `.Release.Revision`, a tool that applies
  `helm template` output still gets a new Job for a changed image, and no Job has to be deleted by
  hand.
- **Existing Secrets.** The chart cannot read a Secret it did not create, so a change to the
  content of `credentials.existingSecret` creates no Job and restarts no pod. The operator
  restarts the pods.
- **Passwords.** The database script runs only on an empty volume. A role password changed in
  the values later must be changed in the database too, as with Compose.
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
- **The database bootstrap as a Job, as in Compose.** Rejected: the Prefect server would need a
  second gate. The cost of the init script is that it runs only on an empty volume, which the
  documentation states.
- **A Job named after `.Release.Revision`.** Rejected. A tool that applies `helm template` output
  renders the revision as 1 every time, so the Job would keep its name, receive a new pod template
  after an image change, and be refused because a Job's pod template cannot be changed.
