infrahub-sync no longer installs the `infrahub-sdk[all]` extra. It declares `jinja2`, `pyyaml`
and `typer` directly, which are the only packages it used from that extra, so a plain install
pulls fewer unrelated packages. infrahub-sdk 1.23.1 is the tested version; the supported range
stays `>=1.17,<2` so infrahub-sync can be paired with the SDK release an Infrahub server needs.
