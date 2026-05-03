#!/usr/bin/env bash
# qlib Blue/Green deployment script.
# Mirrors the likeweb/pm pattern (deploy.sh) with qlib-specific naming and
# the singleton-scheduler caveat.
set -e

if [ -f .env ]; then
  set -a; source .env; set +a
fi

export PATH=$PATH:/usr/local/bin:/usr/bin
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# ─────────────────────────────────────────────────────────────
# Port layout (nginx-waf upstream routes to these)
#   blue : web=25022, api=25023
#   green: web=25024, api=25025
# Sequential with newsro 25011-12 + mail-news 25021 in the tmanager.kr group.
# ─────────────────────────────────────────────────────────────
BLUE_WEB_PORT=${BLUE_WEB_PORT:-25022}
BLUE_API_PORT=${BLUE_API_PORT:-25023}
GREEN_WEB_PORT=${GREEN_WEB_PORT:-25024}
GREEN_API_PORT=${GREEN_API_PORT:-25025}

# nginx-waf-blue is the SSL-terminating WAF on rocky-monitor that owns the
# qlib.tmanager.kr server block. Blue/Green flip = swap the active
# url include file and reload nginx inside the WAF container.
WAF_CONTAINER=${WAF_CONTAINER:-nginx-waf-blue}
WAF_CONF_DIR=${WAF_CONF_DIR:-/etc/nginx/conf.d}
ACTIVE_INCLUDE=${ACTIVE_INCLUDE:-${WAF_CONF_DIR}/qlib-url.inc}

# ─────────────────────────────────────────────────────────────
# 1. Detect active slot via running container name
# ─────────────────────────────────────────────────────────────
IS_BLUE=$(docker ps --format "{{.Names}}" | grep -c "qlib_web_blue" || true)

if [ "$IS_BLUE" -eq "1" ]; then
  TARGET_COLOR="green"; CURRENT_COLOR="blue"
  WEB_PORT=$GREEN_WEB_PORT;  API_PORT=$GREEN_API_PORT
  CURR_WEB_PORT=$BLUE_WEB_PORT; CURR_API_PORT=$BLUE_API_PORT
else
  TARGET_COLOR="blue"; CURRENT_COLOR="green"
  WEB_PORT=$BLUE_WEB_PORT;  API_PORT=$BLUE_API_PORT
  CURR_WEB_PORT=$GREEN_WEB_PORT; CURR_API_PORT=$GREEN_API_PORT
fi

DEPLOY_LOG="/home/qlib/deploy.log"
DEPLOY_TS=$(date '+%Y-%m-%d %H:%M:%S')
log_deploy() { echo "$@"; echo "[${DEPLOY_TS}] $@" >> "${DEPLOY_LOG}"; }

echo "🚀 배포 시작: $TARGET_COLOR (web:${WEB_PORT} / api:${API_PORT})"
log_deploy "========== 배포 시작: $TARGET_COLOR (web:${WEB_PORT} / api:${API_PORT}) =========="

# ─────────────────────────────────────────────────────────────
# 2. Tear down stale target slot (defense in depth)
# ─────────────────────────────────────────────────────────────
echo "🧹 기존 ${TARGET_COLOR} 컨테이너 정리 (redis는 유지)..."
if [ -f ".env.${TARGET_COLOR}" ]; then
  docker compose -p qlib-${TARGET_COLOR} -f docker-compose.prod.yml --env-file .env.${TARGET_COLOR} \
    rm -sf api worker scheduler web 2>/dev/null || true
fi
docker stop qlib_api_${TARGET_COLOR} qlib_worker_${TARGET_COLOR} qlib_scheduler_${TARGET_COLOR} qlib_web_${TARGET_COLOR} 2>/dev/null || true
docker rm -f qlib_api_${TARGET_COLOR} qlib_worker_${TARGET_COLOR} qlib_scheduler_${TARGET_COLOR} qlib_web_${TARGET_COLOR} 2>&1 || true
if docker ps -a --format '{{.Names}}' | grep -q "qlib_api_${TARGET_COLOR}"; then
  echo "❌ qlib_api_${TARGET_COLOR} 컨테이너를 제거할 수 없습니다."
  docker ps -a --filter "name=qlib_api_${TARGET_COLOR}" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}"
  exit 1
