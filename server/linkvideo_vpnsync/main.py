from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "linkvideo_vpnsync.api:app",
        host=settings.bind_host,
        port=settings.bind_port,
        workers=1,
        access_log=True,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
