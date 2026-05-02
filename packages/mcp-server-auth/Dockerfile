FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --omit=dev 2>/dev/null || npm install

COPY tsconfig.json ./
COPY src/ ./src/

RUN npm install -D tsx typescript @types/node @types/express 2>/dev/null || true
RUN npx tsc 2>/dev/null || npx tsx src/index.ts &

EXPOSE 3150

ENV PORT=3150

# Use tsx for fast startup in demo environment (no separate build step)
CMD ["npx", "tsx", "src/index.ts"]
