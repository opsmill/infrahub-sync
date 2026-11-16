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
