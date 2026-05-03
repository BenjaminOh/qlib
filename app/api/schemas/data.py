from pydantic import BaseModel


class CalendarResponse(BaseModel):
    dates: list[str]
    count: int


class InstrumentItem(BaseModel):
    code: str
    start_time: str
    end_time: str


class InstrumentsResponse(BaseModel):
    instruments: list[InstrumentItem]
    count: int
    market: str


class FeatureRow(BaseModel):
    datetime: str
    instrument: str
    values: dict[str, float | None]


class FeaturesResponse(BaseModel):
    data: list[FeatureRow]
    count: int
    fields: list[str]


class MarketInfo(BaseModel):
    name: str
    description: str


class MarketsResponse(BaseModel):
    markets: list[MarketInfo]
