"""Market data endpoints — wraps qlib.data.D APIs."""

from fastapi import APIRouter, HTTPException, Query

from ..core.kr_market import KR_MARKETS
from ..schemas.data import (
    CalendarResponse,
    FeaturesResponse,
    InstrumentsResponse,
    MarketInfo,
    MarketsResponse,
)
from ..services import data_service

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/calendar", response_model=CalendarResponse)
def get_calendar(
    start: str = Query(..., description="Start date (YYYY-MM-DD)"),
    end: str = Query(..., description="End date (YYYY-MM-DD)"),
    freq: str = Query("day", description="Frequency: day, 1min, etc."),
):
    try:
        dates = data_service.get_calendar(start, end, freq)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return CalendarResponse(dates=dates, count=len(dates))


@router.get("/instruments", response_model=InstrumentsResponse)
def get_instruments(
    market: str = Query(..., description="Market name (e.g. kospi200, csi300)"),
):
    try:
        instruments = data_service.get_instruments(market)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return InstrumentsResponse(
        instruments=instruments,
        count=len(instruments),
        market=market,
    )


@router.get("/features", response_model=FeaturesResponse)
def get_features(
    instruments: str = Query(..., description="Comma-separated instrument codes"),
    fields: str = Query(..., description="Comma-separated field expressions (e.g. $close,$volume)"),
    start: str = Query(..., description="Start date"),
    end: str = Query(..., description="End date"),
    freq: str = Query("day", description="Frequency"),
):
    inst_list = [s.strip() for s in instruments.split(",")]
    field_list = [s.strip() for s in fields.split(",")]
    try:
        data = data_service.get_features(inst_list, field_list, start, end, freq)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return FeaturesResponse(data=data, count=len(data), fields=field_list)


@router.get("/markets", response_model=MarketsResponse)
def list_markets():
    markets = [MarketInfo(name=name, description=desc) for name, desc in KR_MARKETS.items()]
    return MarketsResponse(markets=markets)
