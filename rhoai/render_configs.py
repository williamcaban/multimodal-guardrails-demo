"""Render the NeMo Guardrails configurations used on RHOAI (and for local server testing).

The judge prompts are read from ``config/<policy>_vision/prompts.yml`` so the policy text has a
single source of truth shared with the local demo. Each policy gets its own judge model name
(``vision_pii``, ``vision_injection``) so both rails can run in one configuration.

Usage:
    python rhoai/render_configs.py --out /tmp/cfgs --model gemma3 \\
        --base-url http://localhost:11434/v1
    python rhoai/render_configs.py --configmaps --namespace mm-demo \\
        --base-url ... --model ... > cm.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
JUDGE_TASK = "content_safety_check_input"

# policy -> (source prompts file, judge model name used in `$model=`)
POLICIES: dict[str, tuple[Path, str]] = {
    "pii": (ROOT / "config" / "pii_vision" / "prompts.yml", "vision_pii"),
    "injection": (ROOT / "config" / "injection_vision" / "prompts.yml", "vision_injection"),
}

# config id (also the ConfigMap suffix and the `guardrails.config_id` clients send) -> policies
CONFIGS: dict[str, list[str]] = {
    "no-rails": [],
    "pii-vision": ["pii"],
    "injection-vision": ["injection"],
    "pii-and-injection": ["pii", "injection"],
}


class _LiteralDumper(yaml.SafeDumper):
    """Dump multi-line strings as ``|`` blocks so the prompts stay readable."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_LiteralDumper.add_representer(str, _str_representer)


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_LiteralDumper, sort_keys=False, width=100)


def _load_prompts(path: Path) -> list[dict[str, Any]]:
    loaded = yaml.safe_load(path.read_text())
    prompts = loaded["prompts"]
    assert isinstance(prompts, list)
    return [p for p in prompts if isinstance(p, dict)]


def model_entry(model_type: str, model: str, base_url: str, api_key_env: str) -> dict[str, Any]:
    return {
        "type": model_type,
        "engine": "openai",
        "model": model,
        "api_key_env_var": api_key_env,
        "parameters": {"base_url": base_url},
    }


def build_config(config_id: str, model: str, base_url: str, api_key_env: str) -> dict[str, str]:
    """Return the files (name -> content) of one configuration directory."""
    policies = CONFIGS[config_id]
    models = [model_entry("main", model, base_url, api_key_env)]
    flows: list[str] = []
    prompts: list[dict[str, Any]] = []
    general: dict[str, Any] | None = None

    for policy in policies:
        source, judge = POLICIES[policy]
        models.append(model_entry(judge, model, base_url, api_key_env))
        flows.append(f"content safety check input $model={judge}")
        for prompt in _load_prompts(source):
            if str(prompt["task"]).startswith(JUDGE_TASK):
                prompts.append({**prompt, "task": f"{JUDGE_TASK} $model={judge}"})
            elif prompt["task"] == "general":
                general = prompt
    if general is None:  # no-rails: reuse the image-preserving `general` prompt
        general = next(p for p in _load_prompts(POLICIES["pii"][0]) if p["task"] == "general")
    prompts.append(general)

    config: dict[str, Any] = {"models": models}
    if flows:
        config["rails"] = {"input": {"flows": flows}, "dialog": {"single_call": {"enabled": False}}}
    return {
        "config.yaml": dump_yaml(config),
        "prompts.yml": dump_yaml({"prompts": prompts}),
        # The RHOAI operator requires a rails.co key even when it is empty.
        "rails.co": "# built-in rails only\n",
    }


def configmap_manifest(name: str, namespace: str, files: dict[str, str]) -> str:
    return dump_yaml(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {"app.kubernetes.io/part-of": "mm-guardrails-demo"},
            },
            "data": files,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render NeMo Guardrails configs for RHOAI.")
    parser.add_argument("--base-url", required=True, help="OpenAI-compatible URL ending in /v1")
    parser.add_argument("--model", required=True, help="served model name")
    parser.add_argument("--api-key-env", default="MODEL_API_KEY")
    parser.add_argument("--out", type=Path, help="write one directory per config under this path")
    parser.add_argument("--configmaps", action="store_true", help="print ConfigMap manifests")
    parser.add_argument("--namespace", default="mm-guardrails-demo")
    args = parser.parse_args(argv)

    if not args.out and not args.configmaps:
        parser.error("choose --out DIR and/or --configmaps")

    docs: list[str] = []
    for config_id in CONFIGS:
        files = build_config(config_id, args.model, args.base_url, args.api_key_env)
        if args.out:
            target = args.out / config_id
            target.mkdir(parents=True, exist_ok=True)
            for filename, content in files.items():
                (target / filename).write_text(content)
        if args.configmaps:
            docs.append(configmap_manifest(f"mmg-{config_id}", args.namespace, files))
    if args.configmaps:
        sys.stdout.write("---\n".join(docs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
