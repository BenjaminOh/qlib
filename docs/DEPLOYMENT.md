# qlib 운영 배포 가이드

배포 대상 환경 (확정):

| 항목 | 값 |
|---|---|
| 호스트 | `rocky-monitor` (112.175.30.76) |
| 도메인 | `qlib.tmanager.kr` |
| Git remote | `git@github.com:BenjaminOh/qlib.git` |
| 라우팅 | `nginx-proxy → nginx-waf-blue` (rocky-monitor 기존 패턴 재사용) |
| 포트 | Blue web `25022` / api `25023`, Green web `25024` / api `25025` (newsro 25011-12, mail-news 25021 직후 sequential) |
| Registry | `127.0.0.1:5000/qlib-{api,web}` (build-agent 노드) |
| KIS | 미발급 → mock 모드로 시작 |

권한 분담:
- **사용자(직접)**: DNS · certbot · WAF conf 추가 · Jenkins job 등록 · `docker compose up`
- **Claude(읽기 전용)**: `ssh rocky-monitor containers / container_log / container_info / nginx_test waf` 로 검증·트러블슈팅
- **Jenkins(자동)**: 빌드 → registry push → SSH로 deploy.sh 실행

---

## 0. 필요한 모든 산출물 (이미 만들어짐)

```
.
├── Dockerfile.prod                       # api/worker/scheduler 멀티스테이지
├── docker-compose.prod.yml               # 4 서비스 + Blue/Green 변수
├── deploy.sh                             # nginx-waf flip 패턴
├── Jenkinsfile                           # build → push → deploy
├── .env.prod.example                     # /home/qlib/.env 양식
├── app/frontend/Dockerfile               # Next.js standalone
├── app/frontend/next.config.js           # output:standalone
└── infra/nginx/
    ├── qlib.tmanager.kr.conf           # WAF server block (호스트에 복사)
    ├── qlib-url-blue.inc                 # Blue 슬롯 upstream (host:25022/23)
    └── qlib-url-green.inc                # Green 슬롯 upstream (host:25024/25)
```

---

## 1. DNS 등록 (사용자 직접) — 5분

likeweb.co.kr DNS 관리자에서:
```
타입: A
이름: qlib
값:   112.175.30.76
TTL:  300
```

전파 확인:
```bash
dig +short qlib.tmanager.kr   # → 112.175.30.76 나와야 함
```

---

## 2. SSL 인증서 발급 (사용자 직접) — 10분

rocky-monitor에 root SSH 접속 후. 기존 newsro와 동일 패턴(standalone challenge):

```bash
# nginx-proxy가 80을 잡고 있으니 잠시 정지하고 발급
docker stop nginx-proxy
certbot certonly --standalone -d qlib.tmanager.kr \
  --email ohsjwe@likeweb.co.kr --agree-tos --no-eff-email
docker start nginx-proxy

# WAF 컨테이너가 마운트하는 경로(/etc/nginx/ssl)는 호스트 /etc/letsencrypt 를 그대로 가리킴
# (newsro 등 기존 사이트와 동일 구조라 추가 마운트 작업 불필요)
ls /etc/letsencrypt/live/qlib.tmanager.kr/   # fullchain.pem, privkey.pem 확인
```

자동 갱신은 기존 `certbot-renew` 시스템 타이머가 그대로 처리.

---

## 3. WAF 사이트 추가 (사용자 직접) — 5분

호스트에 conf 파일 복사 후 reload. 호스트 `/etc/letsencrypt`는 nginx-waf-blue의 `/etc/nginx/ssl`로 마운트된 상태입니다 (기존 사이트들과 동일).

```bash
# 로컬에서 호스트로 복사
scp infra/nginx/qlib.tmanager.kr.conf root@112.175.30.76:/etc/nginx/conf.d/
scp infra/nginx/qlib-url-blue.inc       root@112.175.30.76:/etc/nginx/conf.d/
scp infra/nginx/qlib-url-green.inc      root@112.175.30.76:/etc/nginx/conf.d/

# rocky-monitor에서:
ssh root@112.175.30.76
# nginx-waf-blue가 /etc/nginx/conf.d 를 호스트와 공유 마운트하는지 inspect로 확인
docker inspect nginx-waf-blue --format '{{range .Mounts}}{{.Source}}->{{.Destination}}{{"\n"}}{{end}}' \
  | grep conf.d

# 마운트가 안 되어 있으면 컨테이너 안에 직접 cp:
docker cp /etc/nginx/conf.d/qlib.tmanager.kr.conf  nginx-waf-blue:/etc/nginx/conf.d/
docker cp /etc/nginx/conf.d/qlib-url-blue.inc        nginx-waf-blue:/etc/nginx/conf.d/
docker cp /etc/nginx/conf.d/qlib-url-green.inc       nginx-waf-blue:/etc/nginx/conf.d/

# 초기 active = blue
docker exec nginx-waf-blue ln -sfn /etc/nginx/conf.d/qlib-url-blue.inc /etc/nginx/conf.d/qlib-url.inc

# 검증 후 reload
docker exec nginx-waf-blue nginx -t
docker exec nginx-waf-blue nginx -s reload
```

