#!/bin/sh
set -e
# Railway's public networking for this service targets port 80 (confirmed in
# Settings > Networking) but doesn't inject a PORT env var here — the old
# ${PORT:-8080} fallback silently mismatched that, so nginx listened on 8080
# while Railway's edge routed public traffic to 80, 502ing every request even
# though the container itself was healthy. Default to 80 to match.
PORT=${PORT:-80}
echo "DIAGNOSTIC: resolved PORT=${PORT} (unset in env means the 80 fallback was used)"
sed -i "s/listen 80;/listen ${PORT};/" /etc/nginx/conf.d/default.conf
echo "DIAGNOSTIC: nginx listen directive after substitution:"
grep -n "listen" /etc/nginx/conf.d/default.conf
cd /app/agentic-tools

# Start uvicorn; capture its PID so we can detect crashes below.
uvicorn api_server:app --host 127.0.0.1 --port 8001 &
UVICORN_PID=$!
echo "uvicorn started (PID ${UVICORN_PID})"

# Wait up to 30 s for uvicorn to answer /health.
# python3 is always available; wget/curl are not in python:3.12-slim.
i=0
while [ $i -lt 30 ]; do
  i=$((i + 1))

  # Bail immediately if uvicorn crashed.
  if ! kill -0 ${UVICORN_PID} 2>/dev/null; then
    echo "ERROR: uvicorn (PID ${UVICORN_PID}) died after ${i}s — check startup logs above"
    exit 1
  fi

  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=1)" 2>/dev/null; then
    echo "api_server is up after ${i}s"
    break
  fi

  sleep 1
done

# Final liveness check before handing off to nginx.
if ! kill -0 ${UVICORN_PID} 2>/dev/null; then
  echo "ERROR: uvicorn died before nginx could start"
  exit 1
fi

# Validate the config explicitly so a bad `location` block fails loudly here
# instead of nginx silently refusing to start (or restarting) later.
echo "DIAGNOSTIC: running nginx -t"
nginx -t

# Run nginx alongside uvicorn (not `exec`) so this script stays PID 1 and can
# supervise both. Previously `exec nginx` handed PID 1 to nginx: if uvicorn
# later crashed, nginx kept serving the static SPA and 502ing every /api
# request, the container never exited, and Railway's ON_FAILURE restart policy
# never fired — so the API stayed down until someone redeployed by hand.
nginx -g 'daemon off;' &
NGINX_PID=$!
echo "nginx started (PID ${NGINX_PID})"

# Poll both processes; the moment either exits, bring the whole container down
# so Railway restarts it cleanly.
while kill -0 "${UVICORN_PID}" 2>/dev/null && kill -0 "${NGINX_PID}" 2>/dev/null; do
  sleep 5
done

if ! kill -0 "${UVICORN_PID}" 2>/dev/null; then
  echo "FATAL: uvicorn (PID ${UVICORN_PID}) exited — stopping nginx so the container restarts"
  kill "${NGINX_PID}" 2>/dev/null || true
else
  echo "FATAL: nginx (PID ${NGINX_PID}) exited — stopping uvicorn so the container restarts"
  kill "${UVICORN_PID}" 2>/dev/null || true
fi
wait 2>/dev/null || true
exit 1
