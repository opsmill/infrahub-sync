# Verification on a local cluster

## T018: install the chart and call the Sync API

Result: every check of [quickstart.md](quickstart.md) steps 2 and 3 passed on the first install.
The cluster run found no defect in the chart templates, so no template changed.

### Environment

- Cluster: minikube v1.39.0, profile and kube context `sync-chart`, Kubernetes v1.37.0, docker
  driver, 4 CPUs, 6 GB of memory, arm64 host.
- Helm v4.2.4.
- Chart: `H/charts/infrahub-sync` at commit `e06ee6d` of `H`, chart version 0.1.0, appVersion
  3.0.0a5.
- Image: `infrahub-sync:chart-test`, built from `S` (T003), image ID
  `sha256:d66282cf76a806e7fb339d200e53dedb314d76a48529113db2a0b27e1a37e50f`, loaded into the cluster
  with `minikube image load`. The registry image of the chart's appVersion is not published, so the
  install uses the local image with `image.pullPolicy=Never`.

### Commands

```bash
minikube start --profile sync-chart --cpus 4 --memory 6g
minikube -p sync-chart image load infrahub-sync:chart-test

# The env file is outside every repository. It holds the seven required keys of
# data-model.md: random hex passwords and S3 keys, a Prefect auth string `prefect:<hex>`, and
# INFRAHUB_SYNC_SERVICE_BEARER_TOKENS={"operator": {"token": "<32 hex characters>", "administrator": true}}
kubectl --context sync-chart create namespace sync
kubectl --context sync-chart -n sync create secret generic infrahub-sync --from-env-file=<scratch env file>

helm --kube-context sync-chart install sync H/charts/infrahub-sync -n sync \
  --set credentials.existingSecret=infrahub-sync \
  --set image.repository=infrahub-sync --set image.tag=chart-test --set image.pullPolicy=Never \
  --wait --timeout 10m

kubectl --context sync-chart -n sync wait --for=condition=complete job -l app.kubernetes.io/component=bootstrap
kubectl --context sync-chart -n sync wait --for=condition=Ready pod -l 'app.kubernetes.io/component!=bootstrap'

kubectl --context sync-chart -n sync port-forward svc/sync-infrahub-sync-api 8001:8000
curl -fsS http://127.0.0.1:8001/version
curl -fsS http://127.0.0.1:8001/status
INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001 INFRAHUB_SYNC_API_TOKEN=<token> \
  uv run --directory S infrahub-sync whoami
```

### Timeline of the install

All times are UTC, read from the Kubernetes objects of the release.

| Time | Event |
| --- | --- |
| 2026-11-16T11:32:51Z | `helm install` starts; the bootstrap Job is created and starts |
| 2026-11-16T11:33:15Z | object store pod ready |
| 2026-11-16T11:33:16Z | PostgreSQL pod ready |
| 2026-11-16T11:33:27Z | Prefect server pod ready |
| 2026-11-16T11:33:29Z | bootstrap container logs its last line |
| 2026-11-16T11:33:31Z | bootstrap Job complete (`completionTime`) |
| 2026-11-16T11:33:32Z | worker pod ready |
| 2026-11-16T11:33:37Z | Sync API pod ready; `helm install` returns `STATUS: deployed`, revision 1 |

Time from `helm install` to every pod ready: 46 seconds.

### How Helm 4 `--wait` treats the bootstrap Job

`helm install --wait` in Helm 4 uses the `watcher` strategy and does not wait for a Job to
complete. It waits for a Job only with `--wait-for-jobs`.

In this install, Helm returned after the Job completed only because of the chart's start order:
the init containers of the Sync API and the worker wait until the bootstrap has created the
deployment `infrahub-sync-service/run`, and `--wait` waits for those pods to be ready. The Job's
own completion is not what Helm waited for. This holds for a first install only. On
`helm upgrade` the deployment already exists in the work pool, so the Sync API and worker pods can
be ready before the new Job completes. That case is not verified here; T021 covers it.

A separate test isolated the behavior. A chart with one Job that runs `sleep 45` was installed on
the same cluster, in a new namespace each time, at 2026-11-16T11:34:10Z:

