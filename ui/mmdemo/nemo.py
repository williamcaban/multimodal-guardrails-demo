"""Client for the NeMo Guardrails ``/v1/chat/completions`` endpoint.

The server is asked for ``guardrails.options.log`` so each response says which rails ran, whether
one stopped the request, and what the vision judge answered.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from mmdemo.settings import Settings

Status = Literal["allowed", "blocked", "error"]

# Canned strings the server returns instead of a real model answer.
_REFUSALS = {"I'm sorry, I can't respond to that."}
_ERROR_PREFIXES = ("Could not load the", "Internal server error")
_JUDGE_TASK = "content_safety_check_input"
_VERDICT_RE = re.compile(r"^\s*(safe|unsafe)\b(.*)$", re.IGNORECASE | re.DOTALL)
_CATEGORY_RE = re.compile(r"\b([A-Z]\d{1,2})\b")


@dataclass(frozen=True)
class Verdict:
    """One vision-judge decision, for example ``unsafe`` with categories ``P1, P3``."""

    judge: str
    verdict: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class GuardResult:
    config_id: str
    status: Status
    answer: str | None
    latency_ms: int
    verdicts: list[Verdict] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def parse_verdict(task: str, completion: str) -> Verdict | None:
    """Parse a judge completion such as ``unsafe, P1, P3`` or ``safe``; None if not a verdict."""
    if not task.startswith(_JUDGE_TASK):
        return None
    match = _VERDICT_RE.match(completion)
    if match is None:
        return None
    verdict = match.group(1).lower()
    judge = task.partition("$model=")[2] or "judge"
    categories = tuple(_CATEGORY_RE.findall(match.group(2))) if verdict == "unsafe" else ()
    return Verdict(judge=judge, verdict=verdict, categories=categories)


def _answer(body: dict[str, Any]) -> str | None:
    choices = body.get("choices")
    if isinstance(choices, list) and choices:
        content = (choices[0].get("message") or {}).get("content")
        return content if isinstance(content, str) else None
    return None


def interpret_response(config_id: str, body: dict[str, Any], latency_ms: int) -> GuardResult:
    """Classify a NeMo response as allowed, blocked, or error."""
    answer = _answer(body)
    log = (body.get("guardrails") or {}).get("log") or {}
    rails: list[dict[str, Any]] = log.get("activated_rails") or []
    calls: list[dict[str, Any]] = log.get("llm_calls") or []
    verdicts = [
        v
        for call in calls
        if (v := parse_verdict(str(call.get("task", "")), str(call.get("completion", ""))))
        is not None
    ]
    if answer is None or answer.startswith(_ERROR_PREFIXES):
        message = answer or "empty response"
        return GuardResult(config_id, "error", None, latency_ms, verdicts, error=message[:300])
    blocked = any(bool(rail.get("stop")) for rail in rails) or (not rails and answer in _REFUSALS)
    return GuardResult(config_id, "blocked" if blocked else "allowed", answer, latency_ms, verdicts)


def build_request(
    settings: Settings, config_id: str, prompt: str, image_url: str
) -> dict[str, Any]:
    return {
        "model": settings.model_name,
        "max_tokens": settings.max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            }
        ],
        "guardrails": {
            "config_id": config_id,
            "options": {"log": {"activated_rails": True, "llm_calls": True}},
        },
    }


def _read_token(token_file: str | None) -> str | None:
    if token_file is None:
        return None
    try:
        # Projected service-account tokens rotate, so read the file on every request.
        return Path(token_file).read_text().strip() or None
    except OSError:
        return None


def call_nemo(settings: Settings, config_id: str, prompt: str, image_url: str) -> GuardResult:
    """Send one image request through a guardrail configuration."""
    body = json.dumps(build_request(settings, config_id, prompt, image_url)).encode()
    headers = {"Content-Type": "application/json"}
    token = _read_token(settings.token_file)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{settings.nemo_url}/v1/chat/completions", data=body, headers=headers, method="POST"
    )
    context = (
        ssl.create_default_context(cafile=settings.nemo_ca_file)
        if settings.nemo_url.startswith("https")
        else None
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(
            request, timeout=settings.request_timeout_s, context=context
        ) as r:
            payload = json.load(r)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return _error(config_id, started, describe_failure(exc, settings))
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not isinstance(payload, dict):
        return _error(config_id, started, "unexpected response shape")
    return interpret_response(config_id, payload, elapsed_ms)


def describe_failure(exc: Exception, settings: Settings) -> str:
    """Turn a transport failure into a message that says what to check."""
    host = urllib.parse.urlparse(settings.nemo_url).hostname or settings.nemo_url
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return (
                f"The guardrail service rejected the UI's credentials (HTTP {exc.code}). "
                "Check that the mm-ui ServiceAccount has the view role in this namespace."
            )
        detail = exc.read().decode(errors="replace")[:200]
        return f"The guardrail service returned HTTP {exc.code}: {detail}"
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        return (
            f"Cannot find the guardrail service ({host}). "
            "Is the NemoGuardrails resource deployed? On the cluster it is created once the "
            "model API key secret exists (re-run rhoai/deploy.sh after adding the key)."
        )
    if isinstance(reason, ConnectionRefusedError):
        return f"The guardrail service at {host} refused the connection; it may still be starting."
    if isinstance(reason, TimeoutError):
        wait = f"{settings.request_timeout_s:.0f}s"
        return f"The guardrail service at {host} did not answer within {wait}."
    if isinstance(reason, ssl.SSLError):
        return f"TLS error talking to the guardrail service at {host}: {reason}"[:300]
    if isinstance(exc, json.JSONDecodeError):
        return "The guardrail service returned a response that is not JSON."
    return f"Could not reach the guardrail service ({type(reason).__name__}: {reason})"[:300]


def _error(config_id: str, started: float, message: str) -> GuardResult:
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return GuardResult(config_id, "error", None, elapsed_ms, error=message)
