import os
import sys
from pathlib import Path

from . import VERSION, server

PORT = 8099
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def main():
    mode = "prod" if os.environ.get("SUPERVISOR_TOKEN") else "dev"
    host = "0.0.0.0" if mode == "prod" else "127.0.0.1"
    httpd = server.make_server(host, PORT, STATIC_DIR, mode)
    print(f"MealPrep Planner {VERSION} listening on {host}:{PORT} ({mode})", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
