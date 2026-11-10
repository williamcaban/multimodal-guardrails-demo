"""Fixtures: a stub NeMo Guardrails server and a factory that starts the UI server."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from helpers import ALLOWED_BODY, BLOCKED_BODY, StubNemo
from mmdemo.server import App, make_server
from mmdemo.settings import Settings


@pytest.fixture
def stub_nemo() -> Iterator[StubNemo]:
    stub = StubNemo(url="")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            stub.requests.append(
                {"path": self.path, "body": body, "auth": self.headers.get("Authorization")}
            )
            config_id = body["guardrails"]["config_id"]
            payload = json.dumps(ALLOWED_BODY if config_id == "no-rails" else BLOCKED_BODY).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            """Silence request logging."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    stub.url = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield stub
    server.shutdown()
    server.server_close()


@pytest.fixture
def start_ui() -> Iterator[Callable[[Settings], str]]:
    """Start the UI server for the given settings; returns its base URL."""
    servers: list[ThreadingHTTPServer] = []

    def _start(settings: Settings) -> str:
        server = make_server(App(settings), host="127.0.0.1", port=0)
        servers.append(server)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield _start
    for server in servers:
        server.shutdown()
        server.server_close()
