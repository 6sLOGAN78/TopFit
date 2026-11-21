# syntax=docker/dockerfile:1
# Node is pinned to an exact patched version (>= 24.22.1, Brotli CVE-2026-111497);
# keep in sync with web/.nvmrc. Do not use a floating node:24 tag.
FROM node:24.22.3-trixie-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM deps AS builder
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
# API_PROXY_TARGET intentionally unset: production routes /api via Caddy.
RUN npm run build

FROM node:24.22.3-trixie-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
