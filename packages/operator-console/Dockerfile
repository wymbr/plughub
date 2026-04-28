# Dockerfile — operator-console
# Build context: monorepo root (context: ../..)
# Stage 1: build React app
FROM node:20-alpine AS builder

WORKDIR /app/packages/operator-console
COPY packages/operator-console/package*.json ./
RUN npm install
COPY packages/operator-console/ ./
RUN npm run build

# Stage 2: serve with nginx + API proxy
FROM nginx:alpine

COPY --from=builder /app/packages/operator-console/dist /usr/share/nginx/html

# Inline nginx config: SPA routing + reverse proxy to all backend APIs
#
# Proxy map:
#   /dashboard, /reports, /admin, /sessions, /supervisor → analytics-api:3500
#   /v1/workflow/*         → workflow-api:3800
#   /v1/pricing/*          → pricing-api:3900
#   /v1/pools, /v1/agent-types, /v1/skills,
#   /v1/instances, /v1/channels  → agent-registry:3300
#   /config/*              → config-api:3600
#   /*                     → SPA (index.html)
RUN printf 'server {\n\
    listen 80;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    # Analytics-api routes (SSE-aware: buffering off, long timeout)\n\
    location ~ ^/(dashboard|reports|admin|sessions|supervisor)(/|$) {\n\
        proxy_pass         http://analytics-api:3500;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Upgrade $http_upgrade;\n\
        proxy_set_header   Connection "";\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 300s;\n\
    }\n\
\n\
    # Workflow-api routes  (must come before generic /v1/)\n\
    location /v1/workflow {\n\
        proxy_pass         http://workflow-api:3800;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # Pricing-api routes\n\
    location /v1/pricing {\n\
        proxy_pass         http://pricing-api:3900;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # Agent-registry routes (pools, agent-types, skills, instances, channels)\n\
    location ~ ^/v1/(pools|agent-types|skills|instances|channels)(/|$) {\n\
        proxy_pass         http://agent-registry:3300;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # Config-api routes\n\
    location /config {\n\
        proxy_pass         http://config-api:3600;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # SPA fallback\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80
