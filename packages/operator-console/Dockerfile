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

# Inline nginx config: SPA routing + reverse proxy to analytics-api
RUN printf 'server {\n\
    listen 80;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    # Proxy analytics-api routes\n\
    location ~ ^/(dashboard|reports|admin|sessions|supervisor)(/|$) {\n\
        proxy_pass         http://analytics-api:3500;\n\
        proxy_http_version 1.1;\n\
        proxy_set_header   Upgrade $http_upgrade;\n\
        proxy_set_header   Connection "";\n\
        proxy_set_header   Host $host;\n\
        proxy_buffering    off;   # needed for SSE\n\
        proxy_read_timeout 300s;\n\
    }\n\
\n\
    # SPA fallback\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80
