from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    qlib_initialized: bool
    provider_uri: str


class ErrorResponse(BaseModel):
    detail: str
