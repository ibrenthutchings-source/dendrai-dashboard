#!/usr/bin/env python3
"""
Quick connectivity check for the Dendrai PostgreSQL + pgvector database.

Usage:
    python db_ping.py                        # reads DATABASE_URL from environment / .env
    DATABASE_URL=postgresql://... python db_ping.py
"""

import json
import os
import sys

# Load .env if present (works with or without python-dotenv installed)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Allow running from the repo root or from this directory
sys.path.insert(0, os.path.dirname(__file__))
import db

url = os.environ.get("DATABASE_URL", "").strip()
if not url:
    print("ERROR: DATABASE_URL is not set.")
    print("  Set it in your .env file or export it before running this script.")
    sys.exit(1)

print(f"Connecting to: {url.split('@')[-1]}")  # hide credentials in output
print("Initialising schema…")
ok = db.init_db()
if not ok:
    print("ERROR: init_db() failed — check logs above for details.")
    sys.exit(1)

print("Running ping…")
result = db.ping()
print(json.dumps(result, indent=2))

if not result["connected"]:
    print(f"\nFAILED: {result.get('error')}")
    sys.exit(1)

print("\nOK — database is reachable.")
if result["pgvector"]:
    print(f"pgvector {result['vector_version']} is installed.")
else:
    print("pgvector extension is NOT installed on this server.")
    print("  Install it with:  CREATE EXTENSION vector;")
    print("  Or use a pgvector-enabled host (Supabase, Neon, etc.).")
