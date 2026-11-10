"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]  # .../ui
_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Everything the UI needs to reach NeMo Guardrails and to protect itself."""

    nemo_url: str
    model_name: str = "qwen3-vl-8b"
    nemo_ca_file: str | None = None
    token_file: str | None = None
    port: int = 8080
    max_tokens: int = 300
    max_prompt_chars: int = 300
    max_upload_bytes: int = 1_500_000
    rate_limit_per_min: int = 30
    allow_upload: bool = False
    request_timeout_s: float = 180.0
    samples_dir: Path = _PACKAGE_ROOT.parent / "samples"
    static_dir: Path = _PACKAGE_ROOT / "static"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        nemo_url = e.get("NEMO_URL", "").rstrip("/")
        if not nemo_url.startswith(("http://", "https://")):
            raise ValueError("NEMO_URL must be set to an http(s) URL, e.g. https://nemo.ns.svc")
        ca = e.get("NEMO_CA_FILE") or _existing(_SA_DIR / "service-ca.crt")
        token = e.get("NEMO_TOKEN_FILE") or _existing(_SA_DIR / "token")
        defaults = cls(nemo_url=nemo_url)
        return cls(
            nemo_url=nemo_url,
            model_name=e.get("MODEL_NAME", defaults.model_name),
            nemo_ca_file=ca,
            token_file=token,
            port=int(e.get("PORT", defaults.port)),
            max_tokens=int(e.get("MAX_TOKENS", defaults.max_tokens)),
            max_prompt_chars=int(e.get("MAX_PROMPT_CHARS", defaults.max_prompt_chars)),
            max_upload_bytes=int(e.get("MAX_UPLOAD_BYTES", defaults.max_upload_bytes)),
            rate_limit_per_min=int(e.get("RATE_LIMIT_PER_MIN", defaults.rate_limit_per_min)),
            allow_upload=e.get("ALLOW_UPLOAD", "false").lower() in _TRUE,
            request_timeout_s=float(e.get("REQUEST_TIMEOUT_S", defaults.request_timeout_s)),
            samples_dir=Path(e.get("SAMPLES_DIR", str(defaults.samples_dir))),
            static_dir=Path(e.get("STATIC_DIR", str(defaults.static_dir))),
        )


def _existing(path: Path) -> str | None:
    return str(path) if path.is_file() else None
