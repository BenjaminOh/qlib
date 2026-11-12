# 비밀값 목록 — qlib (허브)

> `web-app-security-audit` 스킬 `ai-era-threats.md` §5. **값은 절대 적지 않는다.** 유출됐을 때 30분 안에 교체할 수 있게 "어디에·어떻게"만 적는다.
> 키를 만들거나 바꾸거나 지울 때마다 이 표도 같은 커밋에서 고친다. 작성 2026-11-12.

모든 서버 값은 76번 `/home/qlib/.env`(600)에 있고, 배포 스크립트가 `.env.blue`·`.env.green` 으로 복사한다.
**교체는 세 파일 모두**(sed 금지 — 파이썬으로 값만 바꾼다, INSIGHTS 2026-09-21) → 장 마감 뒤 `--env-file .env.<색> --no-build --force-recreate api worker scheduler`. 백업은 `/root/backup/qlib-env-<날짜>/`.

| 이름 | 용도 | 발급처 | 발급일 | 만료 | 교체하면 깨지는 것 |
|---|---|---|---|---|---|
| `KIS_APP_KEY`·`KIS_APP_SECRET` + `QLIB_API_KIS_APP_KEY`·`_SECRET` (같은 값 두 벌) | 허브 시세 조회 + 기본 계좌(open) — 이름 "qlib-hub-시세", 모의계좌 50221363 에 추가 발급한 앱키 `PSaO…` | KIS Developers(고객ID $1272661) | 2026-11-12 | 앱키 2027-11-02 · **모의투자 참가 2027-01-30** | 교체~재생성 사이 스크리너·시세. 기본 계좌 open 주문은 계좌번호(50199531, 해지됨)로 일부러 막혀 있음 |
| `QLIB_API_KIS_COOL_APP_KEY`·`_SECRET` | 냉각 계좌(coolreal) 주문·잔고, 시세 폴백 1순위 | KIS Developers(**다른 고객 ID**) | 2026-09-15 무렵 | **모의투자 3개월 — 12월 중순 추정, 확인 필요** | coolreal 매매 전체 |
| 카페 계좌 키 (DB `trading_accounts.app_key_enc`·`app_secret_enc`, Fernet) | 카페 계좌(cafereal) | KIS Developers(다른 고객 ID) | ? | **2026-11 현재 EGW00105 거부 중** | cafereal 매매 |
| `QLIB_API_SECRETS_KEY` | DB 의 계좌 자격증명 암호화(Fernet) | 직접 생성 | ? | 없음 | **바꾸면 DB 에 저장된 계좌 키를 못 푼다** — 새 키로 재암호화 스크립트 먼저 |
| `QLIB_API_JWT_SECRET` | 로그인 JWT 서명(PyJWT, 2026-11-12). **웹서비스 종료로 외부 로그인 경로 없음** — 내부 API 만 | 직접 생성 | 2026-10-27 교체 | 없음 | 모든 로그인 세션 무효(재로그인) |
| `QLIB_API_ADMIN_PASSWORD` | 관리자 `benadmin` 초기 비번 | 직접 생성 | ? | 없음 | 로그인 |
| `QLIB_API_FEED_TOKENS` | runner 가 허브 feed 를 읽는 토큰 | 직접 생성 | 2026-10-27 | 없음 | **runner 의 `RUNNER_FEED_TOKEN` 과 짝** — 둘을 같이 바꾼다 |
| `QLIB_API_TELEGRAM_BOT_TOKEN` | 운영 알림 | BotFather | ? | 없음 | 알림 |
| `LIVE_DB_URL` | 운영 PostgreSQL `qlib_live` 접속(비번 포함) | 직접 생성 | 2026-09-21 | 없음 | 전체 — DB 롤 비번과 같이 |
| `REDIS_URL` | Celery·토큰 캐시(app-network 내부, 비번 없음) | — | — | — | — |
| `/root/deploy/nginx-waf/conf.d/qlib.htpasswd` | (안 씀) 옛 웹 앞단 기본 인증 — 2026-11-12 웹 종료로 참조 없음, 지워도 됨 | — | — | — | 없음 |

## 만료 알림
- KIS 모의투자 참가 기간(3개월)이 가장 위험하다 — 2026-10-28 기본 계좌가 이렇게 끊겼다. **허브 2027-01-30, 냉각 확인 필요.** 7일 전 알림: 캘린더 또는 서버 cron(미설정 — 과제).

## 유출 의심 시 순서
1. 이 표에서 줄을 찾는다 → 2. 발급처에서 **먼저 폐기**(KIS 는 해당 줄 "해지") → 3. 새 값 발급·반영 → 4. 접근 로그 확인 → 5. 발급일 갱신
