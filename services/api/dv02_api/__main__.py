"""python -m dv02_api [--profile paper|live] [--port 8000]

Serves /api/v1 on 127.0.0.1 only. The dashboard's `npm run dev:api` proxies
/api to it.
"""

from __future__ import annotations

import argparse
import logging
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m dv02_api", description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", choices=["paper", "live"], default=os.environ.get("DV02_PROFILE", "paper"))
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost only)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("DV02_API_PORT", "8000")))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # ib_async logs every failed reconnect; the API reports connection state itself.
    logging.getLogger("ib_async").setLevel(logging.CRITICAL)

    import uvicorn

    from .app import create_app

    # The default asyncio loop: ib_async is tested against it, not uvloop.
    uvicorn.run(create_app(args.profile), host=args.host, port=args.port, loop="asyncio", log_level="info")


if __name__ == "__main__":
    main()
