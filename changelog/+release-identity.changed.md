Set the package version to `3.0.0a1` and made one step the only place a release is
named. `invoke release.identity` reads the version out of package metadata and derives
every artifact name from it — the image's `version` label, the wheel and source
distribution, the Compose bundle archive, the Git tag, and the release title — and
refuses a version that is not its own normalized form.

Publication is now a dispatch-only workflow bound to one qualified candidate run. It
refuses an approval naming a version that candidate did not record, never checks out
source, and uploads only the distributions the candidate already built. With its
`publish` input off, the job that would upload does not exist. The `release.published`
route that rebuilt from source and uploaded unconditionally is removed; the approved
candidate workflow is the only publisher.
