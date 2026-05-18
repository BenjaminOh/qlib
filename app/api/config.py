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
