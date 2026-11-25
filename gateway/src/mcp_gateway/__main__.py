"""Run the gateway: ``python -m mcp_gateway`` (or the ``mcp-gateway`` script)."""

from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .settings import GatewaySettings


def main() -> None:
    settings = GatewaySettings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        server_header=False,
    )


if __name__ == "__main__":
    main()
