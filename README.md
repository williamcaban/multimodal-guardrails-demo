# Multimodal guardrails with NeMo Guardrails: images and audio

Guardrails that read the picture or listen to the recording themselves, with no OCR or
speech-to-text step in between. A multimodal model acts as an LLM-as-a-judge in
[NVIDIA NeMo Guardrails](https://docs.nvidia.com/nemo/guardrails/get-started/tutorials/multimodal)
and blocks a request before the main model sees it. Run it on your laptop, or on Red Hat
OpenShift AI (RHOAI) with a web UI that shows an unprotected and a protected model side by side.

| Config | Blocks | Model |
|---|---|---|
| `config/pii_vision` | personal data in images (invoice, check, healthcare form, badge, driver license) | Qwen3-VL-8B (or `gemma3`) |
| `config/injection_vision` | text in an image that instructs the AI (overt, hidden or faint) | Qwen3-VL-8B (or `gemma3`) |
| `config/pii_audio` | personal data read out loud | Qwen3-Omni-30B-A3B |
| `config/injection_audio` | spoken instructions aimed at the AI inside a recording | Qwen3-Omni-30B-A3B |

![Invoice with PII: answered without guardrails, blocked with them](docs/screenshots/pii-invoice-blocked.png)
![Audio tab: the unprotected model reads an SSN back, the PII rail blocks it](docs/screenshots/ui-audio-pii-blocked.png)

## How it works

```
user message (text + image or audio) -> input rail: "content safety check input $model=<rail>"
                                  |  the multimodal judge reads the image / hears the clip
                                  |  against a policy prompt -> "safe" | "unsafe, P1, P3"
                 safe -> main model answers (and sees the image / hears the clip)
                 unsafe -> "I'm sorry, I can't respond to that."
```

It is the stock LLM-as-judge content-safety flow of NeMo Guardrails; only the policy prompts
(`config/*/prompts.yml`) are custom, with no new Python actions. Both rails together:
[request-flow diagram](docs/diagrams/request-flow.png).

## 1. Try it on your machine

No cluster needed. The `nemoguardrails` library runs in-process and calls a local model.

```bash
git clone https://github.com/williamcaban/multimodal-guardrails-demo && cd multimodal-guardrails-demo
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install "nemoguardrails" pillow
```

**Images** (Ollama):

```bash
ollama pull qwen3-vl:8b
python make_samples.py                                  # synthetic, fake-data images -> samples/
python demo.py --config pii_vision --model qwen3-vl:8b
python demo.py --config injection_vision --model qwen3-vl:8b
python demo.py --config injection_vision --baseline     # NO rails: does the attack work?
```

**Audio** (`llama-server`, for local testing only; the clips are already in `samples_audio/`):

```bash
llama-server -hf ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF:Q4_K_M --port 8089 --ctx-size 16384 --jinja
python audio_demo.py                                    # PII rail; --config injection_audio for the other
```

The audio model is about 19 GB. Setup details, and how to serve it on a DGX Spark instead:
[docs/serving-models.md](docs/serving-models.md).

## 2. Run it on Red Hat OpenShift AI

The guardrails run in the operator-managed NeMo Guardrails server (a `NemoGuardrails` custom
resource), with a small web UI. Everything lives in one namespace, `mm-guardrails-demo`.

**You need:** an OpenShift AI cluster (tested on RHOAI 3.6 EA2) with the TrustyAI component
`Managed` (`oc get crd nemoguardrails.trustyai.opendatahub.io`); `oc` logged in with rights to
create a namespace; Python 3.12 and `uv`; and model endpoints, which are one of:

- **Existing endpoints.** A vision model (`image_url` support) and, optionally, Qwen3-Omni, behind
  OpenAI-compatible `/v1/chat/completions` URLs that the cluster's pods can reach.
- **Two GPUs on the cluster.** One GPU runs the vision model, the other Qwen3-Omni, both in the
  demo namespace: follow [docs/serving-models.md](docs/serving-models.md#3-on-red-hat-openshift-ai-with-two-gpus),
  then use the URLs it gives in step 2 below.

**Steps**

```bash
# 1. The renderer needs pyyaml.
uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install pyyaml
export OC_CONTEXT=<your-oc-context>        # `oc config current-context`

# 2. Point the script at your models. Keys are read from the environment, never printed.
export MODEL_BASE_URL=https://<host>/<path>/v1  MODEL_NAME=<served model name>
export MODEL_API_KEY=<vision model key>
export AUDIO_BASE_URL=http://<audio-host>:8001/v1 AUDIO_MODEL_API_KEY=<audio key>   # optional: adds the Audio tab

# 3. Deploy. Safe to re-run (SKIP_BUILD=1 reuses the UI image).
rhoai/deploy.sh

# 4. Open the Route it prints, or port-forward.
oc --context "$OC_CONTEXT" -n mm-guardrails-demo port-forward svc/mm-ui 8080:8080
```

Check it: `oc -n mm-guardrails-demo get nemoguardrails,deploy,pods` shows `mm-guardrails` 1/1 and
its pod 2/2 Running. In the UI, "Invoice" with "PII guardrail" is answered unprotected and blocked
protected (P1, P4); "Sales chart" is allowed in every mode. With audio on, open the **Audio** tab
and pick "Social security number".

Remove everything with `OC_CONTEXT=<ctx> rhoai/teardown.sh --yes`. The UI is for a demo: fixed
sample files only, no uploads, rate limited. Variables, what gets created, how to change a policy
or reset: [docs/rhoai.md](docs/rhoai.md).

## Results at a glance

Small synthetic sets: a demo, not a benchmark. On RHOAI the 6 clean and redacted images were all allowed.

| Setup | PII rail | Injection rail |
|---|---|---|
| Images, local Ollama `qwen3-vl:8b` | 8/9 | 8/8 |
| Images, RHOAI, Qwen3-VL-8B-Instruct on vLLM | 4/5 blocked (badge missed) | 5/5 blocked |
| Audio, Qwen3-Omni (local GGUF, DGX Spark, RHOAI) | 7/8 | 7/7 |

Details, misses and latency: [docs/results.md](docs/results.md) (images) and
[docs/audio.md](docs/audio.md) (audio).

## Documentation

| | |
|---|---|
| [docs/serving-models.md](docs/serving-models.md) | Serving the models: llama-server (local testing), DGX Spark, RHOAI with two GPUs |
| [docs/rhoai.md](docs/rhoai.md) | The RHOAI deployment in detail: objects, configs, variables, reset, limits |
| [docs/audio.md](docs/audio.md) | Audio guardrails: design, results, findings, caveats |
| [docs/results.md](docs/results.md) | Image results, with and without rails |
| [docs/GOTCHAS.md](docs/GOTCHAS.md) | Eight things that silently break the verdicts |
| [docs/development.md](docs/development.md) | Tests and the quality gate |

## Gotchas and limits

- The judge prompt's user message must be a bare `"{{ user_input }}"`, or the image is dropped.
- The default `general` prompt turns images into `[+ image]`; override it so the main model sees them.
- Thinking models with a small `max_tokens` fail closed, so blocks look right for the wrong reason.
- Input rails only. VLM judges are probabilistic: for real PII enforcement add deterministic
  detection (Presidio, regex) on top, and an output rail.
- Synthetic images and speech are easier than real ones. Test the rails on your own photos,
  recordings and policies, and tune the prompts in `config/*/prompts.yml` for your scenario.

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Disclaimer

This is a personal demo, not an official Red Hat or NVIDIA product or recommendation. All sample
images and recordings contain fabricated data only. Guardrail verdicts come from a language model
and can be wrong; do not rely on this for real PII or safety enforcement without your own evaluation.
