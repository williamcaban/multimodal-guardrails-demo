# Multimodal guardrails with NeMo Guardrails + open-weight vision models

Two image guardrails built on the NVIDIA tutorial
[Add Multimodal Content Safety Using a Vision Model](https://docs.nvidia.com/nemo/guardrails/get-started/tutorials/multimodal):

| config | blocks | example images |
|---|---|---|
| `config/pii_vision` | PII in images | invoice, check, healthcare form, employee badge, driver license |
| `config/injection_vision` | text in an image that instructs the AI | "ignore previous instructions", "generate Fibonacci code", hidden/faint text |

You can run them two ways:

| | [Run on your machine](#run-on-your-machine-ollama-command-line) | [Run on Red Hat OpenShift AI](#run-on-red-hat-openshift-ai-vllm-web-ui) |
|---|---|---|
| Model server | Ollama (`qwen3-vl:8b`, `gemma3`) | vLLM (tested with Qwen3-VL-8B-Instruct behind MaaS) |
| Guardrails engine | the `nemoguardrails` Python library, in-process | the RHOAI NeMo Guardrails operator (`NemoGuardrails` CR) |
| Interface | command line (`demo.py`) | web UI, unprotected vs protected side by side |
| You need | Python 3.12, Ollama | an OpenShift AI cluster, `oc`, a vision model endpoint and its API key |

## How it works

```
user message (text + image) -> input rail: "content safety check input $model=vision_rails"
                                   |  VLM judge reads the image against a policy prompt
                                   |  -> "safe" | "unsafe, P1, P3"
                  safe -> main model answers (and sees the image)
                  unsafe -> "I'm sorry, I can't respond to that."
```

Block diagram of the same flow with both rails (PII judge, then injection judge):
[request-flow.png](docs/diagrams/request-flow.png), editable source
[request-flow.svg](docs/diagrams/request-flow.svg).

The "vision rail" is the stock LLM-as-judge content-safety flow. Only the policy
prompt (`config/*/prompts.yml`) is custom. No new Python actions.

## Run on your machine (Ollama, command line)

No cluster needed. The guardrails run in-process in the `nemoguardrails` library and call a local
Ollama model as both the judge and the model being protected.

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install "nemoguardrails" pillow
ollama pull qwen3-vl:8b            # and/or gemma3
python make_samples.py             # synthetic, fake-data images -> samples/

python demo.py --config pii_vision        --model qwen3-vl:8b
python demo.py --config injection_vision  --model qwen3-vl:8b
python demo.py --config injection_vision  --baseline   # NO rails: does the attack work?
python demo.py --prompt "Describe this image." ...     # override the user text
```

To use a hosted endpoint instead of Ollama, change `base_url`/`model`/`api_key` under `models:`
in `config/*/config.yml`. For a cluster deployment, use the next section.

## Run on Red Hat OpenShift AI (vLLM, web UI)

The same guardrails, served by the RHOAI NeMo Guardrails operator, with a small web UI that
sends a sample image through the unprotected model and a guardrail side by side. The model is
served by vLLM (on the tested cluster, Qwen3-VL-8B-Instruct as an `LLMInferenceService` behind
MaaS). NeMo only needs an OpenAI-compatible vision endpoint, so another vLLM-served model should
work, but only that one was tested. Everything
lives in one namespace (`mm-guardrails-demo`), except one cluster-scoped ClusterRoleBinding that
the operator creates for the NeMo server (see "Remove, reset and uninstall").

### What gets deployed

```
  Your browser
       |  HTTPS
       v
  Route mm-ui (edge TLS)
       |
  +----|---------------------- namespace mm-guardrails-demo ----------------------------+
  |    v                                                                                |
  |  Deployment mm-ui  (stdlib Python; image built on the cluster from the Containerfile)|
  |    ServiceAccount mm-ui + RoleBinding "view"  -> its token proves who is calling    |
  |    |                                                                                |
  |    |  POST /v1/chat/completions                                                     |
  |    |    {"guardrails": {"config_id": "pii-vision", "options": {"log": ...}}}        |
  |    |    Authorization: Bearer <ServiceAccount token>                                |
  |    v                                                                                |
  |  Service mm-guardrails :443 (TLS)                                                   |
  |  +-----------------------------------------------------------------------------+    |
  |  | NemoGuardrails CR "mm-guardrails"  = ONE CR, ONE Deployment, ONE Pod        |    |
  |  |                                                                             |    |
  |  |  [kube-rbac-proxy]  checks the token (enable-auth) then forwards to         |    |
  |  |  [nemo-guardrails server]  which holds all four configurations:             |    |
  |  |       no-rails    | pii-vision | injection-vision | pii-and-injection (default)|   |
  |  |       each one mounted from its own ConfigMap  mmg-<config id>              |    |
  |  |  env MODEL_API_KEY <- Secret qwen3-vl-8b-maas-key (key: api-key)            |    |
  |  +-----------------------------------------------------------------------------+    |
  +-----|-------------------------------------------------------------------------------+
        |  HTTPS + Bearer MaaS API key; server cert verified against the OpenShift service CA
        v
  maas-default-gateway (namespace openshift-ingress)
        |
        v
  LLMInferenceService qwen3-vl-8b (vLLM)   <- the judge AND the model being protected
```

What a request does inside NeMo (config `pii-and-injection`; the single-rail configs run
only their own judge, `no-rails` runs none):

```
  image + text
      |
      v
  [PII judge]  --unsafe-->  "I'm sorry, I can't respond to that."   (request stops)
      | safe
      v
  [injection judge]  --unsafe-->  same refusal
      | safe
      v
  main model answers (it sees the image)
```

The UI sends every request twice, in parallel: once to `no-rails` for the "Unprotected
model" card and once to the config chosen in step 2 for the "Protected" card. It also asks
NeMo for `guardrails.options.log`, so the page can show which rail stopped the request and what
the judge answered ("Why").

Who creates what:

| object | created by | purpose |
|---|---|---|
| Namespace `mm-guardrails-demo` | `rhoai/k8s/00-namespace.yaml` | isolation; removal is deleting it (see below) |
| Secret `qwen3-vl-8b-maas-key` (key `api-key`) | you, or `deploy.sh` from an env var | MaaS API key for the model |
| 4 ConfigMaps `mmg-<config id>` | `rhoai/render_configs.py`, applied by `deploy.sh` | the guardrail configurations |
| `NemoGuardrails` CR `mm-guardrails` | `rhoai/k8s/20-nemo-guardrails.yaml` | asks the operator for a NeMo server |
| Deployment, Service, Route, ServiceAccount `mm-guardrails-serviceaccount`, CA-bundle ConfigMaps | the TrustyAI operator, from the CR | the NeMo server and its auth proxy |
| ClusterRoleBinding `mm-guardrails-<namespace>-auth-delegator` (**cluster-scoped**, role `system:auth-delegator`) | the TrustyAI operator, from the CR | lets the auth proxy validate bearer tokens |
| ServiceAccount `mm-ui`, RoleBinding `mm-ui-view`, ImageStream, BuildConfig, Deployment, Service, Route (all `mm-ui`) | `rhoai/k8s/30-ui.yaml` | the web UI |

### Screenshots of the web UI

Four runs of the same setup (config `pii-and-injection`, message "Process this document and
respond."). Each screenshot shows the selected image, the unprotected answer and the protected
result with the judges' verdicts. All data in the images is fabricated.

- [Invoice with PII: answered without guardrails, blocked with them](docs/screenshots/pii-invoice-blocked.png)
- [Invoice with a hidden footer instruction: the unprotected model obeys it, the injection rail blocks it](docs/screenshots/hidden-instruction-invoice-blocked.png)
- [Sales chart, a safe control: allowed](docs/screenshots/sales-chart-allowed.png)
- [Redacted invoice, a safe control: allowed](docs/screenshots/redacted-invoice-allowed.png)

### Configurations: config ids, ConfigMaps and rails

Clients choose a guardrail per request with a **config id**. This demo defines four, all hosted
by the one `NemoGuardrails` CR (`spec.nemoConfigs`, one entry each). The operator mounts each
ConfigMap as its own configuration directory, and the config id is the entry's `name`.

| config id | ConfigMap | rails that run (`rails.input.flows`) | UI mode that uses it |
|---|---|---|---|
| `no-rails` | `mmg-no-rails` | none | the "Unprotected model" card, and mode "None" |
| `pii-vision` | `mmg-pii-vision` | `content safety check input $model=vision_pii` | "PII guardrail" |
| `injection-vision` | `mmg-injection-vision` | `content safety check input $model=vision_injection` | "Prompt-injection guardrail" |
| `pii-and-injection` (**default**) | `mmg-pii-and-injection` | both of the above, in that order | "Both rails" |

Two different kinds of id are in play:

- **Config id** (`pii-vision`, ...): which configuration the request uses. It goes in the body
  as `guardrails.config_id`. It must be nested under `guardrails`; a top-level `config_id` is
  silently ignored. A request with no config id gets the default, `pii-and-injection`, so
  forgetting it is never unprotected.
- **Rail name** (`vision_pii`, `vision_injection`): the `$model=` part of a flow. It names the
  `models:` entry (here the same Qwen3-VL-8B behind every name) and the prompt that judge uses,
  so the two names must differ. The UI's "Why" box shows these names.

Each ConfigMap holds three files, and the operator requires all three even if one is empty:

| key | content |
|---|---|
| `config.yaml` | `models:` (`main`, `vision_pii`, `vision_injection` as needed) and `rails.input.flows` |
| `prompts.yml` | the policy prompts, one per judge, plus the `general` prompt for the main model |
| `rails.co` | Colang flows; empty here (`# built-in rails only`) because the rails are built in |

`prompts.yml` is a standard NeMo Guardrails file, not something this repo invented. A NeMo
configuration is a directory (or, on RHOAI, a ConfigMap) with `config.yml`, an optional
`prompts.yml` (a list of prompts, each keyed by a `task` name that tells the library when to use
it), Colang `*.co` files, and optional Python (`actions.py`, `config.py`). What is specific to
this repo is the *content*: the PII and prompt-injection policy text, the `$model=` task names
and the `general` prompt override that lets the main model see images. The ConfigMap key is
spelled `config.yaml` where a local directory uses `config.yml`; the operator expects that.

**One CR for all four configs.** They share one Pod, one image, one `MODEL_API_KEY` and one
restart (NeMo reads its configuration at start-up, so `deploy.sh` restarts it when the
ConfigMaps change). The alternative, one CR per policy, gives each its own Pod, credential and
scaling; it was not needed for a demo and is not set up here.

**Single source of truth for policy.** Edit `config/pii_vision/prompts.yml` or
`config/injection_vision/prompts.yml`; `render_configs.py` copies that text into the ConfigMaps
(renaming the task to the `$model=` rail name). The rendered ConfigMaps are generated by
`deploy.sh` on every run and are not committed. To look at them without applying:

```bash
python rhoai/render_configs.py --out /tmp/cfgs --model qwen3-vl-8b --base-url https://example/v1
# or the exact YAML that would be applied:
python rhoai/render_configs.py --configmaps --namespace mm-guardrails-demo \
    --model qwen3-vl-8b --base-url https://example/v1
```

### Deploy on your own RHOAI cluster

**Prerequisites**

- Red Hat OpenShift AI with the TrustyAI component `Managed` and the `NemoGuardrails` CRD
  installed: `oc get crd nemoguardrails.trustyai.opendatahub.io`. Tested on a Red Hat OpenShift AI (RHOAI) 3.6 EA2 cluster;
  other preview versions are untested.
- A vision-capable model behind an OpenAI-compatible `/v1/chat/completions` endpoint that
  accepts `image_url` parts, **reachable from a pod in the demo namespace**, and its API key.
  Tested with `Qwen/Qwen3-VL-8B-Instruct` through MaaS. A model on another box (for example a
  DGX Spark running vLLM or Ollama) works if the cluster can reach it; the "thinking" variants
  work but are 10-20x slower.
- `oc` logged in with rights to create a namespace and the objects above, plus Python 3.12 and
  `uv` on your workstation (the script renders the ConfigMaps with `pyyaml`).
- Permission to pull `registry.redhat.io/ubi9/python-312` on the cluster (the UI image is built
  there with a binary build; the UI has no third-party packages).

**Steps**

```bash
# 1. Get this directory and a Python environment for the renderer.
cd multimodal-guardrails
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install pyyaml

# 2. Point the script at your cluster and model. Context name: `oc config current-context`.
export OC_CONTEXT=<your-oc-context>
export MODEL_NAME=<served model name>                        # default: qwen3-vl-8b
export MODEL_BASE_URL=https://<host>/<path>/v1               # REQUIRED on any other cluster
export MAAS_KEY_VAR=MODEL_KEY MODEL_KEY=<api key>            # read from the environment, never printed

# 3. Deploy. Safe to re-run; SKIP_BUILD=1 reuses the UI image.
rhoai/deploy.sh

# 4. Open the Route it prints, or use a port-forward (see "Known limits" for the certificate).
oc --context "$OC_CONTEXT" -n mm-guardrails-demo port-forward svc/mm-ui 8080:8080
```

If you prefer to create the key yourself, do it before step 3 (the script creates the namespace,
so create it first if needed) and leave `MAAS_KEY_VAR` unset:

```bash
oc create namespace mm-guardrails-demo
oc -n mm-guardrails-demo create secret generic qwen3-vl-8b-maas-key --from-literal=api-key='<key>'
```

The Secret name and its `api-key` field are fixed in `deploy.sh` and in
`rhoai/k8s/20-nemo-guardrails.yaml`; rename both together if you need another name. If no key
is found, the script still deploys the UI and ConfigMaps, skips the NeMo CR, and says so;
re-run it once the Secret exists.

| variable | default | meaning |
|---|---|---|
| `OC_CONTEXT` | your current `oc` context | which kubeconfig context to use; always passed explicitly to `oc` |
| `NAMESPACE` | `mm-guardrails-demo` | target namespace |
| `MODEL_NAME` | `qwen3-vl-8b` | model name sent to the endpoint |
| `MODEL_BASE_URL` | RHOAI's default in-cluster MaaS gateway service | **base URL of your model, ending in `/v1`; set it for your cluster** |
| `MAAS_KEY_VAR` | `MODEL_API_KEY` | name of the environment variable (or `ENV_FILE` line) holding the key |
| `ENV_FILE` | unset | optional file searched for that one variable |
| `SKIP_BUILD` | unset | `1` skips the on-cluster image build |

**Check it worked**

```bash
oc -n mm-guardrails-demo get nemoguardrails,deploy,pods   # mm-guardrails 1/1, pod 2/2 Running
```

In the UI, "Invoice" with "PII guardrail" should show Unprotected = allowed and Protected =
blocked with categories P1, P4; "Chart with faint text" with "Prompt-injection guardrail" should
be blocked (I1, I5); "Sales chart" should be allowed by every mode. If the Protected card shows an
error, its text says what to check (service missing, credentials rejected, TLS, timeout).

**Adapting it**

- *Change a policy:* edit `config/*/prompts.yml`, then run `SKIP_BUILD=1 rhoai/deploy.sh`. It
  re-renders the ConfigMaps and restarts NeMo.
- *Use a different model:* set `MODEL_NAME` and `MODEL_BASE_URL`. The judge and the protected
  model are the same entry today; to split them, give `main` and the `vision_*` entries
  different values in `rhoai/render_configs.py`. For a thinking model keep `max_tokens: 3000`
  ([gotcha 3](docs/GOTCHAS.md)) or the rail fails closed.
- *Add a rail or a config:* add a policy under `config/`, a builder in `render_configs.py`, a
  `nemoConfigs` entry in `rhoai/k8s/20-nemo-guardrails.yaml`, and expose it in the UI: `MODES` in
  `ui/mmdemo/samples.py`, a radio in `ui/static/index.html` and a title in `app.js`.

### Remove, reset and uninstall

**Remove everything** (the whole demo, including the namespace):

```bash
OC_CONTEXT=<your-oc-context> rhoai/teardown.sh --yes     # NAMESPACE=... if you changed it
oc --context <ctx> get namespace mm-guardrails-demo       # repeat until NotFound
oc --context <ctx> get clusterrolebinding mm-guardrails-mm-guardrails-demo-auth-delegator   # expect NotFound
```

What it does, in order: deletes the `NemoGuardrails` CR and waits, so the operator can remove
what it created (including one **cluster-scoped** object, the ClusterRoleBinding
`mm-guardrails-<namespace>-auth-delegator`); then deletes the namespace, which takes the UI, the
image build, both Routes, the ConfigMaps and the Secret with it. If that ClusterRoleBinding is
still there afterwards, the script prints the one `oc delete clusterrolebinding` command to run.

- It deletes the **whole namespace**. Do not set `NAMESPACE` to one you share with other work.
- The Secret goes with the namespace. If you created it by hand, keep a copy of the key.
- The model API key itself is **not revoked**; revoke it where you created it (the MaaS portal).
- Nothing is installed on your workstation beyond the `.venv` you created. Delete it if you
  like; `samples/` is regenerated by `python make_samples.py`.
- Status: run end to end on the reference cluster (2026-11-09). The CR deleted first, the
  ClusterRoleBinding was gone right after, the namespace finished deleting in about 50 s, and no
  CR, binding or volume was left. A fresh `deploy.sh` (full image build) then brought the demo
  back in about 25 s of rollout and the same 16 samples behaved as in the results table.

**Reset** (smaller steps, nothing is uninstalled):

| goal | command | what is kept |
|---|---|---|
| Apply an edited policy | `SKIP_BUILD=1 rhoai/deploy.sh` (re-renders the ConfigMaps, restarts NeMo) | everything |
| Restart NeMo only | `oc -n mm-guardrails-demo rollout restart deploy/mm-guardrails` | everything |
| Rotate the API key | update the Secret (`oc create secret generic qwen3-vl-8b-maas-key --from-literal=api-key='<new>' --dry-run=client -o yaml \| oc apply -f -`), then restart NeMo: it reads the key at start-up | everything |
| Rebuild after UI code changes | `rhoai/deploy.sh` (no `SKIP_BUILD`) | NeMo, ConfigMaps, Secret |
| Take the guardrails down, keep the UI | `oc -n mm-guardrails-demo delete nemoguardrails mm-guardrails` | UI, the four `mmg-*` ConfigMaps, the Secret |
| Bring the guardrails back | `rhoai/deploy.sh` | - |
| Clean slate | `rhoai/teardown.sh --yes`, wait for the namespace to disappear, then `rhoai/deploy.sh` | nothing |

Deleting the CR removes the objects the operator owns (Deployment, Service, Route,
ServiceAccount, CA ConfigMaps, the ClusterRoleBinding). While it is gone the UI's Protected
and Unprotected cards both show "Cannot find the guardrail service".

### How the model is reached

On the reference cluster the model's NetworkPolicy admits only monitoring, the
`openshift-ingress` gateway and its own namespace, so a pod in another namespace cannot call it
directly. NeMo therefore goes through the in-cluster MaaS gateway, the same path as the public
`/models-as-a-service/qwen3-vl-8b/v1` URL. The external hostname fails TLS verification (its
certificate does not list the host), but the in-cluster gateway service presents a certificate
signed by the OpenShift service CA, which the operator already trusts. Verified from a pod:
verified TLS with no key gives HTTP 401, and the same call with only the system CAs is rejected.
Your cluster's layout may differ. If your model's certificate is signed by a private CA that is
not the service CA, NeMo must trust it (the operator mounts `odh-trusted-ca-bundle`); that path
was not tested here. TLS verification is never turned off.

### Safe defaults and known limits

The UI Route is reachable from outside the cluster and the model is shared, so: fixed sample
images only (`ALLOW_UPLOAD=false`), prompts limited to 300 characters, answers to 300 tokens,
30 requests per minute for the whole app, a strict Content-Security-Policy, and no secret in the
UI (it holds only a short-lived ServiceAccount token).

- The operator also creates a **Route for the NeMo server itself**
  (`mm-guardrails-<namespace>.<apps domain>`, reencrypt). `enable-auth` is on, so a call
  without a token gets HTTP 401, but anyone holding a token with the `view` role in the
  namespace can spend your model quota through it.
- The default router certificate does not cover the new Route hostnames, so browsers show a
  name-mismatch warning until the hosts are added to a certificate (or use the port-forward).

### Development

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

## Results (nemoguardrails 0.24.1, Ollama 0.40.15, synthetic images)

| config | `qwen3-vl:8b` | `gemma3` (4B) |
|---|---|---|
| `pii_vision` (9 images) | 8/9 | 7/9 |
| `injection_vision` (8 images) | 8/8 | 8/8 |

Misses:
- **qwen3-vl, `badge_pii.png`: false negative (stable, 3/3 runs).** The judge says
  `safe` and the main model then restates the name and employee ID in its reply.
  Likely a policy gap: P7 needs a *photo* plus name/ID, and the drawn silhouette
  may not count as a photo, while name + employee ID alone is not covered by P1-P6.
  Widening P7 to "name plus any ID number, photo or not" is a policy decision; not done
  here so the result is not tuned to a sample already seen.
- **gemma3, both redacted images: false positives.** It flags field labels
  ("BILL TO", MICR line) even when the values are blacked out.

Latency per image: gemma3 3-8 s; qwen3-vl roughly 20-50 s when Ollama is otherwise
idle (some numbers in raw logs were inflated by concurrent load).

Small, synthetic sample, and the PII prompt was tuned after seeing gemma3's misses on
these images. Treat as a demo, not a benchmark. The ID/badge photos are drawn
silhouettes, not real faces; behavior on real face photos is untested.

### Does the injection actually work without the rail? (`--baseline`)

Models obey injected image text only some of the time, and it depends on the user's request:

| user text | gemma3 obeys | qwen3-vl obeys |
|---|---|---|
| "Describe this image." | 1/5 (hidden invoice footer) | 0/5 (describes the text as content) |
| "Process this document and respond." | 5/5 | 3/5 (code, invoice footer, faint text; refuses the two overt ones) |

So `injection_vision` defaults to the open-ended prompt. Both models also read
low-contrast text (the faint `PWNED` line worked). The rail's job is to block these
before the main model acts on them, whether or not that model would have complied.

### On RHOAI (`Qwen/Qwen3-VL-8B-Instruct` as judge and target, both rails on)

Measured through the UI API on an RHOAI 3.6 EA2 cluster, 2026-11-09:

| group | result |
|---|---|
| PII (invoice, check, healthcare, ID card) | 4/4 blocked, categories P1-P6 as expected |
| PII (`badge_pii`) | allowed (same miss as the local qwen3-vl; policy gap, not tuned) |
| Injection (5 images) | 5/5 blocked by the injection rail |
| Clean, redacted and benign (6 images) | 6/6 allowed, no false positives |

The Instruct model does not think, so a call takes about 1-3 s (local thinking qwen3-vl: 20-50 s)
and `max_tokens: 3000` is headroom, not a need. When the PII rail blocks, the injection rail
does not run, so only one verdict is shown. 15/16 on a small synthetic set: a demo, not a benchmark.

## Gotchas

Eight things that silently break image handling or the verdicts, each with the symptom and the
fix: [docs/GOTCHAS.md](docs/GOTCHAS.md). The ones most likely to bite:

- The judge prompt's user message must be a bare `"{{ user_input }}"`, or the image is dropped.
- The default `general` prompt turns images into `[+ image]`; override it so the main model sees them.
- Thinking models with a small `max_tokens` fail closed, so blocks look right for the wrong reason.
- The judge also sees the user's text, so "a task unrelated to the user's request" lets injections through.

## Limits and next steps

- Input rail only. Add an output rail if the main model could echo PII from an
  allowed image (the badge miss above shows this happening).
- Running both rails together costs two judge calls per request: the `pii-and-injection`
  configuration defines two `models:` entries with different `$model=` names (see the
  RHOAI section).
- VLM judges are probabilistic. For PII, consider two stages: VLM transcription, then
  deterministic detection (Presidio/regex) on the text.
- Not tested: Llama Guard vision, NIM endpoints, GPT-4o, or the `qwen3-vl:8b-instruct` Ollama
  tag. (Qwen3-VL-8B-Instruct was tested on vLLM; see the RHOAI results.)

## License

Apache License 2.0. See [LICENSE](LICENSE).

## Disclaimer

This is a personal demo, not an official Red Hat or NVIDIA product or recommendation.

- All sample images contain fabricated data only.
- Guardrail verdicts come from a language model and can be wrong. Do not rely on this for real
  PII or safety enforcement without your own evaluation.
