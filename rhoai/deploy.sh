#!/usr/bin/env bash
# Deploy the multimodal guardrails demo (NeMo Guardrails + web UI) to an OpenShift AI cluster.
# Idempotent: safe to re-run. Uses `oc` only, with an explicit --context (never the global one).
#
# Model access: NeMo calls the in-cluster MaaS gateway, whose serving cert is signed by the
# OpenShift service CA (verified, never skipped). It needs a MaaS API key, read from the Secret
# KEY_SECRET (key `api-key`) in the namespace. If that Secret is missing, it is created from the
# variable named by MAAS_KEY_VAR (default MODEL_API_KEY), taken from the environment or, if you
# set ENV_FILE, from that file. The key is never printed and never put on a command line.
#
# Audio (optional, experimental): set AUDIO_BASE_URL to the /v1 URL of a vLLM server running
# Qwen3-Omni (for example http://<host>:8001/v1) to also deploy the four *-audio configurations
# and the UI's Audio tab. Its API key is read like the MaaS key, from the variable named by
# AUDIO_KEY_VAR (default AUDIO_MODEL_API_KEY), and stored in the Secret qwen3-omni-key. Without
# AUDIO_BASE_URL nothing about audio is deployed.
#
# Environment overrides: OC_CONTEXT (default: your current oc context) NAMESPACE MODEL_NAME
#                        MODEL_BASE_URL MAAS_KEY_VAR ENV_FILE
#                        AUDIO_BASE_URL AUDIO_MODEL_NAME AUDIO_KEY_VAR
#                        SKIP_BUILD=1 (reuse the existing UI image)
set -euo pipefail
cd "$(dirname "$0")/.."

CTX="${OC_CONTEXT:-$(oc config current-context)}"
NS="${NAMESPACE:-mm-guardrails-demo}"
MODEL_NAME="${MODEL_NAME:-qwen3-vl-8b}"
GATEWAY="maas-default-gateway-data-science-gateway-class.openshift-ingress.svc.cluster.local"
MODEL_BASE_URL="${MODEL_BASE_URL:-https://${GATEWAY}/models-as-a-service/${MODEL_NAME}/v1}"
KEY_VAR="${MAAS_KEY_VAR:-MODEL_API_KEY}"
KEY_SECRET="qwen3-vl-8b-maas-key"  # must match env in rhoai/k8s/20-nemo-guardrails.yaml
ENV_FILE="${ENV_FILE:-}"
AUDIO_BASE_URL="${AUDIO_BASE_URL:-}"
AUDIO_MODEL_NAME="${AUDIO_MODEL_NAME:-qwen3-omni}"
AUDIO_KEY_VAR="${AUDIO_KEY_VAR:-AUDIO_MODEL_API_KEY}"
AUDIO_KEY_SECRET="qwen3-omni-key"  # must match env in rhoai/k8s/20-nemo-guardrails.yaml
PY=".venv/bin/python"

oc_() { oc --context "$CTX" --request-timeout=60s "$@"; }

# Fill in the namespace and the audio switch. When audio is off, drop the marked audio blocks.
manifest() {
  local filter=()
  [[ "$AUDIO_ON" == 1 ]] || filter=(-e '/# audio:begin/,/# audio:end/d')
  sed -e "s/__NAMESPACE__/${NS}/g" -e "s/__AUDIO_ENABLED__/$([[ "$AUDIO_ON" == 1 ]] && echo true || echo false)/g" \
    ${filter[@]+"${filter[@]}"} "$1"
}

# read_key VAR: the value of VAR from the environment or, failing that, from ENV_FILE.
read_key() {
  local var="$1"
  if [[ -n "${!var:-}" ]]; then
    printf '%s' "${!var}"
  elif [[ -n "$ENV_FILE" && -f "$ENV_FILE" ]]; then
    # Read just this one variable; the file also holds unrelated credentials.
    { grep -E "^${var}=" "$ENV_FILE" || true; } | tail -1 | cut -d= -f2- \
      | sed -e "s/^[\"']//" -e "s/[\"']\$//"
  fi
}

# ensure_secret NAME VAR: create Secret NAME (key api-key) from VAR unless it already exists.
# Returns 0 when the Secret is available afterwards, 1 when there is no value for it.
ensure_secret() {
  local name="$1" var="$2" key
  if oc_ -n "$NS" get secret "$name" -o jsonpath='{.data.api-key}' 2>/dev/null | grep -q .; then
    echo "== API key: using existing secret ${NS}/${name} (value not shown)"
    return 0
  fi
  key="$(read_key "$var")"
  [[ -n "$key" ]] || return 1
  echo "== API key secret ${name} (value not shown)"
  oc_ -n "$NS" create secret generic "$name" \
    --from-file=api-key=<(printf '%s' "$key") --dry-run=client -o yaml | oc_ apply -f -
  return 0
}

