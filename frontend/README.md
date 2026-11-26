# Infrahub Sync frontend

Welcome to your new TanStack Start app!

## Getting Started

The Infrahub design system, `@infrahub/ui`, comes from the `opsmill/infrahub` repository, which
is checked out as a Git submodule at `vendor/infrahub` (branch `develop`). Fetch it before you
install:

```bash
git submodule update --init frontend/vendor/infrahub   # from the repository root
```

To run this application against the local development stack, from the repository root:

```bash
uv run invoke dev.frontend
```

The task fetches the submodule when it is missing, installs the dependencies, sets the
variable described in [Calling the Sync API](#calling-the-sync-api) and prints how to sign in
as `admin`: the variable that holds its password, `ADMIN_ACCOUNT_PASSWORD`, and the public default
of that password. Use that default only on a Sync service that no one else can reach. To run the
web interface against another Sync service, export the variable described in
[Calling the Sync API](#calling-the-sync-api) and run:

```bash
pnpm install
pnpm dev
```

Before you push, run the checks the CI job runs:

```bash
pnpm check
pnpm typecheck
pnpm build
```

Import components from the package name:

```tsx
import { Button } from "@infrahub/ui";
```

### Calling the Sync API

The browser sends API requests to `/api` on the page's own origin, where the Sync service serves
the Sync API. While the tab holds no session, the page shows only the sign-in form
(`src/components/sign-in.tsx`). The form sends the account name and the password to
`POST /api/auth/sign-in` and keeps the session token of the answer in `sessionStorage`, for the
browser tab only (`src/api/token.ts`); the password is not kept. The page sends the session token
as a bearer token with every request. No cookie is used.

A `401` for a request that carried the held session token, for example after the session was
unused for the idle period, removes the token and shows the sign-in form again. The header names
the signed-in account from `GET /api/me`; its menu opens **My account** and offers **Sign out**.
**Sign out** calls `POST /api/auth/sign-out` with the session token, then removes the token from
the tab whatever the answer.

In development, the Vite server forwards every request under `/api` to the Sync service without
changing the path and without adding an `Authorization` header, so you sign in on the page as in a
deployment.
The server reads the Sync service's address from the same environment variable as the
`infrahub-sync` CLI, from the shell only, not from a `.env` file: `INFRAHUB_SYNC_API_URL`, by
default `http://127.0.0.1:8001`.

`src/api/types.generated.ts` holds TypeScript types for every Sync API route. It is generated
from `/api/openapi.json` of the running Sync service. Regenerate it after the API changes:

```bash
pnpm codegen:openapi
```

A route starts its requests in its `loader` with `prefetchQuery`, and its component reads them
with `useQuery` and the same query options from `src/api/queries.ts`. `prefetchQuery` never
throws, so each page shows its own loading, error and empty states:

```tsx
export const Route = createFileRoute("/runs/$runId")({
  loader: ({ context: { queryClient }, params }) => queryClient.prefetchQuery(runQuery(params.runId)),
  component: RunRoute,
});
```

### Pages

| Page | What it shows | Sync API routes |
| --- | --- | --- |
| `/` | Every configuration, grouped by the state of its latest run: failed or waiting for review, running, no current plan, in sync. | `GET /configs`, `GET /runs`, and `GET /configs/{config_id}/versions/{registry_version}` for each latest version |
| `/configs/{config_id}` | The latest run, then tabs for the configuration's runs, its versions with who registered them and why, and the declared source, destination and mapping. **Start run** creates a plan or a sync. A **Schedule** line names the configuration's schedule that is not deleted, with its state and next start and a link to it, or offers **Create schedule**. | `GET /configs/{config_id}`, `GET /configs/{config_id}/versions`, `GET /runs?config_id=`, `POST /runs`, `GET /schedules?config_id=&limit=1`, and the routes of the create dialog (see `/schedules`) |
| `/runs` | Every run, newest first, with filters and older pages. A run that a schedule started has a **Scheduled** mark that links to its schedule. The **Schedule** filter, next to the configuration filter, is a searchable list of the schedules by name and sets the `schedule` search parameter; the filter is shown as a removable "Schedule: name" entry. | `GET /runs`, with `schedule_id=` when the schedule filter is set; `GET /schedules` and `GET /schedules?state=deleted` for the filter's choices; `GET /schedules/{schedule_id}` for the name of the filtered schedule |
| `/schedules` | "Needs action" cards for stopped and failing schedules, each linking to its next step, then the active and the paused schedules by name, with tabs (All, Needs action, Active, Paused, Deleted) in the `show` search parameter, a search by schedule or configuration name in `q`, and a removable configuration filter in `config`. A "Next starts" panel lists the next 8 starts by day. Reads every page of the list, 200 schedules at a time; deleted schedules are read only in their tab. **Create schedule** opens the create dialog: the configuration choice shows "name, version n" and the configuration and time zone choices are searchable lists; the cadence helper builds the cron expression, and the dialog shows the next five start times. | `GET /schedules`, `GET /schedules?state=deleted` for the Deleted tab, `GET /configs`; the create dialog: `GET /configs/{config_id}/versions`, `POST /schedules/next-starts`, `POST /schedules` |
| `/schedules/{schedule_id}` | One schedule, in three columns on a wide screen. In the middle, a status card states the situation in one sentence (the next start, the failed runs in a row, the stop reason or the pause) with the next step as its main button, warns when the schedule is not handed to the task manager or a newer version is registered, and the history follows, newest first, with **Show all events** and **Load older events**. On the left, the settings, each value under its label, the open review branch and the created and updated times. On the right, a timeline of the next five starts above **Now** and the last five runs below it, with **All runs**. **Change**, **Pause**, **Resume** and **Delete** for its owner and administrators; **Take over** for administrators who do not own it. | `GET /schedules/{schedule_id}`, `GET /runs?schedule_id=&limit=5` for the last runs, `POST /schedules/next-starts`, `GET /events?related=schedule:{schedule_id}&order=desc`, `GET /configs/{config_id}/versions` for the change dialog, `POST /schedules/{schedule_id}/change`, `/pause`, `/resume`, `/delete`, `/take-over` |
| `/account` | The signed-in account: change its password, and list, create and delete its API tokens. A created token's value is shown once. | `GET /me`, `PUT /me/password`, `GET /me/tokens`, `POST /me/tokens`, `DELETE /me/tokens/{token_id}` |
| `/accounts` | For administrators: every account with its level and state, a filter on the state, and create, set level, set password, allow sign-in again, disable, enable, the tokens of an account, and delete. Other accounts see a message that only an administrator can manage accounts. | `GET /accounts`, `POST /accounts`, `PATCH /accounts/{name}`, `PUT /accounts/{name}/password`, `POST /accounts/{name}/unlock`, `DELETE /accounts/{name}`, `GET /accounts/{name}/tokens`, `DELETE /accounts/{name}/tokens/{token_id}` |
| `/runs/{run_id}` | The plan, verify and apply steps with their timings, the failure detail, the planned changes by kind, the run's files and its executions. **Verify**, **Apply** and **Cancel run** act on the run. For a run that a schedule started: "Started by schedule name, scheduled for time", linked to the schedule. | `GET /runs/{run_id}`, `GET /runs/{run_id}/plan`, `GET /runs/{run_id}/artifacts`, `POST /runs/{run_id}/verify`, `POST /runs/{run_id}/apply`, `POST /runs/{run_id}/cancel`, `GET /schedules/{schedule_id}` for a scheduled run |
| `/health` | Whether each component of the Sync API answered its health check, the number of workers online and required, the executions waiting for a worker, and the live and stale workers of the last 24 hours. The page reads the report again every 15 seconds. When a read fails, the page keeps the last report and draws it grey under a notice. The Sync service details in the top bar link to this page. | `GET /health` |

The top bar reads `GET /health` for the worker state and `GET /me` for the signed-in account,
which it displays, and its level, which decides whether the **Accounts** link is shown. A run in progress is read again every
2 seconds; lists every 10 seconds.

Each write sends an `Idempotency-Key`. A dialog keeps its key until a field changes, so sending
again after a lost response retries the same request instead of creating a second run.

### Updating the Infrahub design system

To move to a newer Infrahub `develop`, run `git submodule update --remote frontend/vendor/infrahub`
from the repository root. Then compare the `catalog:` section of
`vendor/infrahub/frontend/pnpm-workspace.yaml` with the one in `pnpm-workspace.yaml` and copy any
version changes before you run `pnpm install`.

## Building For Production

To build this application for production:

```bash
pnpm build
```

The build writes the page and its assets to `dist/`. The Sync service serves that directory at
the root of its address when `INFRAHUB_SYNC_WEB_DIRECTORY` names it, and the Sync API under `/api`.
The container image builds the web interface and sets this variable itself. To serve your build
from the Sync API that `dev.api` runs on the host, with the development stack started:

```bash
INFRAHUB_SYNC_WEB_DIRECTORY=$PWD/frontend/dist uv run invoke dev.api   # from the repository root
```

### Styling

This project uses [Tailwind CSS](https://tailwindcss.com/) for styling.

#### Removing Tailwind CSS

If you prefer not to use Tailwind CSS:

1. Remove the demo pages in `src/routes/demo/`
2. Replace the Tailwind import in `src/styles.css` with your own styles
3. Remove `tailwindcss()` from the plugins array in `vite.config.ts`
4. Remove `@tailwindcss/vite` and `tailwindcss` from `package.json`

### Linting & Formatting

This project uses [Biome](https://biomejs.dev/) for linting and formatting. The following scripts are available:

```bash
pnpm lint
pnpm format
pnpm check
```

### Routing

This project uses [TanStack Router](https://tanstack.com/router) with file-based routing. Routes are managed as files in `src/routes`.

#### Adding A Route

To add a new route to your application just add a new file in the `./src/routes` directory.

TanStack will automatically generate the content of the route file for you.

Now that you have two routes you can use a `Link` component to navigate between them.

#### Adding Links

To use SPA (Single Page Application) navigation you will need to import the `Link` component from `@tanstack/react-router`.

```tsx
import { Link } from "@tanstack/react-router";
```

Then anywhere in your JSX you can use it like so:

```tsx
<Link to="/about">About</Link>
```

This will create a link that will navigate to the `/about` route.

More information on the `Link` component can be found in the [Link documentation](https://tanstack.com/router/v1/docs/framework/react/api/router/linkComponent).

#### Using A Layout

In the File Based Routing setup the layout is located in `src/routes/__root.tsx`. Anything you add to the root route will appear in all the routes. The route content will appear in the JSX where you render `{children}` in the `shellComponent`.

Here is an example layout that includes a header:

```tsx
import { HeadContent, Scripts, createRootRoute } from '@tanstack/react-router'

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: 'utf-8' },
      { name: 'viewport', content: 'width=device-width, initial-scale=1' },
      { title: 'My App' },
    ],
  }),
  shellComponent: ({ children }) => (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        <header>
          <nav>
            <Link to="/">Home</Link>
            <Link to="/about">About</Link>
          </nav>
        </header>
        {children}
        <Scripts />
      </body>
    </html>
  ),
})
```

More information on layouts can be found in the [Layouts documentation](https://tanstack.com/router/latest/docs/framework/react/guide/routing-concepts#layouts).

### Server Functions

TanStack Start provides server functions that allow you to write server-side code that seamlessly integrates with your client components.

```tsx
import { createServerFn } from '@tanstack/react-start'

const getServerTime = createServerFn({
  method: 'GET',
}).handler(async () => {
  return new Date().toISOString()
})

// Use in a component
function MyComponent() {
  const [time, setTime] = useState('')
  
  useEffect(() => {
    getServerTime().then(setTime)
  }, [])
  
  return <div>Server time: {time}</div>
}
```

### API Routes

You can create API routes by using the `server` property in your route definitions:

```tsx
import { createFileRoute } from '@tanstack/react-router'
import { json } from '@tanstack/react-start'

export const Route = createFileRoute('/api/hello')({
  server: {
    handlers: {
      GET: () => json({ message: 'Hello, World!' }),
    },
  },
})
```

### Data Fetching

There are multiple ways to fetch data in your application. You can use TanStack Query to fetch data from a server. But you can also use the `loader` functionality built into TanStack Router to load the data for a route before it's rendered.

For example:

```tsx
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/people')({
  loader: async () => {
    const response = await fetch('https://swapi.dev/api/people')
    return response.json()
  },
  component: PeopleComponent,
})

function PeopleComponent() {
  const data = Route.useLoaderData()
  return (
    <ul>
      {data.results.map((person) => (
        <li key={person.name}>{person.name}</li>
      ))}
    </ul>
  )
}
```

Loaders simplify your data fetching logic dramatically. Check out more information in the [Loader documentation](https://tanstack.com/router/latest/docs/framework/react/guide/data-loading#loader-parameters).

## Learn More

You can learn more about all of the offerings from TanStack in the [TanStack documentation](https://tanstack.com).

For TanStack Start specific documentation, visit [TanStack Start](https://tanstack.com/start).
