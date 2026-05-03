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


settings = Settings()