| Flags | `helm install` returned | Job `completionTime` |
| --- | --- | --- |
| `--wait` | 11:34:10, at once | not set when Helm returned |
| `--wait --wait-for-jobs` | 11:34:59 | 11:34:59 |
| `--wait=legacy --wait-for-jobs` | 11:35:49 | 11:35:47 |

The test chart has only `Chart.yaml` (`name: waittest`, `version: 0.1.0`) and this Job:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: sleeper
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: s
          image: infrahub-sync:chart-test
          imagePullPolicy: Never
          command: ["sh", "-c", "sleep 45"]
```

The three installs, each followed by `helm uninstall` and the deletion of its namespace:

```bash
helm --kube-context sync-chart install wt <test chart> -n wta --create-namespace --wait --timeout 3m
helm --kube-context sync-chart install wt <test chart> -n wtb --create-namespace --wait --wait-for-jobs --timeout 3m
helm --kube-context sync-chart install wt <test chart> -n wtc --create-namespace --wait=legacy --wait-for-jobs --timeout 3m
```

`helm install --help` of Helm v4.2.4 states that `--wait` alone selects `watcher`, that the
default without the flag is `hookOnly`, and that `--wait-for-jobs` waits "until all Jobs have
been completed".

### Pod list and Job status

At 2026-11-16T11:36:18Z, context `sync-chart`:

```text
NAME                                                     READY   STATUS      RESTARTS   AGE
pod/sync-infrahub-sync-api-7b8876d74f-n8mtj              1/1     Running     0          3m27s
pod/sync-infrahub-sync-bootstrap-56dccc4a-5tqpn          0/1     Completed   0          3m27s
pod/sync-infrahub-sync-object-store-0                    1/1     Running     0          3m27s
pod/sync-infrahub-sync-postgresql-0                      1/1     Running     0          3m27s
pod/sync-infrahub-sync-prefect-server-5688b6c44b-rmzpt   1/1     Running     0          3m27s
pod/sync-infrahub-sync-worker-75b984d5d7-nc2zb           1/1     Running     0          3m27s