Claude로 검증:
```bash
ssh rocky-monitor nginx_test waf       # configuration ok 출력
ssh rocky-monitor nginx_conf waf | grep qlib.tmanager.kr   # 새 server block 확인
```

---

## 4. 호스트 디렉터리 + .env 작성 (사용자 직접) — 5분

```bash
ssh root@112.175.30.76
mkdir -p /home/qlib/data/{qlib,db,redis}
chown -R 1000:1000 /home/qlib   # 컨테이너의 root 사용자가 read/write
cp .env.prod.example /home/qlib/.env
$EDITOR /home/qlib/.env
```

`.env`에서 채울 부분:
- KIS 키는 **미발급 상태이므로 placeholder 그대로** (`PASTE_PAPER_APPKEY_HERE` 등). 자동으로 mock 모드로 동작.
- `REDIS_URL` — rocky-monitor에 공유 redis 없는 듯 → docker-compose가 새 redis 띄우려면 compose에 redis 서비스 추가 또는 기존 redis 컨테이너 재사용 결정 필요. 일단 다음 옵션 중 하나:
  ```
  REDIS_URL=redis://localhost:6379/2          # 호스트의 redis (있으면)
  REDIS_URL=redis://qlib-redis:6379/0         # qlib 전용 redis 컨테이너 (compose 추가 필요)
  ```
- `LIVE_DB_URL` — SQLite로 시작 (PostgreSQL global_shared_db 재사용은 후속):
  ```
  LIVE_DB_URL=sqlite:////app/db/live.sqlite
  ```

⚠️ **redis 미해결**: 현재 docker-compose.prod.yml은 redis를 외부 의존으로 가정. mock 모드에서도 Celery는 redis가 필요. **다음 단계 중 하나** 선택:

**옵션 A — qlib 전용 redis 컨테이너 추가** (간단, 권장):
`docker-compose.prod.yml`에 redis 서비스 1개 추가 후 `REDIS_URL=redis://redis:6379/0`. 색깔 토글 안 함 (Blue/Green 모두 같은 redis 공유).

**옵션 B — 호스트의 기존 redis 재사용**:
rocky-monitor에 redis 컨테이너 없음 → `apt install redis` 후 호스트에서 listen.

**옵션 C — global_shared_db 같은 공유 redis 컨테이너 신설**:
다른 서비스도 쓸 수 있게 별도 컨테이너 1개. 후속 작업.

→ 지금은 **옵션 A**로 진행. 다음 단계에서 compose 파일 1줄 추가하겠습니다 (Claude 작업).

---

## 5. GitHub repo 생성 + 첫 push (사용자 직접) — 5분

```bash
# GitHub 웹에서 https://github.com/new
# Repository name: qlib
# Owner: BenjaminOh
# Visibility: Private (권장)
# README/.gitignore/license 모두 빈 채로 Create

cd /Users/benjaminoh/dev/project/bot/qlib
git remote -v   # 현재 remote가 microsoft/qlib 라면 추가 remote 필요
git remote add deploy git@github.com:BenjaminOh/qlib.git
git checkout -b main 2>/dev/null || git checkout main
git push deploy main
```

⚠️ **현재 repo가 microsoft/qlib fork** 라면 BenjaminOh 계정 SSH 키가 GitHub에 등록돼야 함. 미등록 시:
```bash
ssh -T git@github.com   # 인증 확인
# 안 되면 https://github.com/settings/keys 에 ~/.ssh/id_ed25519.pub 등록
```

---

## 6. Jenkins 설정 (사용자 직접) — 10분

Jenkins UI (rocky-monitor:8080) 접속:

1. **Credentials**: 좌측 → Credentials → Global → Add
   - Kind: SSH Username with private key
   - ID: `github-key-benjaminoh`  ← Jenkinsfile에서 sshagent로 호출하는 값과 맞춰야 함
   - Username: `git`
   - Private Key: BenjaminOh 계정의 GitHub 등록 키

2. **Jenkinsfile의 credential 이름 맞추기** (Claude 처리):
   - 현재 `'github-key-likeweb'` → `'github-key-benjaminoh'` 로 변경 필요

