Set the package version to `3.0.0a1` and made one step the only place a release is
named. `invoke release.identity` reads the version out of package metadata and derives
every artifact name from it — the image's `version` label, the wheel and source
distribution, the Compose bundle archive, the Git tag, and the release title — and
refuses a version that is not its own normalized form.

No publication workflow ships for this line yet. The legacy `release.published` route
is unchanged and remains the only publisher, and nothing a pull request can start
uploads anything. Retaining a candidate and binding an approval to one are later units
of work.
