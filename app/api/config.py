from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "QLIB_API_"}

    # qlib
    provider_uri: str = "~/.qlib/qlib_data/kr_data"
    region: str = "us"  # base region; KR overrides applied via exchange kwargs

    # celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"

    # api
    cors_origins: list[str] = ["http://localhost:5000"]
    debug: bool = False

    # backtest result storage
    results_dir: str = "./app/api/results"

    # KIS (Korea Investment & Securities) Open API
    # Set KIS_ENV=paper for 모의투자, real for 실전. Defaults to paper for safety.
    kis_env: str = "paper"
    kis_app_key: str = ""
    kis_app_secret: str = ""
    kis_account_no: str = ""  # "12345678-01" 형태

    # ─── 2번째 실계좌 (cafereal) ────────────────────────────────
    # 카페 스크리너 픽을 실제로 사는 별도 계좌. 비워두면 cafereal 전략이
    # 통째로 비활성이다(스케줄은 돌지만 즉시 no_account 로 반환).
    #
    # ⚠ appkey 를 기존 계좌와 **다른 것**으로 발급받아야 한다. KIS 의 토큰
    # 발급 한도와 초당 호출 한도는 계좌가 아니라 **appkey 단위**라, 같은 키를
    # 공유하면 09:00 실계좌 주문과 15:28 카페 주문이 서로의 한도를 깎는다.
    # (토큰·쿨다운·API 슬롯 redis 키는 이미 appkey 해시로 분리돼 있다.)
    kis_cafe_env: str = ""          # 비우면 kis_env 를 따름
    kis_cafe_app_key: str = ""
    kis_cafe_app_secret: str = ""
    kis_cafe_account_no: str = ""   # "12345678-01" 형태
    kis_cafe_account_product: str = ""
    kis_account_product: str = "01"  # 종합매매 default

    # ─── Real-trading safety rails ──────────────────────────────────
    # Hard ceiling on a single order's notional (qty × price), in KRW.
    # A sizing bug, a bad quote, or a stale seed can otherwise turn one
    # order into the whole account. 0 disables the check (paper default is
    # generous; set this to something real before flipping KIS_ENV=real).
    live_max_order_value: float = 3_000_000.0
    # 하루에 원장으로 봉합할 수 있는 불일치 종목 수 상한. 초과하면 아무것도
    # 쓰지 않고 알림만 낸다 — 잔고가 부분 응답이거나 조회가 이상한 날 가짜
    # 청산이 원장에 폭주하는 것을 막는다. 사람이 보고 판단할 몫이다.
    live_manual_exit_max_per_day: int = 5
    # Fees applied to simulated + reconciled fills so the curve is not
    # systematically optimistic. KIS 온라인 수수료 ~0.014% (buy and sell),
    # 거래세 0.18% (sell only, 2026 기준 코스피 0.03%+농특세 0.15%/코스닥 0.18%).
    # Set to 0 to restore the old fee-free accounting.
    live_fee_rate: float = 0.00014
    live_tax_rate: float = 0.0018
    # Live trading database
    live_db_url: str = "sqlite:///./app/api/db/live.sqlite"
    # Per-strategy seed cash for the equity curve baseline.
    # - open  : MUST match the KIS paper account's granted virtual funds.
    #           Paper accounts expire and get re-issued with a new grant —
    #           update this (or the QLIB_API_LIVE_SEED_CASH_OPEN env) every
    #           time the account is renewed. 2026-07-28 renewal granted 1천만
    #           (the old account had 1억, which made the ROI card read -90%).
    # - close : matches the DB-only simulated portfolio seed (1천만 default)
    # If the chart shows wildly off percentages, the seed here doesn't match
    # what ended up in starting/ending_equity rows — set the env override.
    live_seed_cash_open: float = 10_000_000.0
    # Daily cap on NEW buys for the real account. TopkDropout only swaps n_drop
    # per day, which left the account stuck at 4 of 10 slots (투입률 20~41%(실측)). Buys
    # now fill empty slots up to this number — 4 ramps 4→8→10 over two sessions
    # instead of taking every position on a single day's print. Set to topk to
    # fill immediately, or to n_drop (2) to restore the old behaviour.
    live_max_buys_per_day: int = 4
    live_seed_cash_close: float = 10_000_000.0
    # - flow  : same seed and execution as close, picks re-ranked by
    #           기관/외국인 net buying. Equal seeds are REQUIRED for the A/B
    #           to mean anything — the curves are compared against each other.
    live_seed_cash_flow: float = 10_000_000.0
    # Exit-rule matrix sims (2026-08-04): same signal/seed as close, only the
    # exit (trail/scale) or entry (limit) mechanics differ.
    live_seed_cash_trail: float = 10_000_000.0
    live_seed_cash_scale: float = 10_000_000.0
    live_seed_cash_limit: float = 10_000_000.0

    # limit strategy: rest 5 virtual limit buys at prev_close × (1−discount),
    # rank-priority fills capped at max_fills per day, unfilled = cancelled.
    live_limit_discount: float = 0.03
    live_limit_candidates: int = 5
    live_limit_max_fills: int = 2

    # cafe strategy: recommender-mimic screener sim. The stop comes from the
    # screener's structural level, capped at −stop_cap below entry.
    # 2026-08-13 사용자 지시: slots 4 → 10 (종목당 25% → 10%) — 집중 베팅
    # 리스크 축소, 회당 계좌 리스크 3.75% → 1.5%. 곡선 해석 주의 (INSIGHTS §2).
    live_seed_cash_cafe: float = 10_000_000.0
    live_cafe_slots: int = 10
    live_cafe_max_buys: int = 2
    live_cafe_stop_cap: float = 0.15
    # cafeopen: cafe's execution twin (2026-08-14). Buys the SAME codes cafe
    # bought yesterday, with the SAME exits — only the entry differs: a −3%
    # limit off today's open, cancelled unfilled at 10:00. Slots/stop_cap are
    # deliberately NOT duplicated (it reads live_cafe_*) so the two curves
    # can never drift apart on anything but the entry. Seed must equal cafe's.
    live_seed_cash_cafeopen: float = 10_000_000.0
    # cafecool — cafe's entry-condition twin. Same seed/slots on purpose: the
    # A/B only means something if capital deployment is identical and the ret20
    # ceiling is the single moving part.
    live_seed_cash_cafecool: float = 10_000_000.0
    # ret20 상한(%). cafe 는 하한 +30%만 있고 상한이 없어 +368%까지 통과했다.
    # 20건 실측에서 50%를 경계로 D+5 중앙값이 +3.19% / −9.50% 로 갈렸다.
    live_cafecool_ret20_max: float = 50.0
    # cafereal — 실계좌라 시드가 아니라 예수금이 출발점이다. 이 값은
    # 곡선 기준선(수익률 0% 지점) 표기에만 쓴다.
    live_seed_cash_cafereal: float = 10_000_000.0
    live_cafeopen_discount: float = 0.03
    # The resting window: order placed at 09:00, judged against the session
    # range at this hour. 10:00 = the first hour only.
    live_cafeopen_cutoff_hour: int = 10

    live_seed_cash_surge: float = 10_000_000.0
    live_surge_slots: int = 10
    live_surge_max_buys: int = 2

    # Telegram trade alerts (existing 'trading' BotFather bot +
    # 시스템트레이딩알림 group). Empty = notifications disabled.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Sim bracket exits (close + flow). 2026-08-03 evening revision (user):
    # take-profit +10%; stop = the LOWEST $low of the `low_window` trading
    # days before entry × (1 − low_buffer) — the cafe-recommender-style
    # structural stop ("전 저점"). `sl` now acts as a RISK CAP: the stop is
    # never placed deeper than avg × (1 − sl), and is the fallback when no
    # valid prior low exists (e.g. entry at fresh lows).
    live_close_bracket_tp: float = 0.10
    live_close_bracket_sl: float = 0.10
    live_close_bracket_low_window: int = 10
    live_close_bracket_low_buffer: float = 0.01

    # Auth — JWT-based admin login. JWT secret MUST be overridden in production
    # (default is a sentinel; rotating it invalidates all existing sessions).
    # Admin user is seeded on first startup from these credentials if the users
    # table is empty (idempotent — subsequent startups are no-ops).
    jwt_secret: str = "CHANGE_ME_IN_PROD"
    admin_username: str = "admin"
    admin_password: str = "CHANGE_ME_IN_PROD"
    session_cookie_name: str = "qlib_session"
    session_max_age_seconds: int = 86400  # 24h

    # Login brute-force protection — IP-based sliding-window counter in Redis.
    # Two tiers: a sustained-fail threshold for normal brute-force, plus a
    # higher burst threshold for credential-stuffing that hits many usernames.
    login_fail_threshold: int = 5
    login_fail_window_sec: int = 300       # 5 min counter TTL
    login_lockout_sec: int = 900           # 15 min lockout after fail_threshold
    login_ip_burst_threshold: int = 20
    login_ip_burst_lockout_sec: int = 3600  # 1 hour lockout after burst_threshold
    # How many proxies sit in front of the API. client_ip() counts this many
    # entries back from the RIGHT of X-Forwarded-For, because only the hops a
    # trusted proxy appended are non-forgeable. 1 = the single nginx in
    # infra/nginx/qlib.tmanager.kr.conf. Raising it without actually adding a
    # proxy would read an attacker-supplied value again.
    trusted_proxy_hops: int = 1


settings = Settings()
