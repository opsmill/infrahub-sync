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

The task fetches the submodule when it is missing, installs the dependencies and sets the
variables described in [Calling the Sync API](#calling-the-sync-api). To run it against another
Sync API, export those variables and run:

```bash
pnpm install
pnpm dev
```

Before you push, run the checks the CI job runs:

```bash
pnpm check
pnpm typecheck
pnpm test
pnpm build
```

`pnpm test` runs the Vitest tests (`src/**/*.test.ts` and `*.test.tsx`) in jsdom.

Import components from the package name:

```tsx
import { Button } from "@infrahub/ui";
```

### Calling the Sync API

The browser sends API requests to `/api` on its own origin. The Sync API serves every route under
`/api`, and the released image serves the built web interface at every other path, so the same
paths work in development and in a deployment.

In development, the Vite server forwards `/api` requests to the Sync API without their `Origin`
header. The browser's `Origin` names the Vite server, so with anonymous access on, the Sync API
would otherwise refuse every write as coming from another site. It reads the
same environment variables as the `infrahub-sync` CLI, from the shell only, not from a `.env`
file:

- `INFRAHUB_SYNC_API_URL`, the Sync API address without `/api`, by default `http://127.0.0.1:8001`
- `INFRAHUB_SYNC_API_TOKEN`, optional. When it is set, the Vite server adds it as the bearer token
  to every request, and it replaces a token stored in the browser.

The token is not included in the browser bundle.

### Tokens in the browser

When the Sync API refuses `GET /api/me` with 401, the page shows a token page instead. It names
`INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR`, the setting that lets requests without a token act as one
actor, and offers a field for a bearer token. A pasted token is checked with `GET /api/me` and
stored only when the Sync API accepts it. It is kept in `localStorage` under
`infrahub-sync.api-token`, sent with every request, and shared by every tab of the same address,
until the person selects **Forget token** in the Sync service details of the top bar.

`src/api/types.generated.ts` holds TypeScript types for every Sync API route. It is generated
from the `/api/openapi.json` of the running Sync API. Regenerate it after the API changes:

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
| `/configs/{config_id}` | The latest run, then tabs for the configuration's runs, its versions with who registered them and why, and the declared source, destination and mapping. **Start run** creates a plan or a sync. | `GET /configs/{config_id}`, `GET /configs/{config_id}/versions`, `GET /runs?config_id=`, `POST /runs` |
| `/runs` | Every run, newest first, with filters and older pages. | `GET /runs` |
| `/runs/{run_id}` | The plan, verify and apply steps with their timings, the failure detail, the planned changes by kind, the run's files and its executions. **Verify**, **Apply** and **Cancel run** act on the run. | `GET /runs/{run_id}`, `GET /runs/{run_id}/plan`, `GET /runs/{run_id}/artifacts`, `POST /runs/{run_id}/verify`, `POST /runs/{run_id}/apply`, `POST /runs/{run_id}/cancel` |

The routes above are relative to `/api`. The top bar reads `GET /api/status` for the worker state
and `GET /api/me` for the actor the requests act as. A run in progress is read again every 2 seconds; lists every 10 seconds.

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