fi

# ─────────────────────────────────────────────────────────────
# 3. Slot env file (.env + COLOR/PORT/IMAGE_TAG)
# ─────────────────────────────────────────────────────────────
cp .env .env.${TARGET_COLOR}
{
  echo "COLOR=${TARGET_COLOR}"
  echo "WEB_PORT=${WEB_PORT}"
  echo "API_PORT=${API_PORT}"
  echo "BUILD_TIME=$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  echo "GIT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "IMAGE_TAG=${IMAGE_TAG:-latest}"
} >> .env.${TARGET_COLOR}

# ─────────────────────────────────────────────────────────────
# 4. Pull or build, then up
# ─────────────────────────────────────────────────────────────
mkdir -p /home/qlib/data/qlib /home/qlib/data/db /home/qlib/data/redis

if [ -n "${IMAGE_TAG}" ] && [ "${IMAGE_TAG}" != "local" ]; then
  echo "📥 Registry에서 이미지 pull (IMAGE_TAG=${IMAGE_TAG})..."
  docker compose -p qlib-${TARGET_COLOR} -f docker-compose.prod.yml --env-file .env.${TARGET_COLOR} pull api web
else
  echo "📦 Docker 이미지 순차 빌드 중..."
  docker compose -p qlib-${TARGET_COLOR} -f docker-compose.prod.yml --env-file .env.${TARGET_COLOR} build api
  docker compose -p qlib-${TARGET_COLOR} -f docker-compose.prod.yml --env-file .env.${TARGET_COLOR} build web
fi

# redis is shared across slots (qlib_redis container, not per-color).
# Bring it up first; idempotent if already running.
echo "🧱 redis 의존성 확인..."
docker compose -p qlib-${TARGET_COLOR} -f docker-compose.prod.yml --env-file .env.${TARGET_COLOR} up -d redis

# Critical: ONLY ONE scheduler may run at a time (Celery beat singleton).
# Start api+worker+web in target slot, but defer scheduler until traffic flips.
echo "🚀 컨테이너 실행 (api + worker + web)..."
docker compose -p qlib-${TARGET_COLOR} -f docker-compose.prod.yml --env-file .env.${TARGET_COLOR} up -d api worker web

# ─────────────────────────────────────────────────────────────
# 5. Health checks
# ─────────────────────────────────────────────────────────────
echo "🏥 ${TARGET_COLOR} API 헬스체크..."
API_HEALTHY=false
for i in {1..30}; do
  HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:${API_PORT}/api/v1/health 2>/dev/null || true)
  if [ "$HTTP" = "200" ]; then
    echo "✅ API healthy (HTTP $HTTP, attempt ${i})"
    API_HEALTHY=true; break
  fi
  echo "  대기... ($i/30, HTTP $HTTP)"; sleep 2
done
if [ "$API_HEALTHY" = "false" ]; then
  echo "❌ API 헬스체크 실패. 컨테이너 로그:"
  docker logs qlib_api_${TARGET_COLOR} --tail 50 2>&1 || true
  exit 1
fi

echo "🏥 ${TARGET_COLOR} Web 헬스체크..."
WEB_HEALTHY=false
for i in {1..40}; do
  HTTP=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:${WEB_PORT}/ 2>/dev/null || true)
  if [ "$HTTP" = "200" ] || [ "$HTTP" = "302" ] || [ "$HTTP" = "307" ]; then
    echo "✅ Web healthy (HTTP $HTTP, attempt ${i})"
    WEB_HEALTHY=true; break
  fi
  echo "  대기... ($i/40, HTTP $HTTP)"; sleep 2
done
if [ "$WEB_HEALTHY" = "false" ]; then
  echo "❌ Web 헬스체크 실패. 컨테이너 로그:"
  docker logs qlib_web_${TARGET_COLOR} --tail 50 2>&1 || true
  exit 1
fi

