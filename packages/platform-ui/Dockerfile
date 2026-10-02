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
    # ATT-08: o arquivo do atendente. O teto padrao do nginx (1 MB) recusaria quase todo anexo\n\
    # com 413 antes de o mcp-server ver; aqui vale o da plataforma (512 MB, video), e o do\n\
    # tenant o gateway confere. Corpo em streaming, sem gravar em disco no proxy.\n\
    location ~ ^/api/agent_attachment/ {\n\
        set $up_mcp_att http://mcp-server-plughub:3100;\n\
        proxy_pass         $up_mcp_att;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        client_max_body_size 512m;\n\
        proxy_request_buffering off;\n\
        proxy_read_timeout 300s;\n\
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
    # rules-engine (RUL-03) — tela de regras. /rules-api/* -> rules-engine:3201/* (prefixo\n\
    # removido: as rotas dele nao tem prefixo proprio). O servico exige Bearer config.rules.\n\
    location ~ ^/rules-api/ {\n\
        set $up_rules http://rules-engine:3201;\n\
        rewrite ^/rules-api(/.*)$ $1 break;\n\
        proxy_pass         $up_rules;\n\
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
    # workflow-api: /v1/workflow e /v1/journeys SAIRAM da borda em 2026-09-29 (AUT-64) —\n\
    # as rotas foram removidas (sem chamador) e /v1/journeys nem era montado. Caem no\n\
    # catch-all do agent-registry, que nao as conhece.\n\
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
    # channel-gateway — SO o que a UI chama: identity resolver (Cliente 360) e resume do\n\
    # Console (DialogForm, aprovacao). AUT-20: era ^/v1/channels inteiro, e publicava na\n\
    # borda o POST /v1/channels/webhook/pool/{id}, ANONIMO (criava sessao ate para pool\n\
    # inexistente) e roubava do registry o /v1/channels das GatewayConfigs. Before generic /v1.\n\
    location ~ ^/v1/channels/webhook/(identity|resume)(/|$) {\n\
        set $up_channel_id http://channel-gateway:8010;\n\
        proxy_pass         $up_channel_id;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # channel-gateway — rotas de FALA: `speech-checks` (VOZ-27) e `speech-models` /\n\
    # `speech-profiles` / `speech-defaults` (VOZ-17: catálogo do serviço e a porta que confere\n\
    # o modelo ANTES de gravar). Todas exigem Bearer + `config.channels`. Before generic /v1.\n\
    location ~ ^/v1/speech-(checks|models|profiles|defaults)(/|$) {\n\
        set $up_speech_check http://channel-gateway:8010;\n\
        proxy_pass         $up_speech_check;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # channel-gateway — gravação da chamada (VOZ-36): ouvir e exportar, com Bearer +\n\
    # `contacts.recording` e trilha LGPD. Áudio em streaming: sem buffer. Before generic /v1.\n\
    location ~ ^/v1/recordings(/|$) {\n\
        set $up_recordings http://channel-gateway:8010;\n\
        proxy_pass         $up_recordings;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 120s;\n\
    }\n\
\n\
    # scheduler-api — Agendas (Scheduler Fase 3). Before generic /v1.\n\
    location ~ ^/v1/agendas(/|$) {\n\
        set $up_scheduler http://scheduler-api:3650;\n\
        proxy_pass         $up_scheduler;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;\n\
        proxy_read_timeout 60s;\n\
    }\n\
\n\
    # mailing-api — Outbound (mailings + campaigns). Before generic /v1.\n\
    location ~ ^/v1/(mailings|campaigns)(/|$) {\n\
        set $up_mailing http://mailing-api:3660;\n\
        proxy_pass         $up_mailing;\n\
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
    # /config/* — paginas do SPA (navegacao) E namespaces do config-api (fetch).\n\
    #\n\
    # O DISCRIMINADOR E O `Accept`, NUNCA O CAMINHO. `masking` e ao mesmo tempo nome de\n\
    # pagina do React Router E namespace do config-api, e as duas URIs sao IDENTICAS:\n\
    # a query string NAO entra no match de `location`, entao GET /config/masking?tenant_id=x\n\
    # tem URI /config/masking, igual a navegacao. Navegacao manda Accept: text/html;\n\
    # `fetch` manda */*. Mesmo discriminador que o proxy do vite ja usa\n\
    # (vite.config.ts:90-94) — sem ele, dev e container DIVERGEM e o defeito so existe\n\
    # em producao, que foi exatamente o que aconteceu.\n\
    #\n\
    # Historico: a versao anterior separava por NUMERO DE SEGMENTOS (`/?$`), raciocinando\n\
    # so sobre ESCRITA (`PUT /config/masking/context_rules`, 2 segmentos, caia no proxy).\n\
    # A LEITURA de namespace tambem tem 1 segmento — entao ela casava aqui e recebia\n\
    # `index.html` com HTTP 200. O `safeJson` (config-hooks.ts:13-19) via `text/html` e\n\
    # lancava, o `catch` deixava `entries` vazio, e a tela de Masking dizia\n\
    # "0 regras configuradas" com 23 em vigor. As regras de categoria caiam no mesmo\n\
    # fetch e degradavam para DEFAULT_DISPLAY_RULE — invisivel, porque default parece certo.\n\
    # Medido 2026-08-26. Gate: infra/test/probe_config_route_collision.sh\n\
    #\n\
    # ROT-01 (2026-09-30): o `Accept` decide para TODO /config/*, sem LISTA de paginas.\n\
    # Havia uma allowlist de nomes de pagina aqui, e ela envelhecia: toda pagina nascida\n\
    # depois dela (agent-reports, context-map, dialog-forms) respondia 422 JSON do\n\
    # config-api a quem abrisse o link direto ou desse F5, sem nada ficar vermelho. O\n\
    # config-api e API JSON e nao serve navegacao, entao `text/html` e sempre do SPA —\n\
    # a mesma regra do proxy do vite (`bypass` do ^/config).\n\
    #\n\
    # `rewrite ... last` dentro de `if` e um dos dois usos seguros de `if` em location.\n\
    location ~ ^/config/ {\n\
        if ($http_accept ~* "text/html") {\n\
            rewrite ^ /index.html last;\n\
        }\n\
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
    # channel-gateway - token de midia do Console (VOZ-04). SO a rota de token.\n\
    location ~ ^/webrtc/token/ {\n\
        set $up_channel_rtc http://channel-gateway:8010;\n\
        proxy_pass         $up_channel_rtc;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
    }\n\
\n\
    # channel-gateway - o atendente desliga a chamada de chat (WCH-07).\n\
    location ~ ^/webrtc/call/ {\n\
        set $up_channel_call http://channel-gateway:8010;\n\
        proxy_pass         $up_channel_call;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Host $host;\n\
    }\n\
\n\
    # SPA fallback\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 5174
