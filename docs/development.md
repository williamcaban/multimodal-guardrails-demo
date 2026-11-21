# Development

Back to the [README](../README.md).

Python 3.12, `uv`, typed, ruff/black/mypy/vulture, pytest:

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install "nemoguardrails[server]" pillow httpx pyyaml \
    ruff black mypy vulture pytest pre-commit types-PyYAML
scripts/check.sh                                   # the full gate, run before every commit
NEMO_URL=http://localhost:8765 MODEL_NAME=gemma3:latest pytest -m live   # against a real server
python rhoai/render_configs.py --out /tmp/cfgs --model gemma3:latest \
    --base-url http://localhost:11434/v1 && nemoguardrails server --config /tmp/cfgs --port 8765
```
