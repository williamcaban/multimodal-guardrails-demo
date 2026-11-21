"""Tests for the guardrail configuration renderer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from render_configs import AUDIO_CONFIGS, CONFIGS, build_config, main

BASE_URL = "https://model.example/v1"


def load(config_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    files = build_config(config_id, "qwen3-vl-8b", BASE_URL, "MODEL_API_KEY")
    return yaml.safe_load(files["config.yaml"]), yaml.safe_load(files["prompts.yml"])


@pytest.mark.parametrize("config_id", list(CONFIGS))
def test_every_config_has_the_files_the_operator_needs(config_id: str) -> None:
    files = build_config(config_id, "m", BASE_URL, "K")
    assert set(files) == {"config.yaml", "prompts.yml", "rails.co"}
    assert files["rails.co"].strip()  # the operator rejects a missing/empty rails.co key


@pytest.mark.parametrize("config_id", list(CONFIGS))
def test_flows_and_prompts_and_models_line_up(config_id: str) -> None:
    config, prompts = load(config_id)
    tasks = {p["task"] for p in prompts["prompts"]}
    models = {m["type"] for m in config["models"]}
    flows = config.get("rails", {}).get("input", {}).get("flows", [])
    assert "main" in models
    assert "general" in tasks  # keeps images attached for the main model
    for flow in flows:
        judge = flow.split("$model=")[1]
        assert judge in models, f"{flow}: no model named {judge}"
        assert f"content_safety_check_input $model={judge}" in tasks, f"{flow}: no prompt"
    assert len(flows) == len(CONFIGS[config_id])


def test_no_rails_config_has_no_rails() -> None:
    config, _ = load("no-rails")
    assert "rails" not in config
    assert [m["type"] for m in config["models"]] == ["main"]


def test_combined_config_runs_both_judges_in_order() -> None:
    config, _ = load("pii-and-injection")
    assert config["rails"]["input"]["flows"] == [
        "content safety check input $model=vision_pii",
        "content safety check input $model=vision_injection",
    ]


def test_model_endpoint_and_key_env_are_applied_everywhere() -> None:
    config, _ = load("pii-and-injection")
    for model in config["models"]:
        assert model["model"] == "qwen3-vl-8b"
        assert model["parameters"]["base_url"] == BASE_URL
        assert model["api_key_env_var"] == "MODEL_API_KEY"
        assert "api_key" not in model["parameters"]  # secrets come from the environment only


def test_judge_prompts_keep_the_image_attached() -> None:
    # The library only keeps the image when the user message is a bare `{{ user_input }}`.
    _, prompts = load("pii-and-injection")
    for prompt in prompts["prompts"]:
        if prompt["task"].startswith("content_safety_check_input"):
            user = [m for m in prompt["messages"] if m["type"] == "user"]
            assert [m["content"].strip() for m in user] == ["{{ user_input }}"]
        else:
            assert any("to_chat_messages" in str(m) for m in prompt["messages"])


def test_cli_writes_directories_and_configmaps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "cfgs"
    argv = [
        "--out",
        str(out),
        "--configmaps",
        "--namespace",
        "demo-ns",
        "--base-url",
        BASE_URL,
        "--model",
        "m",
    ]
    assert main(argv) == 0
    assert {p.name for p in out.iterdir()} == set(CONFIGS)
    docs = [d for d in yaml.safe_load_all(capsys.readouterr().out) if d]
    assert [d["metadata"]["name"] for d in docs] == [f"mmg-{c}" for c in CONFIGS]
    assert {d["metadata"]["namespace"] for d in docs} == {"demo-ns"}
    assert all(set(d["data"]) == {"config.yaml", "prompts.yml", "rails.co"} for d in docs)


AUDIO_URL = "http://omni.example:8001/v1"


def load_audio(config_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    files = build_config(config_id, "qwen3-omni", AUDIO_URL, "AUDIO_MODEL_API_KEY")
    return yaml.safe_load(files["config.yaml"]), yaml.safe_load(files["prompts.yml"])


@pytest.mark.parametrize("config_id", list(AUDIO_CONFIGS))
def test_audio_configs_line_up_and_use_the_audio_endpoint(config_id: str) -> None:
    config, prompts = load_audio(config_id)
    tasks = {p["task"] for p in prompts["prompts"]}
    flows = config.get("rails", {}).get("input", {}).get("flows", [])
    assert len(flows) == len(AUDIO_CONFIGS[config_id])
    for flow in flows:
        judge = flow.split("$model=")[1]
        assert judge in {m["type"] for m in config["models"]}
        assert f"content_safety_check_input $model={judge}" in tasks
    for model in config["models"]:
        assert model["model"] == "qwen3-omni"
        assert model["parameters"]["base_url"] == AUDIO_URL
        assert model["api_key_env_var"] == "AUDIO_MODEL_API_KEY"


def test_combined_audio_config_runs_both_audio_judges_in_order() -> None:
    config, _ = load_audio("pii-and-injection-audio")
    assert config["rails"]["input"]["flows"] == [
        "content safety check input $model=audio_pii",
        "content safety check input $model=audio_injection",
    ]


def test_audio_judge_prompts_keep_the_audio_attached() -> None:
    # Same rule as images: only a bare `{{ user_input }}` keeps non-text parts for the judge.
    _, prompts = load_audio("pii-and-injection-audio")
    judges = [p for p in prompts["prompts"] if p["task"].startswith("content_safety_check_input")]
    assert len(judges) == 2
    for prompt in judges:
        user = [m for m in prompt["messages"] if m["type"] == "user"]
        assert [m["content"].strip() for m in user] == ["{{ user_input }}"]


def test_cli_renders_audio_configs_only_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--configmaps", "--base-url", BASE_URL, "--model", "m"]
    assert main(argv) == 0
    names = [d["metadata"]["name"] for d in yaml.safe_load_all(capsys.readouterr().out) if d]
    assert names == [f"mmg-{c}" for c in CONFIGS]

    assert main([*argv, "--audio-base-url", AUDIO_URL]) == 0
    docs = [d for d in yaml.safe_load_all(capsys.readouterr().out) if d]
    assert [d["metadata"]["name"] for d in docs] == [f"mmg-{c}" for c in (*CONFIGS, *AUDIO_CONFIGS)]
    audio = yaml.safe_load(docs[-1]["data"]["config.yaml"])
    assert audio["models"][0]["model"] == "qwen3-omni"  # default audio model name
    assert audio["models"][0]["parameters"]["base_url"] == AUDIO_URL
