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

# gitleaks static binary — same throwaway-fetch pattern as OPA above, powering
# the DevOps Monitoring secret-scanning producer (secret_scanner_connectors.py's
# _find_gitleaks_binary/scan_repo_for_secrets), the real producer for the
# SECRET_DETECTED EventType outside a live GitHub Advanced Security webhook.
FROM debian:bookworm-slim AS gitleaks-fetch
ARG GITLEAKS_VERSION=8.28.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tar \
    && curl -fsSL -o /tmp/gitleaks.tar.gz "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    && tar -xzf /tmp/gitleaks.tar.gz -C /tmp gitleaks \
    && chmod +x /tmp/gitleaks

# Production stage — Python 3.12 + nginx (Debian slim)
# Uses Debian so that scipy/numpy pre-built wheels install without compilation.
FROM python:3.12-slim AS runner

# Install nginx and git (git is required to clone repos for secret scanning —
# see secret_scanner_connectors.py) and remove the default conflicting site
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx git \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

# Install the real OPA and gitleaks binaries fetched above.
COPY --from=opa-fetch /opa /usr/local/bin/opa
RUN /usr/local/bin/opa version
COPY --from=gitleaks-fetch /tmp/gitleaks /usr/local/bin/gitleaks
RUN /usr/local/bin/gitleaks version

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
