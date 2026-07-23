# Build stage — compile React frontend
FROM node:20-alpine AS builder
WORKDIR /app
COPY project/package.json project/package-lock.json* ./
RUN npm install
COPY project/ .
RUN npm run build

# OPA static binary — fetched in a throwaway stage so curl/ca-certificates
# never end up in the runtime image. Policy-as-Code evaluation/validation
# (pac_endpoints.py's _find_opa_binary/_run_real_opa_eval/_validate_rego_syntax)
# uses the real OPA binary when present on PATH, silently falling back to a
# Python heuristic approximation otherwise — this stage is what makes the
# real path the one that actually runs in production.
FROM debian:bookworm-slim AS opa-fetch
ARG OPA_VERSION=0.69.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /opa "https://openpolicyagent.org/downloads/v${OPA_VERSION}/opa_linux_amd64_static" \
    && chmod +x /opa

# Production stage — Python 3.12 + nginx (Debian slim)
# Uses Debian so that scipy/numpy pre-built wheels install without compilation.
FROM python:3.12-slim AS runner

# Install nginx and remove the default conflicting site
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

# Install the real OPA binary fetched above.
COPY --from=opa-fetch /opa /usr/local/bin/opa
RUN /usr/local/bin/opa version

# Install Python API dependencies
WORKDIR /app/agentic-tools
COPY project/agentic-tools/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agentic-tools source and UBO governance package
COPY project/agentic-tools/ .
COPY UBO/ /app/UBO/

# Copy built frontend into nginx's document root
COPY --from=builder /app/dist /usr/share/nginx/html

# Install nginx config and startup script
COPY project/nginx.conf /etc/nginx/conf.d/default.conf
COPY project/start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 80
CMD ["/start.sh"]
