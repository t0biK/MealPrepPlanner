import os
import sys
from pathlib import Path

from . import VERSION, server, worker

PORT = 8099
APP_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = APP_DIR / "static"


def main():
    mode = "prod" if os.environ.get("SUPERVISOR_TOKEN") else "dev"
    host = "0.0.0.0" if mode == "prod" else "127.0.0.1"
    data_dir = Path("/data") if mode == "prod" else APP_DIR / "data"
    httpd = server.make_server(host, PORT, STATIC_DIR, data_dir, mode)
    worker.start(data_dir)
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