NAME                                              STATUS     COMPLETIONS   DURATION   AGE
job.batch/sync-infrahub-sync-bootstrap-56dccc4a   Complete   1/1           40s        3m27s
```

No container restarted. Both volume claims (`data-sync-infrahub-sync-postgresql-0` and
`data-sync-infrahub-sync-object-store-0`, 10Gi each, storage class `standard`) are bound.

At 2026-11-16T11:35:57Z, `kubectl wait --for=condition=complete` on the bootstrap Job returned
`condition met`, and `kubectl wait --for=condition=Ready` returned `condition met` for the five
other pods.

### Logs of the bootstrap Job and the start gates

Last lines of the bootstrap container:

```text
11:33:29.187 | INFO    | __main__ - bucket created
11:33:29.188 | INFO    | __main__ - object store refuses a second create-only write
11:33:29.188 | INFO    | __main__ - work pool created
11:33:29.188 | INFO    | __main__ - deployment applied
11:33:29.188 | INFO    | __main__ - product schema converged; the configuration registry is the operator's to fill
```

Init containers:

- Bootstrap Job, `wait-for-prefect`: `the Prefect server is healthy after 35s`.
- Sync API, `wait-for-bootstrap`: `the deployment is in the work pool infrahub-sync after 39s`.
- Worker, `wait-for-bootstrap`: `the deployment is in the work pool infrahub-sync after 39s`.
- Prefect server, `wait-for-postgresql`: `PostgreSQL is ready after 15s`.

The warning events of the namespace are all from the first 30 seconds of the start and did not
recur:

- `FailedScheduling` on the PostgreSQL and object store pods, until the minikube provisioner
  bound their volume claims.
- A failed readiness probe on the PostgreSQL and object store pods, and a failed startup probe on
  the Prefect server pod, while each process was starting.

### Sync API answers

Through `kubectl port-forward` to 127.0.0.1:8001, context `sync-chart`:

- 2026-11-16T11:36:10Z, `/version`:
  `{"server_version":"3.0.0a5","api_versions":["v3-unstable"],"stability":"unstable"}`
- 2026-11-16T11:36:10Z, `/status`:
  `{"service":"ready","worker":{"state":"ready","detail_available":true,"live_workers":1,"queue_depth":0,"observed_at":"2026-11-16T11:36:10.132678Z"}}`
- 2026-11-16T11:36:10Z, `infrahub-sync whoami` with the operator token (redacted):

  ```text
  actor: operator
  administrator: true
  approval_required: false
  ```

`worker.state` was `ready` at the first request; no wait for the worker heartbeat was needed.

### Defects found on the cluster

None. The chart installed and every check passed on the first attempt.

One difference from [quickstart.md](quickstart.md): its step 2 gives only `image.repository` and
`image.tag` for a locally built image. With minikube, `image.pullPolicy=Never` is also needed so
that the kubelet does not try to pull `infrahub-sync:chart-test` from a registry.

### Render tests after the run

At 2026-11-16T11:36:22Z, `cd H && uv run pytest -q tests/unit/test_infrahub_sync_chart.py`:
`72 passed in 3.12s`.

### State left for the next task

The release `sync` (revision 1) and the minikube profile `sync-chart` are left running for the
upgrade of T021. The port-forward is stopped.

## T021: upgrade the release

Result: every check of [quickstart.md](quickstart.md) step 4 passed. Neither upgrade needed a
manual step, the configuration package registered before the upgrades is still listed after
them, and the chart templates did not change.

### Environment

The cluster, Helm version and chart are the same as in T018: kube context `sync-chart`,
namespace `sync`, Helm v4.2.4, `H/charts/infrahub-sync` at commit `e06ee6d` of `H` (the chart
templates are unchanged by this task). All times are UTC.

The second image tag is the same image under a new name, so the second upgrade changes only the
image reference:

```bash
docker tag infrahub-sync:chart-test infrahub-sync:chart-test-2
minikube -p sync-chart image load infrahub-sync:chart-test-2
```

### Configuration package registered before the upgrades

The Sync API was reached through `kubectl port-forward svc/sync-infrahub-sync-api 8001:8000`,
opened again after each upgrade because each upgrade replaces the Sync API pod. The token was
read from a file into `INFRAHUB_SYNC_API_TOKEN` and never printed.

At 2026-11-16T11:45:36Z:

```bash
uv run --directory S infrahub-sync configs register S/examples/aci_to_infrahub/package.yml \
  --reason 'T021: package that must survive the chart upgrades'
```

```text
config_id: 20261116T1145-7bf7fab1
name: from-cisco-aci
latest_version: 1
registry_version: 1
package_checksum: c05bce4a2250b96f5353b1fa6ab3f72084ba50bf2f6c2b4b0b660dacc84198b6
created_at: 2026-11-16T11:45:37.101118+00:00
```

`infrahub-sync configs list` returned the same `config_id`, `name`, `latest_version: 1` and
`created_at` at 11:45:38Z, after the first upgrade at 11:46:24Z, and after the second upgrade at
11:47:28Z.

### Upgrade 1: a setting the bootstrap does not read

```bash
helm --kube-context sync-chart upgrade sync H/charts/infrahub-sync -n sync --reuse-values \
  --set settings.runAdmissionTtlSeconds=301 --wait --wait-for-jobs --timeout 10m
```

| Time | Event |
| --- | --- |
| 2026-11-16T11:45:42Z | `helm upgrade` starts |
| 2026-11-16T11:45:43Z | new Sync API pod created; ready at 11:45:49Z |
| 2026-11-16T11:46:13Z | new worker pod created; ready at 11:46:14Z |
| 2026-11-16T11:46:14Z | `helm upgrade` returns `STATUS: deployed`, revision 2 |

The bootstrap Job is the same object before and after: `sync-infrahub-sync-bootstrap-56dccc4a`,
UID `789b1a06-4193-4558-b59f-e64f657f6f7d`, `completionTime` 2026-11-16T11:33:31Z. No Job was
created. The Sync API and worker pods were replaced because both receive
`INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS`.

### Upgrade 2: the image tag, which the bootstrap reads

```bash
helm --kube-context sync-chart upgrade sync H/charts/infrahub-sync -n sync --reuse-values \
  --set image.tag=chart-test-2 --wait --wait-for-jobs --timeout 10m
