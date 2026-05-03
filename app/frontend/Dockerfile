# Multi-stage Next.js production image (standalone output).

# ─── deps ───────────────────────────────────────────────────────
FROM node:20-alpine AS deps
WORKDIR /app
COPY app/frontend/package.json app/frontend/package-lock.json* ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci || npm install --legacy-peer-deps

# ─── build ──────────────────────────────────────────────────────
FROM node:20-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL
ARG BUILD_TIME=unknown
ARG GIT_COMMIT=unknown
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    BUILD_TIME=${BUILD_TIME} \
    GIT_COMMIT=${GIT_COMMIT} \
    NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY app/frontend/ ./
RUN --mount=type=cache,target=/app/.next/cache \
    npm run build

# ─── runtime ────────────────────────────────────────────────────
FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    HOSTNAME=0.0.0.0 \
    PORT=3000 \
    NEXT_TELEMETRY_DISABLED=1 \
    TZ=Asia/Seoul

# Standalone output bundles a minimal node_modules + server.js.
# Our app has no /public assets, so we only ship the standalone runtime
# and the static chunks. Add `COPY --from=build /app/public ./public`
# in the future if static assets are introduced.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
