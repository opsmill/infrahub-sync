"""The whole managed path, through the worker the bundle deploys.

Plan, retrieve the review artifact the worker published, verify the saved plan,
apply it once confirmed, then run a separate sync against a difference of its own. The artifact is fetched back
through the deployment, which reads it from the object store the worker wrote it
to — the transport that exists so the two never share a filesystem.
"""

from __future__ import annotations

from kit import deployment, follow, key, plant, refuse, require_planned_work, run_request, wrote

from infrahub_sync.client.models import ApplyRunRequest, VerifyRunRequest

with deployment() as client:
    planned = follow(client, client.plan(run_request(client, "plan", "clean-host: plan"), key("plan")))
    run_id = planned.run.run_id

    plan = client.get_plan(run_id)
    if not plan.checksum_ok:
        refuse("the saved plan did not verify against its own checksum")
    require_planned_work(client, run_id)

    artifacts = client.list_artifacts(run_id)
    if not artifacts.artifacts:
        refuse("the worker published no review artifact for the plan")
    retrieved = client.get_artifact(run_id, artifacts.artifacts[0].artifact_id)
    if not retrieved.data:
        refuse("the published review artifact came back empty")

    # The apply names the checksum the review verified, so a plan that changed
    # between the two is refused rather than applied.
    follow(client, client.verify(run_id, VerifyRunRequest(reason="clean-host: verify"), key("verify")))
    applied = follow(
        client,
        client.apply(
            run_id,
            ApplyRunRequest(expected_checksum=plan.checksum, confirm_writes=True, reason="clean-host: apply"),
            key("apply"),
        ),
    )
    if "failed" in applied.run.phase:
        refuse(f"the confirmed apply ended in {applied.run.phase}")

    # The apply above converged the two sides, so a sync taken now would have
    # nothing to converge -- and would still reach a phase with no "failed" in it.
    # The claim is that a separate sync is its own admitted run that converges, so
    # it is given its own difference and has to report having written it.
    plant("sync")
    synced = follow(client, client.sync(run_request(client, "sync", "clean-host: sync"), key("sync")))
    if "failed" in synced.run.phase:
        refuse(f"the separate sync ended in {synced.run.phase}")
    written = wrote(synced.run)
    if written < 1:
        refuse("the separate sync completed without writing anything, so it converged nothing")
