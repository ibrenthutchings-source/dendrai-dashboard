# Risk & Control Register (standalone)

The Risk & Control register extracted from the Dendrai dashboard as its own app:
Framework Matrix, Detail, Risk Graph, Control Sankey and Controls tabs, with
sign-in. No pipeline, HITL gates or monitoring screens.

- `src/` — the register screens (`risk-register-review.jsx`, graph/sankey,
  controls reference, xlsx export) plus the shared `components.jsx` / `auth.jsx`
  and stylesheets they depend on.
- `server.py` — minimal FastAPI app mounting only the register and auth routers
  from `../project/agentic-tools` (so the backend logic is shared, not forked).

## Run

```bash
# backend (needs DATABASE_URL, same env as the main app)
pip install -r ../project/agentic-tools/requirements.txt
python server.py            # :8001

# frontend
npm install
npm run dev                 # Vite proxies /api/risk-register and /auth to :8001
```

Control add/remove in the Framework Matrix persists to a `matrix_edit` review
per scope (see `saveMatrixControls` in `src/risk-register-review.jsx`).
