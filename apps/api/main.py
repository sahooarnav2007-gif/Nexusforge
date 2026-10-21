"""API entrypoint: uvicorn runner."""
from __future__ import annotations

import uvicorn

from api.app import create_app
from core.config import get_settings

app = create_app()


def main() -> None:
    s = get_settings()
    uvicorn.run(
        "apps.api.main:app",
        host=s.api_host,
        port=s.api_port,
        log_level="info",
        workers=1,  # in-process worker queue lives in this process
    )


if __name__ == "__main__":
    main()