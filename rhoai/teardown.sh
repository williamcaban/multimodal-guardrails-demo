#!/usr/bin/env bash
# Remove the demo: delete the NemoGuardrails CR, then the namespace that holds everything else.
#
# The CR goes first on purpose. The TrustyAI operator also creates one cluster-scoped object for
# it (ClusterRoleBinding <cr>-<namespace>-auth-delegator) and removes it from a finalizer on the
# CR; deleting the namespace first can leave the binding behind or hang if the operator is down.
# deploy.sh itself creates nothing outside the namespace.
#
# WARNING: this deletes the whole namespace, including anything else you put in it. Do not point
# NAMESPACE at a namespace you share with other work.
#
# Environment overrides: OC_CONTEXT (default: your current oc context) NAMESPACE
set -euo pipefail

CTX="${OC_CONTEXT:-$(oc config current-context)}"
NS="${NAMESPACE:-mm-guardrails-demo}"
CR="mm-guardrails"
CRB="${CR}-${NS}-auth-delegator"

oc_() { oc --context "$CTX" "$@"; }

echo "This deletes the NemoGuardrails '${CR}' and namespace '${NS}' on context '${CTX}'"
echo "(NeMo, UI, image build, routes and the API key secret in it)."
if [[ "${1:-}" != "--yes" ]]; then
  echo "Re-run with --yes to proceed."
  exit 1
fi

echo "== NemoGuardrails CR (lets the operator clean up what it created)"
oc_ -n "$NS" delete nemoguardrails "$CR" --ignore-not-found --timeout=120s \
  || echo "!! the CR did not finish deleting; continuing, but check the cluster-scoped binding below"

echo "== namespace"
oc_ delete namespace "$NS" --wait=false --ignore-not-found

if oc_ get clusterrolebinding "$CRB" >/dev/null 2>&1; then
  echo "!! ClusterRoleBinding ${CRB} still exists. If it stays after the CR is gone, remove it:"
  echo "!!   oc --context ${CTX} delete clusterrolebinding ${CRB}"
else
  echo "== no cluster-scoped leftovers (${CRB} is gone)"
fi
echo "The namespace deletes in the background: oc --context ${CTX} get namespace ${NS}"
echo "Not revoked by this script: the model API key itself. Revoke it where you created it."
