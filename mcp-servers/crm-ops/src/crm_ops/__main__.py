"""Run crm-ops: ``python -m crm_ops`` (or the ``crm-ops-mcp`` script)."""

from __future__ import annotations

import uvicorn

from .server import create_app
from .settings import CrmOpsSettings


def main() -> None:
    settings = CrmOpsSettings()
    app = create_app(settings)
    uvicorn.run(app.asgi, host=settings.host, port=settings.port, server_header=False)


if __name__ == "__main__":
    main()
