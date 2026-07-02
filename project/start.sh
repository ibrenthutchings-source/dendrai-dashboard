#!/bin/sh
set -e
PORT=${PORT:-8080}
sed -i "s/listen 80;/listen ${PORT};/" /etc/nginx/conf.d/default.conf
cd /app/agentic-tools
uvicorn api_server:app --host 127.0.0.1 --port 8001 &
exec nginx -g 'daemon off;'
