Added checkout-free clean-host qualification. A separate `linux/amd64` job downloads the
candidate the build published, verifies the bundle against the checksum the record names,
loads the exact image by its recorded configuration digest, and runs the extracted bundle
through its own lifecycle entry point. The job checks nothing out and installs no
interpreter; every command needing Python or the product CLI runs inside the candidate
image.

The driver states the host's shape rather than assuming it: refusing shims for `python`,
`uv`, `pip`, `pytest`, and the product CLI sit ahead of `PATH` and record any invocation,
and every container carrying the deployment's instance label is inspected for a bind source
outside the extracted bundle. The mandatory matrix covers artifact identity, cold start and
idempotence, managed execution, keyed write policy, schema change, status, restart,
recovery, ownership and reset, alpha replacement, and secrets, and the driver expresses no
way to skip a row.

The qualification kit now carries the driver, its checks, the schemas they load, and the
pinned destination they converge against. None of it is bundle content.
