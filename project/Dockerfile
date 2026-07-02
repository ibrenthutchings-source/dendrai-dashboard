# Build stage — compile React frontend
FROM node:20-alpine AS builder
WORKDIR /app
COPY project/package.json project/package-lock.json* ./
RUN npm install
COPY project/ .
RUN npm run build

# Production stage — Python 3.12 + nginx (Debian slim)
# Uses Debian so that scipy/numpy pre-built wheels install without compilation.
FROM python:3.12-slim AS runner

# Install nginx and remove the default conflicting site
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/nginx/sites-enabled/default

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
