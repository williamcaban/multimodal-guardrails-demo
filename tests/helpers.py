"""Shared test data and helpers."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from mmdemo.settings import Settings

ROOT = Path(__file__).resolve().parent.parent

# A block as the real server reports it (shape captured from nemoguardrails 0.24.1).
BLOCKED_BODY: dict[str, Any] = {
    "choices": [
        {"message": {"role": "assistant", "content": "I'm sorry, I can't respond to that."}}
    ],
    "guardrails": {
        "config_id": "pii-vision",
        "log": {
            "activated_rails": [
                {
                    "type": "input",
                    "name": "content safety check input $model=vision_pii",
                    "stop": True,
                }
            ],
            "llm_calls": [
                {
                    "task": "content_safety_check_input $model=vision_pii",
                    "completion": "unsafe, P1, P3",
                }
            ],
        },
    },
}
ALLOWED_BODY: dict[str, Any] = {
    "choices": [{"message": {"role": "assistant", "content": "A bar chart of sales."}}],
    "guardrails": {
        "config_id": "x",
        "log": {
            "activated_rails": [
                {
                    "type": "input",
                    "name": "content safety check input $model=vision_pii",
                    "stop": False,
                },
                {"type": "generation", "name": "generate user intent", "stop": False},
            ],
            "llm_calls": [
                {"task": "content_safety_check_input $model=vision_pii", "completion": "safe"},
                {"task": "general", "completion": "A bar chart of sales."},
            ],
        },
    },
}


@dataclass
class StubNemo:
    """Records requests; ``no-rails`` gets an answer, every other config gets a block."""

    url: str
    requests: list[dict[str, Any]] = field(default_factory=list)


def base_settings(nemo_url: str, **overrides: Any) -> Settings:
    settings = Settings(
        nemo_url=nemo_url,
        samples_dir=ROOT / "samples",
        static_dir=ROOT / "ui" / "static",
        request_timeout_s=10,
    )
    return replace(settings, **overrides)


def http(method: str, url: str, body: Any = None) -> tuple[int, dict[str, str], bytes]:
    """Tiny HTTP helper that returns (status, headers, body) without raising on 4xx/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
