# NexusForge Frontend

Next.js (App Router) + TypeScript + Tailwind CSS frontend for the NexusForge
AI software-engineering platform. It talks to the FastAPI backend over
`/api/*` and renders live agent runs over Server-Sent Events.

## Pages

| Route           | What it does                                                                 |
| --------------- | ---------------------------------------------------------------------------- |
| `/login`        | Sign in (email + password). Stores tokens in `localStorage`.                  |
| `/register`     | Create an account.                                                             |
| `/`             | Dashboard: project cards, create a project.                                    |
| `/projects/[id]`| Project detail: repositories (import/reindex/remove), tasks (create, list).    |
| `/tasks/[id]`   | Task detail: start/cancel runs, live terminal, agent timeline, run stats,     |
|                 | test runs, security & review findings, Markdown report + PDF download.         |

## Running

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
```

Production build:

```bash
npm run build
npm start
```

### Configuration

| Variable                | Default                 | Purpose                          |
| ----------------------- | ----------------------- | -------------------------------- |
| `NEXT_PUBLIC_API_BASE`  | `http://localhost:8000` | Base URL of the NexusForge API.   |

Set it in `.env.local` (or the environment) if the backend isn't on
`http://localhost:8000`:

```
NEXT_PUBLIC_API_BASE=http://10.0.0.5:8000
```

> The backend's CORS must allow the frontend origin (it defaults to
> `http://localhost:3000`, matching this app's dev port).

## Notes

- Auth: Bearer access token kept in `localStorage`
  (`nexusforge.access_token` / `nexusforge.refresh_token`). The API client in
  `lib/api.ts` transparently refreshes the token once on a 401 and retries.
- Live runs: `lib/sse.ts` implements a fetch-based SSE reader
  (`EventSource` can't send `Authorization` headers). The backend replays
  persisted events then streams live ones; the UI reconnects to a non-terminal
  `latest_run` automatically on page load.
- Report PDF: the "Download PDF" link opens
  `/api/tasks/{id}/runs/{runId}/report/pdf` with the access token as a query
  parameter (browsers can't set headers on navigation).
- Dependencies are intentionally light: `react-markdown` (report rendering)
  and `clsx` (class composition). No UI framework.