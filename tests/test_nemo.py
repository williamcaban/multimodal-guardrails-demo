"""Unit tests for judge-verdict parsing, response classification and request shaping."""

from __future__ import annotations

import copy
import io
import json
import socket
import ssl
import urllib.error
from email.message import Message
from typing import Any

import pytest
from helpers import ALLOWED_BODY, BLOCKED_BODY, base_settings
from mmdemo.nemo import (
    Verdict,
    build_request,
    describe_failure,
    interpret_response,
    parse_verdict,
)

JUDGE = "content_safety_check_input $model=vision_pii"


@pytest.mark.parametrize(
    ("completion", "expected"),
    [
        ("unsafe, P1, P2, P3, P4", Verdict("vision_pii", "unsafe", ("P1", "P2", "P3", "P4"))),
        ("unsafe,P1,P3", Verdict("vision_pii", "unsafe", ("P1", "P3"))),
        ("unsafe P1,P2,P5", Verdict("vision_pii", "unsafe", ("P1", "P2", "P5"))),
        ("unsafe\nP7", Verdict("vision_pii", "unsafe", ("P7",))),
        ("safe", Verdict("vision_pii", "safe", ())),
        # Models sometimes append commentary after the verdict; categories only count when unsafe.
        (
            "safe\nThe image shows an invoice with P1 style fields.",
            Verdict("vision_pii", "safe", ()),
        ),
        ("  UNSAFE I1", Verdict("vision_pii", "unsafe", ("I1",))),
    ],
)
def test_parse_verdict(completion: str, expected: Verdict) -> None:
    assert parse_verdict(JUDGE, completion) == expected


def test_parse_verdict_ignores_non_judge_tasks_and_garbage() -> None:
    assert parse_verdict("general", "safe") is None
    assert parse_verdict(JUDGE, "I cannot tell") is None
    assert parse_verdict(JUDGE, "") is None


def test_parse_verdict_names_the_judge_from_the_task() -> None:
    verdict = parse_verdict("content_safety_check_input $model=vision_injection", "unsafe I3")
    assert verdict is not None
    assert verdict.judge == "vision_injection"


def test_blocked_response() -> None:
    result = interpret_response("pii-vision", BLOCKED_BODY, 1234)
    assert result.status == "blocked"
    assert result.latency_ms == 1234
    assert result.verdicts == [Verdict("vision_pii", "unsafe", ("P1", "P3"))]
    assert result.error is None


def test_allowed_response_keeps_answer_and_safe_verdict() -> None:
    result = interpret_response("pii-vision", ALLOWED_BODY, 10)
    assert result.status == "allowed"
    assert result.answer == "A bar chart of sales."
    # The `general` task is not a judge, so only the safety verdict is reported.
    assert result.verdicts == [Verdict("vision_pii", "safe", ())]


def test_response_without_log_falls_back_to_refusal_text() -> None:
    body: dict[str, Any] = {
        "choices": [{"message": {"content": "I'm sorry, I can't respond to that."}}]
    }
    assert interpret_response("x", body, 1).status == "blocked"
    body = {"choices": [{"message": {"content": "Hello"}}]}
    assert interpret_response("x", body, 1).status == "allowed"


@pytest.mark.parametrize(
    "answer",
    [
        "Internal server error",
        "Could not load the ['pii-vision'] guardrails configuration. An internal error occurred.",
    ],
)
def test_server_side_failures_are_errors_not_blocks(answer: str) -> None:
    body = copy.deepcopy(ALLOWED_BODY)
    body["choices"][0]["message"]["content"] = answer
    result = interpret_response("x", body, 5)
    assert result.status == "error"
    assert result.answer is None
    assert result.error is not None
    assert result.error.startswith(answer[:20])


def test_empty_choices_is_an_error() -> None:
    assert interpret_response("x", {"choices": []}, 1).status == "error"


def test_build_request_shape() -> None:
    settings = base_settings("http://nemo", model_name="qwen3-vl-8b", max_tokens=111)
    body = build_request(settings, "pii-vision", "describe", "data:image/png;base64,AAAA")
    assert body["model"] == "qwen3-vl-8b"
    assert body["max_tokens"] == 111
    parts = body["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "describe"}
    assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
    # config_id must be nested under `guardrails`; a top-level config_id is silently ignored.
    assert body["guardrails"]["config_id"] == "pii-vision"
    assert "config_id" not in body
    assert body["guardrails"]["options"]["log"] == {"activated_rails": True, "llm_calls": True}


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (
            urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
            "Cannot find the guardrail service (nemo.example)",
        ),
        (urllib.error.URLError(ConnectionRefusedError()), "refused the connection"),
        (TimeoutError(), "did not answer within 10s"),
        (urllib.error.URLError(ssl.SSLError("bad certificate")), "TLS error"),
        (json.JSONDecodeError("x", "", 0), "not JSON"),
        (
            urllib.error.HTTPError("u", 401, "Unauthorized", Message(), io.BytesIO(b"")),
            "rejected the UI's credentials (HTTP 401)",
        ),
        (
            urllib.error.HTTPError("u", 500, "Boom", Message(), io.BytesIO(b"upstream down")),
            "HTTP 500: upstream down",
        ),
    ],
)
def test_describe_failure_says_what_to_check(exc: Exception, expected: str) -> None:
    settings = base_settings("https://nemo.example", request_timeout_s=10)
    assert expected in describe_failure(exc, settings)


def test_missing_service_message_points_at_the_deploy_step() -> None:
    exc = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
    message = describe_failure(exc, base_settings("https://nemo.example"))
    assert "NemoGuardrails" in message
    assert "Errno" not in message  # no raw urllib text for an operator-facing error
