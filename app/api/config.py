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
    kis_account_product: str = "01"  # 종합매매 default
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


settings = Settings()
