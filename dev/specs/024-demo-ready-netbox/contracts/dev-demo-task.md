# Contract: the `demo` preset and `uv run invoke dev.demo`

- `dev.start --preset demo` starts the core and Sync services, a local Infrahub and the local NetBox.
  It writes nothing into Infrahub.
- `dev.demo`:
  1. Starts the `demo` preset (builds the image when its inputs changed, as `dev.start` does).
  2. Loads the NetBox `demo` dataset when NetBox is new; keeps an existing NetBox's data, says so,
     names the dataset when it can tell, and prints `uv run invoke netbox.seed --dataset demo` to
     reload it.
  3. Runs `infrahub-sync demo prepare --netbox-url http://netbox:8080
     --infrahub-url http://infrahub-server:8000 --infrahub-address <published Infrahub address>`
     on the host from the source tree, against the Sync API's loopback address, with the stack's
     Infrahub admin token and Sync development token in the process environment. It does not run
     in the `sync-api` container, whose source mount hides the bundled assets (critique E1).
  4. Prints: web interface address, Sync API address, how to sign in as `admin` (the variable that
     holds the password and its public default; never a token or a password the user set; changed
     on 2026-11-19 after the rebase onto local accounts), the configuration ID, and the next
     commands:

     ```text
     uv run infrahubctl branch create netbox-demo-1
     uv run infrahub-sync plan --config-id <config-id> --version <version> --branch netbox-demo-1 --reason "demo"
     ```

- Running `dev.demo` again changes nothing and says so.
- Stops before starting anything when a port it publishes is in use, as `dev.start` does.
- `dev.destroy` and `netbox.destroy` remove everything, as today.