```

| Time | Event |
| --- | --- |
| 2026-11-16T11:46:40Z | `helm upgrade` starts; the Job `sync-infrahub-sync-bootstrap-895f48df` is created and starts; the new Sync API pod is created |
| 2026-11-16T11:46:42Z | new Prefect server pod created (it runs the Sync image too) |
| 2026-11-16T11:46:52Z | bootstrap container logs its last line |
| 2026-11-16T11:46:55Z | new bootstrap Job complete (`completionTime`); new Sync API pod ready |
| 2026-11-16T11:47:10Z | new worker pod created; ready at 11:47:12Z |
| 2026-11-16T11:47:12Z | `helm upgrade` returns `STATUS: deployed`, revision 3 |

After the upgrade, one bootstrap Job is listed: `sync-infrahub-sync-bootstrap-895f48df`, UID
`36ba4bad-ee8e-4410-9243-d6b0c145b4a5`, complete. The Job of revision 1
(`sync-infrahub-sync-bootstrap-56dccc4a`) is gone. Every pod that runs the Sync image runs
`infrahub-sync:chart-test-2`, with no restart.

Last lines of the new bootstrap container. On the converged deployment it found the bucket and
the work pool present and applied the deployment again:

```text
11:46:52.622 | INFO    | __main__ - bucket present
11:46:52.623 | INFO    | __main__ - object store refuses a second create-only write
11:46:52.623 | INFO    | __main__ - work pool present
11:46:52.623 | INFO    | __main__ - deployment applied
11:46:52.623 | INFO    | __main__ - product schema converged; the configuration registry is the operator's to fill
```

The `wait-for-bootstrap` init container of the new Sync API pod logged
`the deployment is in the work pool infrahub-sync after 8s`. The Prefect server pod was being
replaced during those 8 seconds.

The only warning events of the upgrade are a failed readiness probe on the old Prefect server pod
while it stopped, and a failed startup probe on the new Sync API pod while its process started.

### Whether `--wait --wait-for-jobs` waited for the new Job on upgrade

In upgrade 2, Helm returned at 11:47:12Z, 17 seconds after the new Job completed at 11:46:55Z.
That run does not show that Helm waited for the Job itself: the last event before Helm returned
is the worker pod becoming ready, and Helm waits for that pod with `--wait` alone.

A third upgrade isolated the Job. It changes only a pod annotation of the bootstrap Job; a
`helm template` with and without the value differs only in the Job's name and annotation:

```bash
helm --kube-context sync-chart upgrade sync H/charts/infrahub-sync -n sync --reuse-values \
  --set bootstrap.podAnnotations.t021=wait --wait --wait-for-jobs --timeout 10m
