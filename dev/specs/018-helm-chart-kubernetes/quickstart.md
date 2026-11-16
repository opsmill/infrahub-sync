# Quickstart: validate the `infrahub-sync` chart on a local cluster

This guide validates the chart end to end. It is for the person who implements or reviews the
chart, not for operators; the operator page is `docs/docs/kubernetes-deployment.mdx`.

## Prerequisites

- `helm`, `kubectl`, `minikube` and `docker`.
- The `infrahub-helm` worktree at `/Users/bkohler/automation/opsmill/infrahub-helm-sync-chart`.
- The Sync image. Either pull `registry.opsmill.io/opsmill/infrahub-sync:<appVersion>` with a
  registry login, or build it from this repository (`docker build -t infrahub-sync:chart-test .`)
  and load it (`minikube image load infrahub-sync:chart-test`).

## 1. Static checks

```bash
cd /Users/bkohler/automation/opsmill/infrahub-helm-sync-chart
helm lint charts/infrahub-sync --set credentials.existingSecret=infrahub-sync
uv run pytest -v tests/unit/test_infrahub_sync_chart.py
```

Expected: no lint error, every render test passes. The render tests assert
[contracts/rendered-release.md](contracts/rendered-release.md).

## 2. Install

```bash
minikube start --profile sync-chart
kubectl create namespace sync
kubectl -n sync create secret generic infrahub-sync --from-env-file=<a .env with the seven required credentials>
helm install sync charts/infrahub-sync -n sync \
  --set credentials.existingSecret=infrahub-sync \
  --wait --timeout 10m
```

With a locally built image, add `--set image.repository=infrahub-sync --set image.tag=chart-test`.

Expected:

- `kubectl -n sync get jobs`: the bootstrap Job of revision 1 is complete.
- `kubectl -n sync get pods`: the Sync API, the worker, the Prefect server, PostgreSQL and the
  object store are ready.

## 3. Call the Sync API

```bash
kubectl -n sync port-forward svc/sync-infrahub-sync-api 8001:8000 &
curl -fsS http://127.0.0.1:8001/version
curl -fsS http://127.0.0.1:8001/status
INFRAHUB_SYNC_API_URL=http://127.0.0.1:8001 INFRAHUB_SYNC_API_TOKEN=<token> uv run infrahub-sync whoami
```

Expected: the version of the image; `worker.state` is `ready`; `whoami` reports the principal.

## 4. Upgrade

First an upgrade that changes a setting the bootstrap does not read. The Job keeps its name
(RR-34):

```bash
helm upgrade sync charts/infrahub-sync -n sync --reuse-values --set settings.runAdmissionTtlSeconds=301 --wait --timeout 10m
kubectl -n sync get jobs
```

Then an upgrade that changes the image tag, which the bootstrap does read. Rebuild the image under
a second tag first (`docker tag infrahub-sync:chart-test infrahub-sync:chart-test-2`, then
`minikube image load infrahub-sync:chart-test-2`):

```bash
helm upgrade sync charts/infrahub-sync -n sync --reuse-values --set image.tag=chart-test-2 --wait --timeout 10m
kubectl -n sync wait --for=condition=complete job -l app.kubernetes.io/component=bootstrap --timeout=10m
kubectl -n sync get jobs
```

Expected:

- Both upgrades complete without a manual step.
- After the second upgrade, one bootstrap Job is listed. It has a new name, it is complete, and
  the previous Job is gone.
- Step 3 gives the same answers, and the package registered before the upgrade is still listed.
- A pod restart (`kubectl -n sync rollout restart deployment/sync-infrahub-sync-api`) creates no
  Job.

## 5. Refusals and the external variant

```bash
helm template sync charts/infrahub-sync                                  # fails: names the first missing credential
helm template sync charts/infrahub-sync --set credentials.existingSecret=s \
  --set objectStore.enabled=false                                        # fails: names objectStore.enabled and s3.endpointUrl
helm template sync charts/infrahub-sync --set credentials.existingSecret=s \
  --set objectStore.enabled=false --set s3.endpointUrl=https://s3.example.com \
  --set postgresql.enabled=false | grep -c 'kind: StatefulSet'           # prints 0
```

## 6. Uninstall

```bash
helm uninstall sync -n sync
kubectl -n sync get pvc      # the two claims are still listed
minikube delete --profile sync-chart
```
