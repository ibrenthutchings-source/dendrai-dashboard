#!/bin/sh
set -e
cd /app/agentic-tools
uvicorn api_server:app --host 127.0.0.1 --port 8001 &
exec nginx -g 'daemon off;'
