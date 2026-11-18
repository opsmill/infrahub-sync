# Contract: the `demo` preset and `uv run invoke dev.demo`

- `dev.start --preset demo` starts the core and Sync services, a local Infrahub and the local NetBox.
  It writes nothing into Infrahub.
- `dev.demo`:
  1. Starts the `demo` preset (builds the image when its inputs changed, as `dev.start` does).
  2. Loads the NetBox `demo` dataset when NetBox is new; keeps an existing NetBox's data and says so.
  3. Runs `infrahub-sync demo prepare --netbox-url http://netbox:8080
     --infrahub-url http://infrahub-server:8000` inside the `sync-api` container, with the stack's
     Infrahub admin token and Sync development token passed as `exec` environment variables.
  4. Prints: web interface address, Sync API address, which token to enter (never a token the user
     set), the configuration ID, and the next commands:

     ```text
     uv run infrahubctl branch create netbox-demo-1
     uv run infrahub-sync plan --config-id <config-id> --version <version> --branch netbox-demo-1 --reason "demo"
     ```

- Running `dev.demo` again changes nothing and says so.
- Stops before starting anything when a port it publishes is in use, as `dev.start` does.
- `dev.destroy` and `netbox.destroy` remove everything, as today.