# ─────────────────────────────────────────────────────────────
# 6. nginx-waf traffic flip (current → target)
#    The WAF container holds the SSL cert + qlib.tmanager.kr server block
#    and `include`s qlib-url.inc. We swap that include's symlink target
#    and reload nginx inside the container — sub-second flip, no drops.
# ─────────────────────────────────────────────────────────────
echo "🔄 nginx-waf 트래픽 전환: ${CURRENT_COLOR} → ${TARGET_COLOR}"

if ! docker ps --format '{{.Names}}' | grep -q "^${WAF_CONTAINER}$"; then
  echo "❌ WAF 컨테이너(${WAF_CONTAINER})가 떠 있지 않습니다. 트래픽 전환 불가."
  exit 1
fi

# Atomic symlink swap inside the WAF container.
docker exec ${WAF_CONTAINER} sh -c "ln -sfn ${WAF_CONF_DIR}/qlib-url-${TARGET_COLOR}.inc ${ACTIVE_INCLUDE}.tmp && mv -Tf ${ACTIVE_INCLUDE}.tmp ${ACTIVE_INCLUDE}"
echo "  qlib-url.inc → qlib-url-${TARGET_COLOR}.inc"

# Validate then reload — fails fast if the new conf is broken.
if ! docker exec ${WAF_CONTAINER} nginx -t 2>&1 | tee /tmp/nginx-test.$$.log | grep -q "successful"; then
  echo "❌ nginx 설정 검증 실패. 로그:"
  cat /tmp/nginx-test.$$.log
  rm -f /tmp/nginx-test.$$.log
  # Roll back the symlink so existing traffic isn't disrupted.
  docker exec ${WAF_CONTAINER} sh -c "ln -sfn ${WAF_CONF_DIR}/qlib-url-${CURRENT_COLOR}.inc ${ACTIVE_INCLUDE}.tmp && mv -Tf ${ACTIVE_INCLUDE}.tmp ${ACTIVE_INCLUDE}"
  exit 1
fi
rm -f /tmp/nginx-test.$$.log

docker exec ${WAF_CONTAINER} nginx -s reload
echo "✅ nginx-waf reload 완료 (active=${TARGET_COLOR})"

# ─────────────────────────────────────────────────────────────
# 7. Scheduler hand-off (singleton)
#    Old slot's beat goes down BEFORE new slot's beat comes up to avoid
#    the brief window where two beats fire the same cron.
# ─────────────────────────────────────────────────────────────
echo "⏰ Scheduler 핸드오프..."
docker stop qlib_scheduler_${CURRENT_COLOR} 2>/dev/null || true
docker compose -p qlib-${TARGET_COLOR} -f docker-compose.prod.yml --env-file .env.${TARGET_COLOR} up -d scheduler
sleep 3
if ! docker ps --format '{{.Names}}' | grep -q "qlib_scheduler_${TARGET_COLOR}"; then
  echo "❌ scheduler 시작 실패. 로그:"; docker logs qlib_scheduler_${TARGET_COLOR} --tail 50 2>&1 || true
  exit 1
fi
echo "✅ scheduler ${TARGET_COLOR} 가동"

# ─────────────────────────────────────────────────────────────
# 8. Drain & shutdown old slot
# ─────────────────────────────────────────────────────────────
if [ -n "$CURRENT_COLOR" ] && [ -f ".env.${CURRENT_COLOR}" ]; then
  echo "🛑 이전 슬롯(${CURRENT_COLOR}) 종료 (redis 제외)..."
  # redis는 슬롯 간 공유라 down하지 않음. api/worker/scheduler/web만 정리.
  docker compose -p qlib-${CURRENT_COLOR} -f docker-compose.prod.yml --env-file .env.${CURRENT_COLOR} \
    rm -sf api worker scheduler web 2>/dev/null || true
fi

# ─────────────────────────────────────────────────────────────
# 9. Cleanup
# ─────────────────────────────────────────────────────────────
echo "🧹 미사용 이미지 정리..."
docker image prune -f --filter "until=24h"

echo "🎉 배포 완료!"
echo "   활성 슬롯: ${TARGET_COLOR}"
echo "   Web 포트:  ${WEB_PORT}"
echo "   API 포트:  ${API_PORT}"
log_deploy "========== 배포 완료: $TARGET_COLOR =========="