```

| Time | Event |
| --- | --- |
| 2026-11-16T11:53:18Z | `helm upgrade` starts |
| 2026-11-16T11:53:19Z | the Job `sync-infrahub-sync-bootstrap-024fcf91` is created and starts |
| 2026-11-16T11:53:24Z | the Job completes (`completionTime`); `helm upgrade` returns `STATUS: deployed`, revision 4 |

No other pod was created. Helm returned in the second the Job completed, so on upgrade
`--wait --wait-for-jobs` waits for the new bootstrap Job.

In upgrade 2 the new Sync API pod became ready in the same second as the new Job completed. That
is the timing of this run only. On upgrade the `wait-for-bootstrap` gate passes as soon as the
Prefect server answers, because the deployment already exists in the work pool, so a Sync API pod
of the new revision can be ready before the new Job completes. This run does not show whether
that happens.

### Sync API answers after the upgrades

At 2026-11-16T11:46:22Z (after upgrade 1) and 2026-11-16T11:47:27Z (after upgrade 2), through a
new port-forward each time, the answers were the same as in T018:

- `/version`: `{"server_version":"3.0.0a5","api_versions":["v3-unstable"],"stability":"unstable"}`
- `/status`: `"service":"ready"`, `worker.state` `ready`. `live_workers` was 2 at both times,
  because the heartbeat of the replaced worker had not expired yet.
- `/me` without a token: HTTP 401. With the operator token:
  `{"actor":"operator","administrator":true,"approval_required":false}`.
- `infrahub-sync whoami`: `actor: operator`, `administrator: true`, `approval_required: false`.

### A pod restart creates no Job

```bash
kubectl --context sync-chart -n sync rollout restart deployment/sync-infrahub-sync-api
kubectl --context sync-chart -n sync rollout status deployment/sync-infrahub-sync-api --timeout=5m
```

The restart started at 2026-11-16T11:47:32Z and the rollout finished at 11:47:38Z. Afterwards
one Job is listed, the same object as after upgrade 2 (`sync-infrahub-sync-bootstrap-895f48df`,
UID `36ba4bad-ee8e-4410-9243-d6b0c145b4a5`, `completionTime` 11:46:55Z), with its one completed
pod. `helm history` still ended at revision 3. The third upgrade, described above, ran later, at
11:53:18Z.

### Defects found on the cluster

None. No chart template changed.

### Tests of User Story 2 in `H`

- Render tests (T019), at 2026-11-16T11:48:29Z, `cd H && uv run pytest -v tests/unit/test_infrahub_sync_chart.py`:
  `78 passed in 3.99s`, including the six RR-34 cases. `helm template` of Helm v4.2.4 has no
  `--revision` flag, so the revision case checks that no template reads `.Release.Revision`
  and that a render with `--is-upgrade` gives the same Job name.
- End-to-end test (T020), from 2026-11-16T11:48:24Z to 11:50:16Z,
  `cd H && INFRAHUB_SYNC_E2E_IMAGE=infrahub-sync:chart-test uv run pytest -v -rs -m sync tests/e2e`:
  `tests/e2e/test_infrahub_sync.py::test_sync_installs_and_upgrades PASSED`,
  `1 passed, 28 deselected, 2 warnings in 110.96s`. The harness created its own vcluster
  (vcluster 0.36.1, docker driver, Kubernetes v1.36.0), not the minikube context `sync-chart`;
  the local image was imported into the vcluster node with `docker save`. The two warnings are
  from kr8s, which does not list Kubernetes v1.36.0 as supported. The test checks the principal
  with `GET /me`, the route that `infrahub-sync whoami` calls. The paths that use
  `INFRAHUB_SYNC_REGISTRY_USERNAME` and `INFRAHUB_SYNC_REGISTRY_PASSWORD` (registry login and
  image pull Secret) were not run, because no registry login was available.
- Without a local image and without a registry login
  (`INFRAHUB_SYNC_E2E_IMAGE=infrahub-sync:does-not-exist`), the test is skipped before any
  vcluster is created, with the message that names the image and the two ways to provide it.

### Finding for the release workflow (T022)

`scripts/bump_chart.py app-version` reads the current `appVersion` with the same check as the
new one, which accepts only `MAJOR.MINOR.PATCH`. The chart's `appVersion` is `3.0.0a5`, so the
first run of `update-infrahub-sync.yml` for `3.0.0` fails. On a copy of `scripts/` and
`charts/infrahub-sync`, `python3 scripts/bump_chart.py app-version infrahub-sync 3.0.0` exited
with `Not a final MAJOR.MINOR.PATCH version: '3.0.0a5'`. The script is shared with the other
charts and was not changed. Until it accepts a pre-release as the current value, a maintainer
sets `appVersion: "3.0.0"` by hand, as research R-6 plans for the versions before 3.0.0; the
workflow then finds the update done and skips it.

### State left for the next task

The release `sync` (revision 4, image `infrahub-sync:chart-test-2`, with the bootstrap pod
annotation `t021: wait`) and the minikube profile `sync-chart` are left running. The configuration `20261116T1145-7bf7fab1` stays registered. The
port-forward is stopped.

## T023 and T024: external PostgreSQL and object store

Result: every refusal and render check of [quickstart.md](quickstart.md) step 5 behaves as
written. On the local cluster, a release with `objectStore.enabled=false` that uses the object
store of a second release completed its bootstrap and its Sync API reported a ready worker. A
release with both `objectStore.enabled=false` and `postgresql.enabled=false` did the same. No
chart template changed.

### Environment

Kube context `sync-chart` (minikube, Kubernetes v1.37.0, arm64), Helm v4.2.4, image
`infrahub-sync:chart-test-2` with `image.pullPolicy=Never`, `H/charts/infrahub-sync` at commit
`8702d11` of `H`. All times are UTC. Each release read its credentials from a Secret
`infrahub-sync` created in its namespace with `kubectl create secret generic --from-env-file`; no
credential value was printed.

### Render tests (T023)

At 2026-11-16T11:55:32Z, `cd H && uv run pytest -q tests/unit/test_infrahub_sync_chart.py`:
`88 passed in 4.23s` (Python 3.11.8).

At 2026-11-16T11:55:41Z, `uv run pytest -v -k "rr30 or rr31 or rr32 or rr41 or rr42 or rr44"`:

```text
tests/unit/test_infrahub_sync_chart.py::test_rr41_endpoint_with_bundled_object_store_is_refused PASSED
tests/unit/test_infrahub_sync_chart.py::test_rr30_external_postgresql_renders_no_postgresql PASSED
tests/unit/test_infrahub_sync_chart.py::test_rr30_connection_strings_come_from_the_secret[False] PASSED
tests/unit/test_infrahub_sync_chart.py::test_rr30_connection_strings_come_from_the_secret[True] PASSED
tests/unit/test_infrahub_sync_chart.py::test_rr31_external_object_store_uses_the_values_given[-None] PASSED
tests/unit/test_infrahub_sync_chart.py::test_rr31_external_object_store_uses_the_values_given[path-path] PASSED
tests/unit/test_infrahub_sync_chart.py::test_rr31_external_object_store_uses_the_values_given[virtual-virtual] PASSED
tests/unit/test_infrahub_sync_chart.py::test_rr32_both_external_claim_no_volume PASSED
tests/unit/test_infrahub_sync_chart.py::test_rr42_external_object_store_without_endpoint_is_refused PASSED
tests/unit/test_infrahub_sync_chart.py::test_rr44_external_postgresql_without_connection_string_is_refused[databaseUrl] PASSED
tests/unit/test_infrahub_sync_chart.py::test_rr44_external_postgresql_without_connection_string_is_refused[prefectDatabaseUrl] PASSED
11 passed, 77 deselected in 0.43s
```

RR-21 was already covered by `test_rr21_ca_only_in_api_worker_and_bootstrap`. RR-41 was already
covered by a refusal test, renamed `test_rr41_endpoint_with_bundled_object_store_is_refused`.
`helm lint charts/infrahub-sync -f charts/infrahub-sync/ci/lint-values.yaml`: `1 chart(s) linted,
0 chart(s) failed`.

### Quickstart step 5

At 2026-11-16T11:56:13Z, from `H`:

| Command | Output |
| --- | --- |
| `helm template sync charts/infrahub-sync` | exit 1, `credentials.bearerTokens is required when credentials.existingSecret is empty` |
| `... --set credentials.existingSecret=s --set objectStore.enabled=false` | exit 1, `s3.endpointUrl is required when objectStore.enabled is false` |
| `... --set objectStore.enabled=false --set s3.endpointUrl=https://s3.example.com --set postgresql.enabled=false \| grep -c 'kind: StatefulSet'` | `0` |

