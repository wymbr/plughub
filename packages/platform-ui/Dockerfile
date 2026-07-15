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
# IMPORTANT: proxy_pass must use a $variable (not a literal hostname) so nginx
# re-resolves upstreams via Docker DNS on every request, preventing stale IPs
# after sibling containers restart and get a new internal IP.
RUN printf 'server {\n\
    listen 5174;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    # Docker internal DNS resolver with short TTL — enables dynamic re-resolution\n\
    # when sibling containers restart and change IP.\n\
    resolver 127.0.0.11 valid=10s ipv6=off;\n\
\n\
    # auth-api (Arc 7a)\n\
    location ~ ^/auth(/|$) {\n\
        set $up_auth http://auth-api:3200;\n\
        proxy_pass         $up_auth;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 30s;\n\
    }\n\
\n\
    # mcp-server-plughub (agent WebSocket)\n\
    # /agent-ws → /agent/ws on the backend (path rewrite required)\n\
    location ~ ^/agent-ws {\n\
        set $up_mcp http://mcp-server-plughub:3100;\n\
        rewrite ^/agent-ws(.*) /agent/ws$1 break;\n\
        proxy_pass         $up_mcp;\n\
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
        set $up_mcp http://mcp-server-plughub:3100;\n\
        proxy_pass         $up_mcp;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # analytics-api (agent-assist customer history + transcript)\n\
    # /analytics/* → analytics-api:3500/*  (prefix stripped, mirrors legacy agent-assist-ui).\n\
    # Reaches /sessions/customer/{id} and /v1/transcript/sessions/{id} without colliding\n\
    # with the generic /v1 → agent-registry rule below.\n\
    location ~ ^/analytics/ {\n\
        set $up_analytics_aa http://analytics-api:3500;\n\
        rewrite ^/analytics(/.*)$ $1 break;\n\
        proxy_pass         $up_analytics_aa;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # workflow-api (/v1/workflow and /v1/journeys) — must come before generic /v1 rule\n\
    location ~ ^/v1/workflow(/|$) {\n\
        set $up_workflow http://workflow-api:3800;\n\
        proxy_pass         $up_workflow;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 300s;\n\
    }\n\
\n\
    location ~ ^/v1/journeys(/|$) {\n\
        set $up_workflow http://workflow-api:3800;\n\
        proxy_pass         $up_workflow;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 300s;\n\
    }\n\
\n\
    # evaluation-api (Arc 6) — before generic /v1\n\
    location ~ ^/v1/evaluation(/|$) {\n\
        set $up_eval http://evaluation-api:3400;\n\
        proxy_pass         $up_eval;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # mcp-server-knowledge (Arc 6 RAG) — before generic /v1\n\
    location ~ ^/v1/knowledge(/|$) {\n\
        set $up_knowledge http://mcp-server-knowledge:3401;\n\
        proxy_pass         $up_knowledge;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # pricing-api (Arc 2) — before generic /v1\n\
    location ~ ^/v1/pricing(/|$) {\n\
        set $up_pricing http://pricing-api:3900;\n\
        proxy_pass         $up_pricing;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # calendar-api routes\n\
    location ~ ^/v1/(calendars|holiday-sets|associations|engine)(/|$) {\n\
        set $up_calendar http://calendar-api:3700;\n\
        proxy_pass         $up_calendar;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # analytics-api audit endpoints — must come before generic /v1 catch-all\n\
    location ~ ^/v1/audit(/|$) {\n\
        set $up_audit http://analytics-api:3500;\n\
        proxy_pass         $up_audit;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # dialog-api (DialogForm store — dialog primitive editor) — before generic /v1\n\
    location ~ ^/v1/dialog(/|$) {\n\
        set $up_dialog http://dialog-api:3760;\n\
        proxy_pass         $up_dialog;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # channel-gateway — identity resolver REST (Cliente 360 cadastro manual). Before generic /v1.\n\
    location ~ ^/v1/channels(/|$) {\n\
        set $up_channel_id http://channel-gateway:8010;\n\
        proxy_pass         $up_channel_id;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # agent-registry — generic /v1 catch-all\n\
    location ~ ^/v1(/|$) {\n\
        set $up_registry http://agent-registry:3300;\n\
        proxy_pass         $up_registry;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # SPA routes under /config — must come BEFORE config-api proxy.\n\
    # These are React Router PAGE routes (single segment). Use /?$ so only the exact\n\
    # page (optionally trailing slash) is served by the SPA — config-api key writes\n\
    # like /config/masking/context_rules (2 segments) fall through to the proxy below.\n\
    # (`masking` is both a page name and a config-api namespace, so the old\n\
    #  ^/config/masking(/|$) wrongly captured PUT /config/masking/context_rules → 405.)\n\
    location ~ ^/config/(access|billing|platform|masking|recursos|resources|channels|canais|groups|calendars)/?$ {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
\n\
    # config-api (namespace/key CRUD — all other /config/* routes)\n\
    location ~ ^/config/ {\n\
        set $up_config http://config-api:3600;\n\
        proxy_pass         $up_config;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_set_header   X-Admin-Token $http_x_admin_token;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # analytics-api (dashboard SSE + reports)\n\
    # NOTE: using / (not (/|$)) so bare paths like /reports fall through to SPA fallback\n\
    location ~ ^/(dashboard|sessions|supervisor|reports)/ {\n\
        set $up_analytics http://analytics-api:3500;\n\
        proxy_pass         $up_analytics;\n\
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
        set $up_channel http://channel-gateway:8010;\n\
        proxy_pass         $up_channel;\n\
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
