# AGENTS.md

Guidance for coding agents (Claude Code, OpenCode, and similar) working in this repository,
either to **replicate the demo in the user's own environment** or to **change the code**.
Humans: start with [README.md](README.md).

## What this repo is

A demo of multimodal guardrails with NeMo Guardrails. A multimodal model is the
LLM-as-a-judge: it reads an image (Qwen3-VL) or hears a recording (Qwen3-Omni) and blocks the
request before the main model sees it. Two policies per modality: PII and prompt injection.
It runs two ways: from the command line on a laptop (`nemoguardrails` library plus Ollama or
llama.cpp), and on Red Hat OpenShift AI (RHOAI) with the operator-managed NeMo server and a web
UI that shows an unprotected and a protected model side by side.

## Repository map

```
config/{pii,injection}_{vision,audio}/   the guardrail policies: config.yml + prompts.yml (edit these)
demo.py, audio_demo.py                   command-line runners (rails, or --baseline for the bare model)
make_samples.py, make_audio_samples.py   generate the fabricated sample images / spoken clips
samples/, samples_audio/                 the generated samples (committed; fake data only)
rhoai/deploy.sh, teardown.sh             deploy / remove the demo on a cluster (uses `oc` only)
rhoai/render_configs.py                  turns config/*/prompts.yml into the cluster ConfigMaps
rhoai/k8s/                               Namespace, NemoGuardrails CR, web UI manifests
rhoai/models/                            optional vLLM Deployments for the two models (two GPUs)
ui/mmdemo/, ui/static/, ui/Containerfile web UI: standard-library Python server + plain JS
tests/, scripts/check.sh                 pytest suite and the quality gate
docs/                                    details: rhoai.md, serving-models.md, audio.md, results.md, GOTCHAS.md
```

## Ground rules

- **Secrets.** API keys come from the environment or a git-ignored `.env`; `deploy.sh` stores them in
  Secrets and never prints them. Never put a key, token, IP address, hostname or username in a
  committed file, a command line you show, or a reply. `.env`, `.env.*`, `*.pem` and `*.key` are
  git-ignored; keep it that way.
- **Cluster safety.** Always pass the context explicitly (`OC_CONTEXT=...`; the scripts do). Show
  the user which cluster and namespace you are about to change and get a yes before running
  `deploy.sh`. `teardown.sh` deletes the **whole namespace**: ask first, and never point
  `NAMESPACE` at one the user shares with other work.
- **Fake data only.** Sample images and clips contain fabricated data. Do not add real personal
  data, real credentials or real recordings.
- **`main` is protected.** Work on a branch, run the gate, open a PR (`gh pr create`). Do not push
  to `main`. Commit messages end with the user's `Signed-off-by` line if their setup asks for it.
- **Say what you verified.** Report what you ran and saw. If you could not run something (for
  example the two-GPU manifests need two GPUs), say it was not run.

## Replicate the demo: what the user must supply

Nothing cluster-specific is committed; collect these from the user (do not guess):

| Needed | Used for | Where it goes |
|---|---|---|
| `oc` context of an OpenShift AI cluster with TrustyAI `Managed` (`oc get crd nemoguardrails.trustyai.opendatahub.io`) | the deployment target | `OC_CONTEXT` |
| Vision model endpoint ending in `/v1` that accepts `image_url`, the cluster can reach, and its key | the image judge and target | `MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY` |
| Qwen3-Omni endpoint ending in `/v1` and its key (optional) | the Audio tab | `AUDIO_BASE_URL`, `AUDIO_MODEL_API_KEY`, `AUDIO_MODEL_NAME` |
| Namespace, if not `mm-guardrails-demo` | isolation | `NAMESPACE` |
| Pull access to `registry.redhat.io/ubi9/python-312` | the UI image is built on the cluster | cluster pull secret |
| For the two-GPU path: GPU taints, storage class, GPU sizes | `rhoai/models/*.yaml` | edit the manifests (tolerations, storageClassName, memory flags) |
| Whether the Route's certificate covers its host | browser warning | add the host to the ingress certificate, or use `oc port-forward svc/mm-ui 8080:8080` |

Defaults that only fit the reference cluster: `MODEL_BASE_URL` defaults to RHOAI's in-cluster MaaS
gateway service for a model named `qwen3-vl-8b`; set it explicitly anywhere else. The Secret names
`qwen3-vl-8b-maas-key` and `qwen3-omni-key` (key `api-key`) are fixed in `deploy.sh` and
`rhoai/k8s/20-nemo-guardrails.yaml`; rename them in both places together.

