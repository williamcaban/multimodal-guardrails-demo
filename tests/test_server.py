"""Integration tests: the real UI HTTP server talking to a stub NeMo Guardrails server."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

from helpers import ROOT, StubNemo, base_settings, http
from mmdemo.samples import SAMPLES
from mmdemo.settings import Settings

StartUi = Callable[[Settings], str]
PROMPT = "Process this document and respond."


def run(base: str, **body: Any) -> tuple[int, dict[str, Any]]:
    status, _, raw = http("POST", f"{base}/api/run", {"prompt": PROMPT, **body})
    return status, json.loads(raw)


def test_pages_and_catalog(stub_nemo: StubNemo, start_ui: StartUi) -> None:
    base = start_ui(base_settings(stub_nemo.url))

    status, headers, body = http("GET", f"{base}/")
    assert status == 200
    assert b"Multimodal guardrails demo" in body
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"

    status, _, raw = http("GET", f"{base}/api/samples")
    assert status == 200
    assert [s["id"] for s in json.loads(raw)] == [s.id for s in SAMPLES]

    status, _, raw = http("GET", f"{base}/api/config")
    config = json.loads(raw)
    assert status == 200
    assert config["allow_upload"] is False
    assert config["categories"]["P1"]

    assert http("GET", f"{base}/healthz")[0] == 200


def test_every_catalog_sample_has_an_image() -> None:
    for sample in SAMPLES:
        assert (ROOT / "samples" / sample.file).is_file(), sample.file


def test_sample_images_are_served_and_paths_are_confined(
    stub_nemo: StubNemo, start_ui: StartUi
) -> None:
    base = start_ui(base_settings(stub_nemo.url))
    status, headers, body = http("GET", f"{base}/samples/invoice_pii.png")
    assert (status, headers["Content-Type"]) == (200, "image/png")
    assert body.startswith(b"\x89PNG")
    for bad in (
        "/samples/../pyproject.toml",
        "/samples/%2e%2e/pyproject.toml",
        "/static/../../pyproject.toml",
    ):
        assert http("GET", f"{base}{bad}")[0] == 404, bad
    assert http("GET", f"{base}/samples/not_in_catalog.png")[0] == 404


def test_protected_vs_unprotected(stub_nemo: StubNemo, start_ui: StartUi) -> None:
    base = start_ui(base_settings(stub_nemo.url, model_name="qwen3-vl-8b"))
    status, data = run(base, sample="invoice_pii", mode="pii")
    assert status == 200
    assert data["unprotected"]["status"] == "allowed"
    assert data["protected"]["status"] == "blocked"
    assert data["protected"]["verdicts"][0]["categories"] == ["P1", "P3"]

    by_config = {r["body"]["guardrails"]["config_id"]: r["body"] for r in stub_nemo.requests}
    assert set(by_config) == {"no-rails", "pii-vision"}
    sent = by_config["pii-vision"]
    assert sent["model"] == "qwen3-vl-8b"
    image = sent["messages"][0]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/png;base64,")
    assert all(r["path"] == "/v1/chat/completions" for r in stub_nemo.requests)


def test_each_mode_maps_to_its_config(stub_nemo: StubNemo, start_ui: StartUi) -> None:
    base = start_ui(base_settings(stub_nemo.url))
    expected = {"injection": "injection-vision", "both": "pii-and-injection"}
    for mode, config_id in expected.items():
        stub_nemo.requests.clear()
        assert run(base, sample="chart_clean", mode=mode)[0] == 200
        assert {r["body"]["guardrails"]["config_id"] for r in stub_nemo.requests} == {
            "no-rails",
            config_id,
        }


def test_mode_none_only_calls_the_unprotected_config(
    stub_nemo: StubNemo, start_ui: StartUi
) -> None:
    base = start_ui(base_settings(stub_nemo.url))
    status, data = run(base, sample="chart_clean", mode="none")
    assert status == 200
    assert data["protected"] is None
    assert [r["body"]["guardrails"]["config_id"] for r in stub_nemo.requests] == ["no-rails"]


def test_token_file_becomes_a_bearer_header(
    stub_nemo: StubNemo, start_ui: StartUi, tmp_path: Any
) -> None:
    token = tmp_path / "token"
    token.write_text("abc123\n")
    base = start_ui(base_settings(stub_nemo.url, token_file=str(token)))
    run(base, sample="chart_clean", mode="pii")
    assert {r["auth"] for r in stub_nemo.requests} == {"Bearer abc123"}


def test_rejects_bad_requests(stub_nemo: StubNemo, start_ui: StartUi) -> None:
    base = start_ui(base_settings(stub_nemo.url, max_prompt_chars=20))
    cases: list[tuple[dict[str, Any], int]] = [
        ({"sample": "chart_clean", "mode": "bogus"}, 400),
        ({"sample": "nope", "mode": "pii"}, 400),
        ({"mode": "pii"}, 400),
        ({"sample": "chart_clean", "mode": "pii", "prompt": "x" * 21}, 400),
        ({"sample": "chart_clean", "mode": "pii", "prompt": "   "}, 400),
        ({"sample": "../../etc/passwd", "mode": "pii"}, 400),
    ]
    for body, expected in cases:
        body.setdefault("prompt", "ok")
        status, _, _ = http("POST", f"{base}/api/run", body)
        assert status == expected, body
    assert http("POST", f"{base}/api/run", None)[0] == 400
    assert http("POST", f"{base}/nope", {})[0] == 404
    assert stub_nemo.requests == []  # nothing reached the guardrail service


def test_uploads_are_off_by_default(stub_nemo: StubNemo, start_ui: StartUi) -> None:
    base = start_ui(base_settings(stub_nemo.url))
    tiny = "data:image/png;base64," + base64.b64encode(b"\x89PNG....").decode()
    assert run(base, image=tiny, mode="pii")[0] == 403
    assert stub_nemo.requests == []


def test_upload_validation_when_enabled(stub_nemo: StubNemo, start_ui: StartUi) -> None:
    base = start_ui(base_settings(stub_nemo.url, allow_upload=True, max_upload_bytes=64))
    good = "data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"x" * 10).decode()
    assert run(base, image=good, mode="pii")[0] == 200
    too_big = "data:image/png;base64," + base64.b64encode(b"x" * 200).decode()
    assert run(base, image=too_big, mode="pii")[0] == 413
    for bad in (
        "data:text/html;base64,PGI+",
        "data:image/png;base64,@@@@",
        "http://evil.example/x.png",
    ):
        assert run(base, image=bad, mode="pii")[0] == 400, bad


def test_rate_limit(stub_nemo: StubNemo, start_ui: StartUi) -> None:
    base = start_ui(base_settings(stub_nemo.url, rate_limit_per_min=2))
    statuses = [run(base, sample="chart_clean", mode="none")[0] for _ in range(3)]
    assert statuses == [200, 200, 429]


def test_unreachable_guardrail_service_is_reported_not_crashed(start_ui: StartUi) -> None:
    # Port 9 (discard) is closed locally, so the connection is refused immediately.
    base = start_ui(base_settings("http://127.0.0.1:9", request_timeout_s=2))
    status, data = run(base, sample="chart_clean", mode="pii")
    assert status == 200
    assert data["unprotected"]["status"] == "error"
    assert data["protected"]["status"] == "error"
    assert data["protected"]["error"]
