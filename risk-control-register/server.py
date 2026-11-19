"""Standalone backend for the Risk & Control register.

Mounts ONLY the register router and the auth router (login/session) from the
sibling project/agentic-tools package — none of the pipeline, gates, or
monitoring routers. Run from this folder:

    pip install -r ../project/agentic-tools/requirements.txt
    DATABASE_URL=postgresql://... python server.py     # serves :8001
"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "project" / "agentic-tools"
sys.path.insert(0, str(TOOLS))
os.chdir(TOOLS)

from fastapi import FastAPI  # noqa: E402

import auth_db  # noqa: E402
import auth_endpoints  # noqa: E402
import db  # noqa: E402
import risk_register_endpoints  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if db.init_db():
        auth_db.init_auth_db()
        risk_register_endpoints.seed_static_data()
    yield


app = FastAPI(title="Risk & Control Register", lifespan=lifespan)
app.include_router(auth_endpoints.router)
app.include_router(risk_register_endpoints.router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8001")))
