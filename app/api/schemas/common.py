from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    qlib_initialized: bool
    # provider_uri was reported here until 2026-08-18. The endpoint is
    # unauthenticated and the value is a container-internal absolute path that
    # also reveals the process runs as root — nothing a liveness check needs.
    # Optional rather than removed so an older client parsing the field keeps
    # working.
    provider_uri: str | None = None


class ErrorResponse(BaseModel):
    detail: str
