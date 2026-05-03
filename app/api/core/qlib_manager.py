"""Manages qlib initialization in Celery worker processes.

Each worker process gets its own qlib.init() call to avoid
singleton conflicts with the FastAPI server process.
"""

import qlib
from qlib.config import C

from ..config import settings

_initialized = False


def ensure_qlib_initialized():
    """Initialize qlib in the current process if not already done."""
    global _initialized
    if not _initialized:
        qlib.init(provider_uri=settings.provider_uri, region=settings.region)
        _initialized = True


def is_initialized() -> bool:
    return _initialized
