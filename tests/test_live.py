"""Live integration test against a real NeMo Guardrails server and vision model.

Skipped unless NEMO_URL is set, for example against the local server used during development:

    NEMO_URL=http://localhost:8765 MODEL_NAME=gemma3:latest pytest -m live

or against the RHOAI service through a port-forward (set NEMO_TOKEN_FILE for the bearer token).
"""

from __future__ import annotations

import os

import pytest
from helpers import base_settings
from mmdemo.nemo import call_nemo
from mmdemo.samples import SAMPLES_BY_ID, sample_data_url
from mmdemo.settings import Settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("NEMO_URL"), reason="NEMO_URL not set"),
]

PROMPT = "Process this document and respond."


def settings() -> Settings:
    return base_settings(
        os.environ["NEMO_URL"].rstrip("/"),
        model_name=os.environ.get("MODEL_NAME", "qwen3-vl-8b"),
        token_file=os.environ.get("NEMO_TOKEN_FILE"),
        nemo_ca_file=os.environ.get("NEMO_CA_FILE"),
        max_tokens=200,
        request_timeout_s=300,
    )


def image(sample_id: str) -> str:
    s = base_settings("http://unused")
    return sample_data_url(s.samples_dir, SAMPLES_BY_ID[sample_id])


@pytest.mark.parametrize(
    ("config_id", "sample_id"),
    [
        ("pii-vision", "invoice_pii"),
        ("pii-vision", "healthcare_form_pii"),
        ("injection-vision", "inj_exfil"),
    ],
)
def test_clear_violations_are_blocked(config_id: str, sample_id: str) -> None:
    result = call_nemo(settings(), config_id, PROMPT, image(sample_id))
    assert result.status == "blocked", result
    assert any(v.verdict == "unsafe" for v in result.verdicts), result


@pytest.mark.parametrize("config_id", ["pii-vision", "injection-vision", "pii-and-injection"])
def test_clean_chart_is_allowed_and_answered(config_id: str) -> None:
    result = call_nemo(settings(), config_id, PROMPT, image("chart_clean"))
    assert result.status == "allowed", result
    assert result.answer
    assert all(v.verdict == "safe" for v in result.verdicts), result


def test_passthrough_runs_no_rails() -> None:
    result = call_nemo(settings(), "passthrough", PROMPT, image("invoice_pii"))
    assert result.status == "allowed", result
    assert result.verdicts == []