### A release that uses another release's object store

The second release `store`, in namespace `sync-store`, is a default install whose object store
the third release uses:

```bash
helm --kube-context sync-chart -n sync-store install store H/charts/infrahub-sync \
  --set credentials.existingSecret=infrahub-sync --set image.repository=infrahub-sync \
  --set image.tag=chart-test-2 --set image.pullPolicy=Never
```

Installed at 11:56:28Z; its bootstrap Job completed by 11:56:49Z.

The release `ext`, in namespace `sync-ext`, has its own Secret created from the same env file, so
it has the same S3 access key and secret key as `store`. It uses its own `s3.prefix` so that it
does not share stored objects with `store`:

```bash
helm --kube-context sync-chart -n sync-ext install ext H/charts/infrahub-sync \
  --set credentials.existingSecret=infrahub-sync --set image.repository=infrahub-sync \
  --set image.tag=chart-test-2 --set image.pullPolicy=Never \
  --set objectStore.enabled=false \
  --set s3.endpointUrl=http://store-infrahub-sync-object-store.sync-store.svc.cluster.local:9000 \
  --set s3.prefix=ext
```

| Time | Event |
| --- | --- |
| 2026-11-16T11:57:01Z | `helm install` returns `STATUS: deployed`, revision 1 |
| 2026-11-16T11:57:17Z | Job `ext-infrahub-sync-bootstrap-58861813`: `Complete   1/1   16s` (`completionTime` 11:57:17Z) |
| 2026-11-16T11:57:22Z | the Sync API, Prefect server and worker Deployments are available |

