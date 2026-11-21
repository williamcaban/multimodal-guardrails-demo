# Serving the models

The demo needs OpenAI-compatible chat endpoints: a vision model for the image rails and an
audio model (Qwen3-Omni) for the audio rails. Pick the section that matches where you are
running it. Back to the [README](../README.md).

| Where | For | Section |
|---|---|---|
| Your laptop, with `llama-server` | **Local testing only** of the audio rails from the command line | [1](#1-local-testing-on-a-laptop-llama-server) |
| An NVIDIA DGX Spark, with vLLM | Running the audio example on a Spark | [2](#2-running-the-example-on-an-nvidia-dgx-spark-vllm) |
| An OpenShift AI cluster with two GPUs | The whole demo on the cluster: vision model on one GPU, Qwen3-Omni on the other | [3](#3-on-red-hat-openshift-ai-with-two-gpus) |

The vision model for local testing is served by Ollama; see the README.

## 1. Local testing on a laptop (llama-server)

**For trying the audio rails on your own machine, not for a cluster.** It uses a 4-bit quantized
GGUF of Qwen3-Omni (about 18.6 GB download, roughly 20 GB of memory) through llama.cpp's
`llama-server`. Ollama has no Qwen3-Omni, which is why llama.cpp is used. Tested on an Apple
M1 Max with 64 GB.

```bash
brew install llama.cpp          # or build llama.cpp; you need the `llama-server` binary
llama-server -hf ggml-org/Qwen3-Omni-30B-A3B-Instruct-GGUF:Q4_K_M --port 8089 --ctx-size 16384 --jinja

# in another terminal, from this repository (the 14 clips are already in samples_audio/)
python audio_demo.py                          # the PII rail
python audio_demo.py --config injection_audio # the prompt-injection rail
python audio_demo.py --config injection_audio --baseline   # no rails: does the attack work?
```

`config/pii_audio` and `config/injection_audio` already point at `http://localhost:8089/v1`.
`make_audio_samples.py` regenerates the clips with the macOS `say` command; you only need it if you
change the clip texts.

## 2. Running the example on an NVIDIA DGX Spark (vLLM)

**This is the configuration used to run the example on a DGX Spark** (GB10, aarch64, 128 GB of
unified memory). It serves the full bf16 model with vLLM 0.34.0. The upstream vLLM image is
enough for audio in, text out; vLLM-Omni is not needed.

```bash
# vllm-omni.env contains one line:  VLLM_API_KEY=<a key you choose>
docker run -d --name vllm-omni --gpus all --ipc=host --ulimit memlock=-1 -p 8001:8000 \
  -v $HOME/.cache/huggingface/hub:/root/.cache/huggingface/hub --env-file vllm-omni.env \
  vllm/vllm-openai:v0.34.0 Qwen/Qwen3-Omni-30B-A3B-Instruct --host 0.0.0.0 --port 8000 \
  --served-model-name qwen3-omni --max-model-len 32768 --gpu-memory-utilization 0.65 \
  --limit-mm-per-prompt '{"audio":1,"image":4,"video":0}'
```

- The first start downloads about 66 GiB of weights.
- `--gpu-memory-utilization 0.65` leaves room for another model on the same Spark. If the Spark is
  otherwise idle you can raise it.
- Run the example from any machine that can reach the Spark:

  ```bash
  AUDIO_DEMO_API_KEY=<the key> python audio_demo.py --base-url http://<spark>:8001/v1 --model qwen3-omni
  ```

- To use it from the web UI on RHOAI, give the same URL to the deploy script (see
  [audio.md](audio.md)): `AUDIO_BASE_URL=http://<spark>:8001/v1`. The cluster's pods must be able
  to reach the Spark, and the traffic is plain HTTP, so keep it on a private network.

## 3. On Red Hat OpenShift AI with two GPUs

This runs both models in the demo namespace, next to the guardrails, with no MaaS gateway.
**Assumptions:** the NVIDIA GPU Operator is installed (pods can request `nvidia.com/gpu`); one GPU
with 24 GB or more for the vision model; one GPU with about 80 GB (A100 or H100 80 GB class) for
Qwen3-Omni in bf16; the cluster can reach huggingface.co (the weights download on the first
start); a default storage class for two PersistentVolumeClaims (40 Gi and 120 Gi).

```bash
export NS=mm-guardrails-demo
oc create namespace $NS

# 1. One API key per model. deploy.sh and the vLLM servers read the same Secrets.
oc -n $NS create secret generic qwen3-vl-8b-maas-key --from-literal=api-key="$(openssl rand -hex 24)"
oc -n $NS create secret generic qwen3-omni-key       --from-literal=api-key="$(openssl rand -hex 24)"

# 2. Start both vLLM servers (one GPU each). The first start downloads the weights.
oc -n $NS apply -f rhoai/models/
oc -n $NS rollout status deploy/qwen3-vl --timeout=40m
oc -n $NS rollout status deploy/qwen3-omni --timeout=60m     # follow progress: oc -n $NS logs -f deploy/qwen3-omni

# 3. Deploy the guardrails and the web UI against them (see the README for the prerequisites).
MODEL_BASE_URL=http://qwen3-vl:8000/v1 AUDIO_BASE_URL=http://qwen3-omni:8000/v1 rhoai/deploy.sh
```

The names in `rhoai/models/` (`qwen3-vl`, `qwen3-omni`, model names `qwen3-vl-8b` and `qwen3-omni`)
match the deploy script's defaults. The model Services are `ClusterIP`: only pods in the namespace
can reach them, and the Secrets stop anyone in the namespace without the key.

**What was and was not verified.** The two manifests were accepted by the cluster API
(`oc apply --dry-run=server`), and they use the vLLM arguments that ran the vision model on the
reference RHOAI cluster and Qwen3-Omni on a DGX Spark. They were **not run end to end**: the
reference cluster has a single GPU. Expect to adjust these:

- **GPU nodes:** uncomment `tolerations` in the manifests if your GPU nodes are tainted, or add a
  `nodeSelector` to put each model on a different node or GPU class.
- **Qwen3-Omni memory:** the bf16 weights are about 66 GiB. On an 80 GB GPU there is little room
  left for the KV cache; if vLLM stops with a KV-cache error, lower `--max-model-len` (for example
  to 16384) or raise `--gpu-memory-utilization` a little.
- **Smaller GPUs:** a quantized Qwen3-Omni (for example `Intel/Qwen3-Omni-30B-A3B-Instruct-int4-AutoRound`,
  about 26 GB, listed in the vLLM-Omni docs) or splitting the model over two GPUs with
  `--tensor-parallel-size 2` should be possible. Neither was tried here.
- **Image:** the manifests use the community `vllm/vllm-openai:v0.34.0` image. Replace it with the
  vLLM image your organisation supports if needed.
- **Already have a vision model?** Skip `qwen3-vl.yaml` and point `MODEL_BASE_URL` at it (the
  MaaS route is the tested path; see [rhoai.md](rhoai.md)).