3. **Pipeline 등록**: New Item → Pipeline
   - Name: `qlib`
   - Pipeline → Definition: Pipeline script from SCM
   - SCM: Git, URL `git@github.com:BenjaminOh/qlib.git`, Credentials `github-key-benjaminoh`
   - Branch: `*/main`
   - Script Path: `Jenkinsfile`
   - Save

4. **Build agent 라벨 확인**:
   - Jenkinsfile은 `build-agent-135` 와 `build-agent` 라벨을 요구
   - likeweb/pm 빌드와 동일 노드면 그대로
   - 노드 라벨 다르면 Jenkinsfile 수정 필요 (Claude 처리)

5. **첫 빌드**: "Build Now" 클릭

---

## 7. 첫 배포 (Jenkins가 트리거)

Jenkins가 다음을 자동 수행:
1. Git pull (build-agent-135)
2. `docker compose -f docker-compose.prod.yml build --parallel api web`
3. `docker push 127.0.0.1:5000/qlib-{api,web}:{BUILD_NUMBER,latest}`
4. SSH로 rocky-monitor → `cd /home/qlib && IMAGE_TAG=N ./deploy.sh`

deploy.sh는:
1. 활성 슬롯 감지 → 첫 배포는 Blue
2. `docker compose -p qlib-blue up -d api worker web`
3. 헬스체크 `localhost:25023/api/v1/health`, `localhost:25022/`
4. WAF의 qlib-url.inc 심볼릭 링크 → qlib-url-blue.inc + nginx reload
5. scheduler(beat) Blue 시작
6. (재배포 시) 이전 슬롯 down

---

## 8. KOSPI 데이터 시드 (1회) — 8분

deploy.sh가 끝난 뒤 호스트에서:
```bash
ssh root@112.175.30.76
docker exec qlib_api_blue python scripts/kr_data_fetch.py \
  --start 2023-01-01 --end $(date +%F) \
  --csv_dir /tmp/kr_csv --qlib_dir /root/.qlib/qlib_data/kr_data
```

Claude로 진행 확인:
```bash
ssh rocky-monitor container_log qlib_api_blue   # fetch 진행 로그
```

데이터가 호스트의 `/home/qlib/data/qlib` 에 영구 저장되어 다음 배포부터는 재다운로드 불필요.

---

## 9. Smoke test (배포 후 5분 내) — Claude로 검증 가능

```bash
# 사용자 로컬에서:
curl https://qlib.tmanager.kr/api/v1/health
# → {"status":"ok","qlib_initialized":true,...}

curl https://qlib.tmanager.kr/api/v1/live/balance
# → {"cash":100000000.0,"mode":"mock",...}

# 브라우저로:
https://qlib.tmanager.kr/live              # 모의투자 대시보드 (mock 배지)
https://qlib.tmanager.kr/backtest/new      # 백테스트 페이지
```

Claude로 컨테이너 상태:
```bash
ssh rocky-monitor containers              # qlib_* 5개 컨테이너 Up 확인
ssh rocky-monitor container_log qlib_api_blue
ssh rocky-monitor container_log qlib_scheduler_blue
ssh rocky-monitor container_log qlib_worker_blue
```

---

## 10. KIS 발급 후 활성화 (이후 단계)

```bash
ssh root@112.175.30.76
$EDITOR /home/qlib/.env       # KIS_APP_KEY/SECRET/ACCOUNT_NO 입력
docker compose -p qlib-blue -f docker-compose.prod.yml --env-file .env.blue \
  restart api worker scheduler
```

브라우저로 `/live` 새로고침 → 배지가 `mock` → `paper` 로 바뀌면 성공. 다음 평일 15:35 KST에 첫 자동 시그널이 생성되고 09:00 KST에 자동 발주.

---

## 11. 알려진 미해결 / 다음 작업

| 이슈 | 영향 | 해결 |
|---|---|---|
| **redis 부재** | Celery worker/beat 동작 불가 | docker-compose.prod.yml에 redis 서비스 1개 추가 (다음 turn) |
| **scheduler singleton 1~3초 갭** | 평일 09:00·15:35 발주 시각에 배포는 위험 | deploy.sh에 시각 가드 추가 또는 운영 룰로 |
| **Jenkins credential 이름** | `github-key-likeweb` → `github-key-benjaminoh` | Jenkinsfile 수정 |
| **build-agent 라벨** | likeweb/pm용 라벨 그대로 사용 | 별 노드면 라벨 변경 |
| **rollback 자동화 없음** | 헬스체크 실패 시 트래픽은 안 넘어가지만 컨테이너는 살아있음 | 수동 down |
| **DB 백업 없음** | live.sqlite 손상 시 매매 이력 손실 | 야간 cron 추가 |
