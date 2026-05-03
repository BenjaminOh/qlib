"""Korean stock market constants and configuration."""

REG_KR = "kr"

# KRX exchange default parameters for qlib's Exchange / get_exchange()
KR_EXCHANGE_KWARGS = {
    "limit_threshold": 0.30,    # KOSPI/KOSDAQ 30% daily price limit
    "deal_price": "close",
    "open_cost": 0.00015,       # buy commission (~0.015%)
    "close_cost": 0.00315,      # sell commission + 0.3% securities transaction tax
    "min_cost": 0,
    "trade_unit": 1,            # KRX standard lot
}

# Predefined Korean market instrument lists
KR_MARKETS = {
    "kospi200": "KOSPI 200 index constituents",
    "kosdaq150": "KOSDAQ 150 index constituents",
    "kospi": "All KOSPI listed stocks",
    "kosdaq": "All KOSDAQ listed stocks",
}
