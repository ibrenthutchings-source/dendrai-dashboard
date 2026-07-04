# Dendrai Dashboard — Frontend

React + Vite frontend for the Dendrai Risk Loop dashboard.

## Development

```bash
cd project
npm install
npm run dev        # Vite dev server at http://localhost:5173
```

The frontend expects `api_server.py` running at `http://localhost:8001`. Start the backend first:

```bash
cd project/agentic-tools
python api_server.py
```

## Build

```bash
cd project
npm run build      # outputs to project/dist/
```

## Entry point

`project/src/main.jsx` imports all component modules in order, exposes globals on `window`, wraps the app in `AuthProvider`, and mounts `<App />`.

## Key files

| File | Purpose |
|---|---|
| `src/main.jsx` | Entry point — imports, globals, auth wrapping, React root |
| `app.jsx` | Root component: pipeline state, routing, loop orchestration |
| `auth.jsx` | Auth context, `LoginScreen`, `ChangePasswordScreen` |
| `pipeline.jsx` | Six-stage pipeline UI + HITL gate substep rendering |
| `code-screens.jsx` | `PolicyAsCodeScreen` (Rego editor + flow map), `RisksAsCodeLiveScreen` |
| `rail.jsx` | Live Register right-hand rail (Risks · Heatmap · Loop tabs) |
| `nav.jsx` | Left navigation sidebar |
| `charts.jsx` | All Recharts chart components |
| `governance.jsx` | Governance Intelligence screen |
| `cem.jsx` | Control Event Monitor screen |
| `styles.css` | All component CSS including auth, PAC, pipeline, charts |

## Authentication

The app is gated by `AuthProvider` (from `auth.jsx`). On load it fetches `/auth/me` — if not authenticated it renders `LoginScreen`. On first login with a `must_change_pw` account it renders `ChangePasswordScreen` before the app.

Default credentials: `admin` / `Admin@Dendrai1!` and `dendrai` / `Dendrai@Pass1!` (both force a password change).

## MCP integration

The frontend communicates with the backend over REST. MCP tools are called server-side by `api_server.py`; the frontend never talks to MCP directly. The AI chat panel in the dashboard surfaces MCP tool traces in real time as Claude calls tools during a conversation.
