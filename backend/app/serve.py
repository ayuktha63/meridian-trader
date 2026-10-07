"""`python -m app.serve` — the supported way to start the API.

Exists so the bind address is checked *before* a socket is opened. Started
through the bare `uvicorn` CLI the app has no way to know which host it was
given, so a `--host 0.0.0.0` with no access token used to come up without a
word. Here that combination exits with an explanation instead. The request
middleware in `security.py` enforces the same rule per request either way;
this only makes the misconfiguration loud at the moment it is made.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from .config import settings
from .security import InsecureBindError, assert_safe_bind


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.serve", description=__doc__)
    parser.add_argument("--host", default=None,
                        help="bind address (default: API_HOST, i.e. {})".format(settings.api_host))
    parser.add_argument("--port", type=int, default=None,
                        help="port (default: API_PORT, i.e. {})".format(settings.api_port))
    parser.add_argument("--reload", action="store_true",
                        help="restart on code changes (development only)")
    args = parser.parse_args(argv)

    host = args.host or settings.api_host
    port = args.port or settings.api_port
    try:
        assert_safe_bind(host, settings.app_access_token)
    except InsecureBindError as exc:
        print("\n{}\n".format(exc), file=sys.stderr)
        return 2

    # The lifespan re-checks the host it believes it is on; keep that belief
    # true for this process and for the worker `--reload` spawns.
    settings.api_host = host
    os.environ["API_HOST"] = host

    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
