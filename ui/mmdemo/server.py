"""HTTP server: serves the demo page and runs one image through NeMo Guardrails twice.

For each request it calls the ``passthrough`` configuration (no rails, the "unprotected" model) and
the configuration for the selected guardrail mode, in parallel, so the page can show them side by
side.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mmdemo.nemo import GuardResult, call_nemo
from mmdemo.samples import (
    CATEGORY_LABELS,
    MODES,
    RAIL_LABELS,
    SAMPLES,
    SAMPLES_BY_ID,
    UNPROTECTED_CONFIG,
    sample_data_url,
)
from mmdemo.settings import Settings

NemoCall = Callable[[Settings, str, str, str], GuardResult]

STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
_UPLOAD_RE = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)$")
# No inline script or style, nothing loaded from other origins.
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class RequestError(Exception):
    """A client error that maps to an HTTP status and a short message."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class RateLimiter:
    """Global sliding-window limiter: the demo's model is shared, so cap total requests."""

    per_minute: int
    _stamps: deque[float] = field(default_factory=deque)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._stamps and now - self._stamps[0] > 60:
                self._stamps.popleft()
            if len(self._stamps) >= self.per_minute:
                return False
            self._stamps.append(now)
            return True


@dataclass(frozen=True)
class RunRequest:
    prompt: str
    mode: str
    image_url: str


def parse_run_request(settings: Settings, raw: bytes) -> RunRequest:
    """Validate the JSON body of ``POST /api/run`` and resolve the image to a data URL."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RequestError(400, "body must be JSON") from exc
    if not isinstance(data, dict):
        raise RequestError(400, "body must be a JSON object")

    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise RequestError(400, "prompt is required")
    if len(prompt) > settings.max_prompt_chars:
        raise RequestError(400, f"prompt is limited to {settings.max_prompt_chars} characters")
    mode = data.get("mode")
    if not isinstance(mode, str) or mode not in MODES:
        raise RequestError(400, f"mode must be one of {sorted(MODES)}")

    sample_id, upload = data.get("sample"), data.get("image")
    if isinstance(sample_id, str):
        sample = SAMPLES_BY_ID.get(sample_id)
        if sample is None:
            raise RequestError(400, "unknown sample")
        try:
            image_url = sample_data_url(settings.samples_dir, sample)
        except OSError as exc:
            raise RequestError(500, "sample image is missing on the server") from exc
    elif isinstance(upload, str):
        if not settings.allow_upload:
            raise RequestError(403, "uploads are disabled on this deployment")
        image_url = _validate_upload(settings, upload)
    else:
        raise RequestError(400, "provide either sample or image")
    return RunRequest(prompt.strip(), mode, image_url)


def _validate_upload(settings: Settings, data_url: str) -> str:
    match = _UPLOAD_RE.match(data_url)
    if match is None:
        raise RequestError(400, "image must be a base64 png, jpeg or webp data URL")
    try:
        size = len(base64.b64decode(match.group(2), validate=True))
    except (binascii.Error, ValueError) as exc:
        raise RequestError(400, "image is not valid base64") from exc
    if size > settings.max_upload_bytes:
        raise RequestError(413, f"image is limited to {settings.max_upload_bytes} bytes")
    return data_url


class App:
    """Shared state for the request handlers."""

    def __init__(self, settings: Settings, nemo: NemoCall = call_nemo) -> None:
        self.settings = settings
        self.nemo = nemo
        self.limiter = RateLimiter(settings.rate_limit_per_min)

    def public_config(self) -> dict[str, Any]:
        s = self.settings
        return {
            "model": s.model_name,
            "allow_upload": s.allow_upload,
            "max_prompt_chars": s.max_prompt_chars,
            "max_upload_bytes": s.max_upload_bytes,
            "categories": CATEGORY_LABELS,
            "rails": RAIL_LABELS,
        }

    def run(self, request: RunRequest) -> dict[str, Any]:
        protected_config = MODES[request.mode]
        with ThreadPoolExecutor(max_workers=2) as pool:
            unprotected = pool.submit(
                self.nemo, self.settings, UNPROTECTED_CONFIG, request.prompt, request.image_url
            )
            protected = (
                pool.submit(
                    self.nemo, self.settings, protected_config, request.prompt, request.image_url
                )
                if protected_config
                else None
            )
            return {
                "unprotected": unprotected.result().to_json(),
                "protected": protected.result().to_json() if protected else None,
            }


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "mmdemo"

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/healthz":
                self._json(200, {"status": "ok"})
            elif path == "/api/config":
                self._json(200, app.public_config())
            elif path == "/api/samples":
                self._json(200, [vars(s) for s in SAMPLES])
            elif path == "/":
                self._static("index.html")
            elif path.startswith("/static/"):
                self._static(path.removeprefix("/static/"))
            elif path.startswith("/samples/"):
                self._sample(path.removeprefix("/samples/"))
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != "/api/run":
                self._json(404, {"error": "not found"})
                return
            try:
                limit = app.settings.max_upload_bytes * 4 // 3 + 4096
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    raise RequestError(400, "empty body")
                if length > limit:
                    raise RequestError(413, "request body too large")
                request = parse_run_request(app.settings, self.rfile.read(length))
                if not app.limiter.allow():
                    self._json(
                        429,
                        {"error": "too many requests, try again shortly"},
                        {"Retry-After": "10"},
                    )
                    return
                self._json(200, app.run(request))
            except RequestError as exc:
                self._json(exc.status, {"error": exc.message})
            except ValueError:
                self._json(400, {"error": "invalid Content-Length"})

        def _static(self, name: str) -> None:
            content_type = STATIC_FILES.get(name)
            if content_type is None:
                self._json(404, {"error": "not found"})
                return
            # Never cache the page assets: a redeploy must take effect on the next load.
            self._send(
                200, (app.settings.static_dir / name).read_bytes(), content_type, cache=False
            )

        def _sample(self, name: str) -> None:
            if not any(s.file == name for s in SAMPLES):
                self._json(404, {"error": "not found"})
                return
            self._send(200, (app.settings.samples_dir / name).read_bytes(), "image/png")

        def _json(self, status: int, body: Any, extra: dict[str, str] | None = None) -> None:
            self._send(status, json.dumps(body).encode(), "application/json", extra, cache=False)

        def _send(
            self,
            status: int,
            payload: bytes,
            content_type: str,
            extra: dict[str, str] | None = None,
            cache: bool = True,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "max-age=300" if cache else "no-store")
            self.send_header("Content-Security-Policy", _CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

    return Handler


# The server runs in a container behind a Service, so it must listen on every interface.
ALL_INTERFACES = "0.0.0.0"  # noqa: S104


def make_server(
    app: App, host: str = ALL_INTERFACES, port: int | None = None
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(
        (host, app.settings.port if port is None else port), make_handler(app)
    )


def main() -> None:
    settings = Settings.from_env()
    server = make_server(App(settings))
    print(f"serving on :{settings.port}, NeMo at {settings.nemo_url}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
