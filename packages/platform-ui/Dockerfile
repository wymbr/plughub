# Dockerfile — platform-ui
# Build context: monorepo root (context: ../..)
# Stage 1: build React app
FROM node:20-alpine AS builder

WORKDIR /app/packages/platform-ui
COPY packages/platform-ui/package*.json ./
RUN npm install
COPY packages/platform-ui/ ./
RUN npm run build

# Stage 2: serve with nginx + API proxy to all backends
FROM nginx:alpine

COPY --from=builder /app/packages/platform-ui/dist /usr/share/nginx/html

# Inline nginx config: SPA routing + reverse proxy to all PlugHub backends
RUN printf 'server {\n\
    listen 5174;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    # auth-api (Arc 7a)\n\
    location ~ ^/auth(/|$) {\n\
        proxy_pass         http://auth-api:3200;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 30s;\n\
    }\n\
\n\
    # mcp-server-plughub (agent WebSocket)\n\
    location ~ ^/agent-ws(/|$) {\n\
        proxy_pass         http://mcp-server-plughub:3100;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Upgrade $http_upgrade;\n\
        proxy_set_header   Connection "upgrade";\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 3600s;\n\
    }\n\
\n\
    # mcp-server-plughub (REST /api)\n\
    location ~ ^/api(/|$) {\n\
        proxy_pass         http://mcp-server-plughub:3100;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # workflow-api — must come before generic /v1 rule\n\
    location ~ ^/v1/workflow(/|$) {\n\
        proxy_pass         http://workflow-api:3800;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 300s;\n\
    }\n\
\n\
    # evaluation-api (Arc 6) — before generic /v1\n\
    location ~ ^/v1/evaluation(/|$) {\n\
        proxy_pass         http://evaluation-api:3400;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # mcp-server-knowledge (Arc 6 RAG) — before generic /v1\n\
    location ~ ^/v1/knowledge(/|$) {\n\
        proxy_pass         http://mcp-server-knowledge:3401;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # pricing-api (Arc 2) — before generic /v1\n\
    location ~ ^/v1/pricing(/|$) {\n\
        proxy_pass         http://pricing-api:3900;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # calendar-api routes\n\
    location ~ ^/v1/(calendars|holiday-sets|associations|engine)(/|$) {\n\
        proxy_pass         http://calendar-api:3700;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # agent-registry — generic /v1 catch-all\n\
    location ~ ^/v1(/|$) {\n\
        proxy_pass         http://agent-registry:3300;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # config-api\n\
    location ~ ^/config(/|$) {\n\
        proxy_pass         http://config-api:3600;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # analytics-api (dashboard SSE + reports)\n\
    location ~ ^/(dashboard|sessions|supervisor|reports)(/|$) {\n\
        proxy_pass         http://analytics-api:3500;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Upgrade $http_upgrade;\n\
        proxy_set_header   Connection "";\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 300s;\n\
    }\n\
\n\
    # channel-gateway (webchat WebSocket + upload)\n\
    location ~ ^/webchat(/|$) {\n\
        proxy_pass         http://channel-gateway:8010;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Upgrade $http_upgrade;\n\
        proxy_set_header   Connection "upgrade";\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 300s;\n\
    }\n\
\n\
    # SPA fallback\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 5174