# Audio is deployed only when AUDIO_BASE_URL is set and its key is available (decided below).
AUDIO_ON=0
[[ -z "$AUDIO_BASE_URL" ]] || AUDIO_ON=1

echo "== context=${CTX} namespace=${NS} model=${MODEL_NAME}"
echo "   model url: ${MODEL_BASE_URL}"

echo "== namespace"
manifest rhoai/k8s/00-namespace.yaml | oc_ apply -f -

HAVE_KEY=0
if ensure_secret "$KEY_SECRET" "$KEY_VAR"; then
  HAVE_KEY=1
else
  echo "!! No secret ${NS}/${KEY_SECRET} and ${KEY_VAR} is not set (environment${ENV_FILE:+ or ${ENV_FILE}});"
  echo "!! skipping the NeMo CR. Create the secret (key: api-key) or set ${KEY_VAR}, re-run."
fi

if [[ "$AUDIO_ON" == 1 ]]; then
  echo "   audio model url: ${AUDIO_BASE_URL} (model ${AUDIO_MODEL_NAME})"
  if ! ensure_secret "$AUDIO_KEY_SECRET" "$AUDIO_KEY_VAR"; then
    echo "!! No secret ${NS}/${AUDIO_KEY_SECRET} and ${AUDIO_KEY_VAR} is not set: audio is NOT deployed."
    AUDIO_ON=0
  fi
fi

echo "== guardrail configurations (ConfigMaps)"
RENDER_AUDIO=()
[[ "$AUDIO_ON" == 1 ]] && RENDER_AUDIO=(--audio-base-url "$AUDIO_BASE_URL" --audio-model "$AUDIO_MODEL_NAME")
"$PY" rhoai/render_configs.py --configmaps --namespace "$NS" \
  --base-url "$MODEL_BASE_URL" --model "$MODEL_NAME" ${RENDER_AUDIO[@]+"${RENDER_AUDIO[@]}"} | oc_ apply -f -

echo "== web UI (service account, build config, deployment, service, route)"
manifest rhoai/k8s/30-ui.yaml | oc_ apply -f -

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  echo "== build UI image on the cluster"
  STAGE="$(mktemp -d)"
  trap 'rm -rf "$STAGE"' EXIT
  mkdir -p "$STAGE/ui" "$STAGE/samples" "$STAGE/samples_audio"
  cp -R ui/mmdemo ui/static "$STAGE/ui/"
  find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
  cp samples/*.png "$STAGE/samples/"
  [[ "$AUDIO_ON" == 1 ]] && cp samples_audio/*.wav "$STAGE/samples_audio/"
  cp ui/Containerfile "$STAGE/Containerfile"
  for attempt in 1 2 3; do
    # Single-node clusters sometimes fail a build with "too many open files": retry.
    if oc_ -n "$NS" start-build mm-ui --from-dir="$STAGE" --follow; then break; fi
    [[ "$attempt" == 3 ]] && { echo "!! build failed 3 times"; exit 1; }
    echo "   build attempt ${attempt} failed, retrying"
  done
fi

if [[ "$HAVE_KEY" == 1 ]]; then
  echo "== NeMo Guardrails (TrustyAI operator)"
  EXISTED=0
  oc_ -n "$NS" get deploy/mm-guardrails >/dev/null 2>&1 && EXISTED=1
  manifest rhoai/k8s/20-nemo-guardrails.yaml | oc_ apply -f -
  # The operator creates the Deployment a moment after the CR; `rollout status` fails before that.
  for _ in $(seq 30); do oc_ -n "$NS" get deploy/mm-guardrails >/dev/null 2>&1 && break; sleep 2; done
  # NeMo reads its configuration at start-up, so a changed ConfigMap needs a restart.
  [[ "$EXISTED" == 1 ]] && oc_ -n "$NS" rollout restart deploy/mm-guardrails >/dev/null
  oc_ -n "$NS" rollout status deploy/mm-guardrails --timeout=300s
fi

oc_ -n "$NS" rollout restart deploy/mm-ui >/dev/null
oc_ -n "$NS" rollout status deploy/mm-ui --timeout=180s

HOST="$(oc_ -n "$NS" get route mm-ui -o jsonpath='{.spec.host}')"
echo "== done. UI: https://${HOST}/   (or: oc --context ${CTX} -n ${NS} port-forward svc/mm-ui 8080:8080)"
