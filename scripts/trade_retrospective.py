"""Trade retrospective — CLI wrapper around the shared engine.

The heavy lifting lives in app.api.services.retrospective (also serving
GET /live/retro). This wrapper prints the full JSON for the weekly
docs/07-retro/ report. Run inside a worker/api container:

    ssh rocky-prod "docker exec -i <worker> python -" \
        < scripts/trade_retrospective.py
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, "/app")


def main() -> None:
    from app.api.core.qlib_manager import ensure_qlib_initialized
    ensure_qlib_initialized()
    from app.api.services.retrospective import build_retro
    print(json.dumps(build_retro("open"), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