`kubectl get pods,svc,statefulset,pvc -n sync-ext` lists the Sync API, bootstrap, Prefect server,
worker and PostgreSQL pods; no object store pod; Services for the Sync API, Prefect server and
PostgreSQL only; one StatefulSet (`ext-infrahub-sync-postgresql`); and one volume claim
(`data-ext-infrahub-sync-postgresql-0`), no object store claim. The Sync API and worker
Deployments set `INFRAHUB_SYNC_S3_ENDPOINT_URL` to the endpoint above and no
`INFRAHUB_SYNC_S3_ADDRESSING_STYLE`.

Last lines of the bootstrap container. `bucket present` shows that it reached the bucket in the
object store of `store`:

```text
11:57:14.530 | INFO    | __main__ - bucket present
11:57:14.531 | INFO    | __main__ - object store refuses a second create-only write
11:57:14.531 | INFO    | __main__ - work pool created
11:57:14.531 | INFO    | __main__ - deployment applied
11:57:14.531 | INFO    | __main__ - product schema converged; the configuration registry is the operator's to fill
```

Through `kubectl port-forward svc/ext-infrahub-sync-api 8011:8000`, at 2026-11-16T11:57:33Z:

- `/version`: `{"server_version":"3.0.0a5","api_versions":["v3-unstable"],"stability":"unstable"}`
- `/status`: `{"service":"ready","worker":{"state":"ready","detail_available":true,"live_workers":1,"queue_depth":0,"observed_at":"2026-11-16T11:57:33.133407Z"}}`

### A release that uses another release's PostgreSQL and object store

The release `extpg`, in namespace `sync-ext-pg`, turns off both bundled services. Its Secret
holds the four credentials that do not concern PostgreSQL, from the same env file, and the two
connection strings to the databases of `store`
(`store-infrahub-sync-postgresql.sync-store.svc.cluster.local:5432`, databases `infrahub_sync`
and `prefect`, with the roles and passwords of `store`). The env file for that Secret was written
to the scratchpad by a script, used, and deleted. `extpg` uses its own `s3.prefix` and its own
work pool:

```bash
helm --kube-context sync-chart -n sync-ext-pg install extpg H/charts/infrahub-sync \
  --set credentials.existingSecret=infrahub-sync --set image.repository=infrahub-sync \
  --set image.tag=chart-test-2 --set image.pullPolicy=Never \
  --set objectStore.enabled=false --set postgresql.enabled=false \
  --set s3.endpointUrl=http://store-infrahub-sync-object-store.sync-store.svc.cluster.local:9000 \
  --set s3.prefix=extpg --set settings.workPool=extpg
```

| Time | Event |
| --- | --- |
| 2026-11-16T11:57:47Z | `helm install` returns `STATUS: deployed`, revision 1 |
| 2026-11-16T11:57:59Z | Job `extpg-infrahub-sync-bootstrap-8aafbe84`: `Complete   1/1   11s` |

The namespace holds the Sync API, bootstrap, Prefect server and worker pods only, Services for the
Sync API and the Prefect server only, no StatefulSet and no volume claim. The bootstrap logged
`bucket present`, `work pool created` and `product schema converged`. Through a port-forward at
2026-11-16T11:58:02Z, `/version` gave the same answer as above and `/status` gave
`"service":"ready"`, `worker.state` `ready`, `live_workers` 1.

`extpg` shares the product database and the Prefect database of `store`, so both releases wrote
to the same Prefect deployment. This setup checks only that the chart connects to an external
PostgreSQL. It is not a supported way to run two releases, and it was removed right after the
check.

### Defects found on the cluster

None. No chart template changed.

### State left for the next task

Releases `extpg`, `ext` and `store` were uninstalled between 11:58:08Z and 11:58:53Z. Their volume
claims and the namespaces `sync-ext-pg`, `sync-ext` and `sync-store` were deleted. The release
`sync` (revision 4) and its two volume claims are left running in namespace `sync`. The
port-forwards are stopped.