### Procedure

1. **Local first (no cluster).** `uv venv --python 3.12 .venv && source .venv/bin/activate &&
   uv pip install nemoguardrails pillow`. Images: `ollama pull qwen3-vl:8b`, then
   `python demo.py --config pii_vision --model qwen3-vl:8b`. Audio: serve Qwen3-Omni
   ([docs/serving-models.md](docs/serving-models.md): llama-server for local testing, vLLM for a
   DGX Spark), then `python audio_demo.py`. Success: the script prints `matched expectation: N/N`
   with blocks on the PII/injection samples and allows on the controls (the README lists the
   expected counts).
2. **Models on the cluster.** Use existing endpoints, or with two GPUs follow section 3 of
   [docs/serving-models.md](docs/serving-models.md) (`oc apply -f rhoai/models/`).
3. **Deploy.** `uv pip install pyyaml`, export the variables from the table, run
   `rhoai/deploy.sh` (idempotent; `SKIP_BUILD=1` reuses the UI image). If a key is missing the
   script says so and skips that part; it never prompts.
4. **Verify.** `oc -n <ns> get nemoguardrails,deploy,pods`: `mm-guardrails` 1/1 and its pod 2/2
   Running. In the UI, "Invoice" with "PII guardrail" is answered unprotected and blocked
   protected; "Sales chart" is allowed everywhere. If the Protected card shows an error, its
   text names the cause (service missing, credentials rejected, TLS, timeout).
5. **Remove.** `OC_CONTEXT=<ctx> rhoai/teardown.sh --yes` (see Ground rules).

## Customize the guardrails

- **Change a policy:** edit `config/<name>/prompts.yml` (the single source of truth), then
  `SKIP_BUILD=1 rhoai/deploy.sh`. It re-renders the ConfigMaps and restarts NeMo, which reads its
  configuration only at start-up. Re-run the samples before and after; the judge is probabilistic.
- **Another model:** set the model and URL variables. Judge and target are the same model by design;
  to split them, give `main` and the rail entries different values in `rhoai/render_configs.py`.
- **Add a rail or config:** a policy directory under `config/`, a builder in
  `rhoai/render_configs.py`, a `nemoConfigs` entry in `rhoai/k8s/20-nemo-guardrails.yaml`, and the
  UI: `MODES`/`AUDIO_MODES` in `ui/mmdemo/samples.py`, a radio in `ui/static/index.html`, a title in
  `ui/static/app.js`. Add tests.
- **Add samples:** extend `make_samples.py` / `make_audio_samples.py` and `ui/mmdemo/samples.py`.
  Fabricated data only. The UI image bakes the samples in, so rebuild it (no `SKIP_BUILD`).

## Develop

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install "nemoguardrails[server]" pillow httpx pyyaml ruff black mypy vulture pytest pre-commit types-PyYAML
scripts/check.sh      # ruff, black, mypy --strict, vulture, pytest (live tests deselected); run before every commit
```

Standards: Python 3.12, fully typed, 100-column lines. **`ui/` uses the standard library only**
(no third-party packages in the UI image). Keep `pytest -m live` tests opt-in (`NEMO_URL` set).
`deploy.sh` must keep working on macOS bash 3.2 (guard empty arrays with `${arr[@]+"${arr[@]}"}`).

## Gotchas that break the verdicts

Full list in [docs/GOTCHAS.md](docs/GOTCHAS.md). The ones agents hit:

| Wrong | Right |
|---|---|
| Embedding `{{ user_input }}` in other text in a judge prompt | The judge's user message is exactly `"{{ user_input }}"`, or the image/audio is dropped |
| Leaving the default `general` prompt | Override it so the main model sees images (`{{ history \| to_chat_messages }}`) |
| Small `max_tokens` with a thinking model | `max_tokens: 3000`, or the rail fails closed and blocks everything |
| `config_id` at the top level of the request | It must be nested: `"guardrails": {"config_id": ...}` |
| Same `$model=` name for two rails | Distinct names; each names a `models:` entry and its prompt |
| Expecting the library's audio handling to match the RHOAI server | Test your NeMo version with `input_audio` before relying on it |

The `mm-ui` pod has a 256Mi memory limit: keep the UI process small and do not load every sample
into memory at once (a test probe that did was OOM-killed).

## Not verified (do not claim otherwise)

The `rhoai/models/` manifests were accepted by the cluster API but not run end to end. Audio goes
to the model over plain HTTP. Real photos, noisy audio and other policies need the user's own
testing and prompt tuning. Reference tested version: RHOAI 3.6 EA2.
