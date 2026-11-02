"""Start the engine in the foreground:  python -m app.run   (Ctrl+C to stop gracefully)"""
import uvicorn

from app.api.main import app
from app.core.config import get_settings


def main() -> None:
    s = get_settings()
    config = uvicorn.Config(
        app, host=s.API_HOST, port=s.API_PORT, log_config=None, access_log=False,  # no GET/POST lines in any log
        ws_max_size=65536, ws_ping_interval=20, ws_ping_timeout=20, timeout_keep_alive=30, backlog=2048,
    )
    server = uvicorn.Server(config)
    app.state.server = server  # lets POST /v1/admin/shutdown stop the engine gracefully
    server.run()


if __name__ == "__main__":
    main()
