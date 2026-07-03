#!/bin/sh
set -e
PORT=${PORT:-8080}
sed -i "s/listen 80;/listen ${PORT};/" /etc/nginx/conf.d/default.conf
cd /app/agentic-tools
uvicorn api_server:app --host 127.0.0.1 --port 8001 &

# Wait for uvicorn to be ready before nginx starts proxying.
# python3 is always available in this image; wget/curl are not.
echo "Waiting for api_server to start..."
for i in $(seq 1 30); do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=1)" 2>/dev/null; then
    echo "api_server is up after ${i}s"
    break
  fi
  sleep 1
done

exec nginx -g 'daemon off;'
