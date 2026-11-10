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
# Environment overrides: OC_CONTEXT (default: your current oc context) NAMESPACE MODEL_NAME
#                        MODEL_BASE_URL MAAS_KEY_VAR ENV_FILE
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
PY=".venv/bin/python"

oc_() { oc --context "$CTX" --request-timeout=60s "$@"; }
manifest() { sed "s/__NAMESPACE__/${NS}/g" "$1"; }

read_key() {
  if [[ -n "${!KEY_VAR:-}" ]]; then
    printf '%s' "${!KEY_VAR}"
  elif [[ -n "$ENV_FILE" && -f "$ENV_FILE" ]]; then
    # Read just this one variable; the file also holds unrelated credentials.
    { grep -E "^${KEY_VAR}=" "$ENV_FILE" || true; } | tail -1 | cut -d= -f2- \
      | sed -e "s/^[\"']//" -e "s/[\"']\$//"
  fi
}

echo "== context=${CTX} namespace=${NS} model=${MODEL_NAME}"
echo "   model url: ${MODEL_BASE_URL}"

echo "== namespace"
manifest rhoai/k8s/00-namespace.yaml | oc_ apply -f -

echo "== guardrail configurations (ConfigMaps)"
"$PY" rhoai/render_configs.py --configmaps --namespace "$NS" \
  --base-url "$MODEL_BASE_URL" --model "$MODEL_NAME" | oc_ apply -f -

HAVE_KEY=0
if oc_ -n "$NS" get secret "$KEY_SECRET" -o jsonpath='{.data.api-key}' 2>/dev/null | grep -q .; then
  echo "== model API key: using existing secret ${NS}/${KEY_SECRET} (value not shown)"
  HAVE_KEY=1
else
  KEY="$(read_key)"
  if [[ -n "$KEY" ]]; then
    echo "== model API key secret ${KEY_SECRET} (value not shown)"
    oc_ -n "$NS" create secret generic "$KEY_SECRET" \
      --from-file=api-key=<(printf '%s' "$KEY") --dry-run=client -o yaml | oc_ apply -f -
    HAVE_KEY=1
  else
    echo "!! No secret ${NS}/${KEY_SECRET} and ${KEY_VAR} is not set (environment${ENV_FILE:+ or ${ENV_FILE}});"
    echo "!! skipping the NeMo CR. Create the secret (key: api-key) or set ${KEY_VAR}, re-run."
  fi
fi
unset KEY 2>/dev/null || true

echo "== web UI (service account, build config, deployment, service, route)"
manifest rhoai/k8s/30-ui.yaml | oc_ apply -f -

if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  echo "== build UI image on the cluster"
  STAGE="$(mktemp -d)"
  trap 'rm -rf "$STAGE"' EXIT
  mkdir -p "$STAGE/ui" "$STAGE/samples"
  cp -R ui/mmdemo ui/static "$STAGE/ui/"
  find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} +
  cp samples/*.png "$STAGE/samples/"
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
