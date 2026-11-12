# Multi-stage Next.js production image (standalone output).

# ─── deps ───────────────────────────────────────────────────────
FROM node:20-alpine AS deps
WORKDIR /app
COPY app/frontend/package.json app/frontend/package-lock.json ./
# 공급망 잠금(2026-11-12, web-app-security-audit ai-era-threats §2):
# - lock 파일 그대로만 설치한다. 예전의 `|| npm install --legacy-peer-deps` 대체 경로는
#   npm ci 가 실패하면 lock 을 무시한 설치로 넘어가 버려 지웠다 — 실패하면 빌드가 멈춰야 한다.
# - 설치 스크립트(pre/postinstall)는 막는다. npm 웜은 설치 순간 실행된다.
#   지금 설치 스크립트가 있는 의존성은 개발용 fsevents(맥 전용)·unrs-resolver 뿐이라 빌드에 영향 없음.
#   나중에 꼭 필요한 패키지가 생기면 아래에 `npm rebuild <이름>` 으로 이름을 찍어 돌린다.
RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts

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
