Set the package version to `3.0.0a1` and made one step the only place a release is
named. `invoke release.identity` reads the version out of package metadata and derives
every artifact name from it — the image's `version` label, the wheel and source
distribution, the Compose bundle archive, the Git tag, and the release title — and
refuses a version that is not its own normalized form.

Publication is not part of this change. The existing release route is unchanged and
remains the only publisher: a push to `stable` drafts a release, and publishing that
release runs the dispatch-only publish workflow. `invoke release.build` produces the
two distributions a publication would upload; binding an upload to a qualified
candidate is a later, separately approved change.
