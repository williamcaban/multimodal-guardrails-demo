"""Offline checks for the audio guardrail demo: samples, request shape and rail configs."""

from __future__ import annotations

import base64
import wave

import pytest
from nemoguardrails import RailsConfig

from audio_demo import CASES, HERE, SAMPLES, message_for
from make_audio_samples import CLIPS


@pytest.mark.parametrize("name", sorted(CLIPS))
def test_sample_is_16khz_mono_pcm_wav(name: str) -> None:
    with wave.open(str(SAMPLES / name), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.getnframes() > 16000  # longer than one second


def test_every_expected_clip_exists_and_every_clip_is_used() -> None:
    used = {name for case in CASES.values() for name in case.expected}
    assert used <= set(CLIPS), used - set(CLIPS)
    assert set(CLIPS) <= used, set(CLIPS) - used
    assert {p.name for p in SAMPLES.glob("*.wav")} == set(CLIPS)


def test_expected_verdicts_match_clip_names() -> None:
    for case in CASES.values():
        for name, verdict in case.expected.items():
            blocked = name.startswith(("pii_", "inj_"))
            assert verdict == ("BLOCK" if blocked else "ALLOW"), name


def test_message_carries_text_and_input_audio_part() -> None:
    path = SAMPLES / "ctrl_weather.wav"
    (msg,) = message_for(path, "Summarize this.")
    text_part, audio_part = msg["content"]
    assert msg["role"] == "user"
    assert text_part == {"type": "text", "text": "Summarize this."}
    assert audio_part["type"] == "input_audio"
    assert audio_part["input_audio"]["format"] == "wav"
    assert base64.b64decode(audio_part["input_audio"]["data"]) == path.read_bytes()


@pytest.mark.parametrize("config_name", sorted(CASES))
def test_rail_config_is_wired_for_audio(config_name: str) -> None:
    config = RailsConfig.from_path(str(HERE / "config" / config_name))
    assert {m.type for m in config.models} == {"main", "audio_rails"}
    assert "content safety check input $model=audio_rails" in config.rails.input.flows
    judge = next(
        p for p in config.prompts if p.task == "content_safety_check_input $model=audio_rails"
    )
    # The user turn must be the bare variable or NeMo flattens the audio part away.
    assert [m.content for m in judge.messages if m.type == "user"] == ["{{ user_input }}"]
    assert judge.output_parser == "is_content_safe"
    assert judge.max_tokens is not None and judge.max_tokens >= 1000
