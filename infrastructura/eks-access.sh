#!/usr/bin/env bash
set -euo pipefail

# Se permite seleccionar destinos alternativos para comprobar el transporte TCP.
upstream=${1:-floci-eks-axiz-payments}
payment_port=${2:-30080}
settlement_port=${3:-30081}
pids=()

cleanup() {
  if ((${#pids[@]})); then
    kill "${pids[@]}" 2>/dev/null || true
    wait "${pids[@]}" 2>/dev/null || true
  fi
}
trap 'cleanup' EXIT
trap 'exit 0' INT TERM

# Se reenvían bytes sin modificar claves de acceso, cabeceras ni contenido HTTP.
socat -T 120 \
  TCP4-LISTEN:30080,bind=0.0.0.0,reuseaddr,fork,max-children=128 \
  "TCP4:${upstream}:${payment_port},connect-timeout=5" &
pids+=("$!")
socat -T 120 \
  TCP4-LISTEN:30081,bind=0.0.0.0,reuseaddr,fork,max-children=128 \
  "TCP4:${upstream}:${settlement_port},connect-timeout=5" &
pids+=("$!")

printf '%s\n' 'Se publican los accesos EKS: pagos http://localhost:30080 y liquidación http://localhost:30081.'
# Si termina un listener se detiene el otro para reiniciar el puente completo.
if wait -n "${pids[@]}"; then
  status=1
else
  status=$?
fi
cleanup
trap - EXIT
exit "$status"
