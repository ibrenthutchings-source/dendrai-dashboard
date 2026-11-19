#!/usr/bin/env python3
"""
Deployment-environment detection.

Railway auto-injects RAILWAY_ENVIRONMENT_NAME ("production" | "uat" |
"sandbox" | "development") into every service in this project — trustworthy
there, since it's set precisely per deployed service.

It is unset when running outside Railway (a local `python api_server.py`),
but that must NOT default to "development": a local run's DATABASE_URL
comes from whatever's in .env, which is NOT guaranteed to be a development
database — this app's own .env, discovered while building this module,
points at PRODUCTION's Postgres for local-dev convenience. Defaulting an
unset RAILWAY_ENVIRONMENT_NAME to "development" would have let a dev-only
feature's background writes (Exception Management's connector-event scoring
hook) run against whatever real environment .env happens to point at. A
local developer who genuinely wants to exercise dev-only features against a
database they know is safe opts in explicitly with EXCEPTION_MGMT_LOCAL_DEV=true.

Single source of truth for anything gated to the Development environment
only — currently just Exception Management (exceptions_endpoints.py,
connector_poller.py's scoring hook, nav.jsx's devOnly items via /health).

IS_DEVELOPMENT is opt-in-able per environment via EXCEPTION_MGMT_LOCAL_DEV,
so it is NOT a reliable "this is really the Development environment" check
on its own — AI Governance used to need exactly that stricter guarantee
(via a since-removed IS_TRUE_DEVELOPMENT) while its screen had no way to
register a system through the UI; now that it does, it's gated the same
way as every other screen-permissioned feature instead.
"""
import os

ENVIRONMENT_NAME = os.environ.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower()
IS_DEVELOPMENT = (
    ENVIRONMENT_NAME == "development"
    or os.environ.get("EXCEPTION_MGMT_LOCAL_DEV", "").strip().lower() == "true"
)
