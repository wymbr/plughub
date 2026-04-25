# Dockerfile — agent-assist-ui
# Build context: monorepo root (context: ../..)
FROM node:20-alpine AS builder

WORKDIR /app/packages/agent-assist-ui
COPY packages/agent-assist-ui/package*.json ./
RUN npm install
COPY packages/agent-assist-ui/ ./
RUN npm run build

# ── Runtime: nginx with WebSocket + REST proxy ───────────────────────────────
FROM nginx:alpine

COPY --from=builder /app/packages/agent-assist-ui/dist /usr/share/nginx/html

RUN printf 'server {\n\
    listen 5173;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    # REST bridge: /api/* → mcp-server-plughub:3100/*\n\
    location /api/ {\n\
        proxy_pass         http://mcp-server-plughub:3100/;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # WebSocket: /agent-ws → mcp-server-plughub:3100/agent/ws\n\
    location /agent-ws {\n\
        proxy_pass         http://mcp-server-plughub:3100/agent/ws;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Upgrade $http_upgrade;\n\
        proxy_set_header   Connection "upgrade";\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 3600s;\n\
    }\n\
\n\
    # Analytics API: /analytics/* → analytics-api:3500/*\n\
    location /analytics/ {\n\
        proxy_pass         http://analytics-api:3500/;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # SPA fallback\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 5173
