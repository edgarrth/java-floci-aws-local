#!/usr/bin/env bash
set -euo pipefail
payment=${PAYMENT_URL:-http://localhost:8080}
settlement=${SETTLEMENT_URL:-http://localhost:8081}
for base in "$payment" "$settlement"; do
  deadline=$((SECONDS + 30))
  until curl -fsS --connect-timeout 2 --max-time 2 "$base/actuator/health/readiness" >/dev/null 2>&1; do
    if ((SECONDS >= deadline)); then
      printf 'No se pudo acceder a readiness en %s. Se comprueba el arranque y, en EKS, el estado healthy de eks-access.\n' "$base" >&2
      exit 1
    fi
    sleep 1
  done
done
api_key=$(cat .runtime/api-key)
internal=$(cat .runtime/internal-key)
run=$(date +%s%N)
request='{"merchantId":"merchant-demo","amount":25.50,"currency":"PEN","token":"tok_smoke"}'
json_field() { python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
pay() { curl -fsS -X POST "$payment/api/payments" -H "X-Api-Key: $api_key" -H 'Content-Type: application/json' -H "Idempotency-Key: $1" -d "$2"; }
first=$(pay "smoke-$run-accepted" "$request")
id=$(printf '%s' "$first" | json_field id)
[[ $(printf '%s' "$first" | json_field status) == ACCEPTED ]]
[[ $(pay "smoke-$run-accepted" "$request" | json_field id) == "$id" ]]
code=$(curl -sS -o .runtime/conflict.json -w '%{http_code}' -X POST "$payment/api/payments" -H "X-Api-Key: $api_key" -H 'Content-Type: application/json' -H "Idempotency-Key: smoke-$run-accepted" -d '{"merchantId":"merchant-demo","amount":26,"currency":"PEN","token":"tok_smoke"}')
[[ "$code" == 409 ]]
echo 'Se comprobó registro, replay y conflicto de idempotencia.'
rejected=$(pay "smoke-$run-rejected" '{"merchantId":"merchant-demo","amount":1000000.00,"currency":"PEN","token":"tok_smoke"}')
rejected_id=$(printf '%s' "$rejected" | json_field id)
[[ $(printf '%s' "$rejected" | json_field status) == REJECTED ]]
echo 'Se comprobó el rechazo persistido por límite del comercio.'
for pid in "$id" "$rejected_id"; do
  ready=false
  for _attempt in $(seq 1 90); do
    for projection in "settlements/$pid" "settlements/$pid/audit"; do
      output=.runtime/settlement-smoke.json
      [[ "$projection" != */audit ]] || output=.runtime/audit-smoke.json
      code=$(curl -sS --connect-timeout 2 --max-time 5 -o "$output" -w '%{http_code}' "$settlement/api/$projection" -H "X-Api-Key: $api_key")
      if [[ "$code" == 404 ]]; then
        break
      elif [[ "$code" != 200 ]]; then
        printf 'Falló la consulta asíncrona %s: HTTP %s.\n' "$projection" "$code" >&2
        cat "$output" >&2
        exit 1
      fi
    done
    if [[ "$code" == 200 ]]; then ready=true; break; fi
    sleep 1
  done
  if [[ "$ready" != true ]]; then
    printf 'No se materializaron comprobante y auditoría del pago %s dentro de la espera.\n' "$pid" >&2
    exit 1
  fi
done
curl -fsS "$settlement/api/settlements/$id/ledger" -H "X-Api-Key: $api_key" | python3 -c 'import json,sys,decimal; rows=json.load(sys.stdin); assert len(rows)==2; assert sum(decimal.Decimal(str(r["amount"])) for r in rows)==0'
curl -fsS "$settlement/api/settlements/$rejected_id/ledger" -H "X-Api-Key: $api_key" | python3 -c 'import json,sys; assert json.load(sys.stdin)==[]'
echo 'Se comprobó SNS/SQS, liquidación, auditoría MSK y balance contable.'
for service in payment settlement; do
  base=$payment
  [[ "$service" == payment ]] || base=$settlement
  curl -fsS "$base/internal/platform" -H "X-Internal-Key: $internal" | python3 infrastructura/check_identity.py "$service"
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$base/internal/platform")
  [[ "$code" == 401 ]]
done
echo 'Se comprobó rol asumido, TLS PostgreSQL y rechazo de acceso interno anónimo.'
[[ $(curl -sS -o /dev/null -w '%{http_code}' "$payment/api/payments/$id") == 401 ]]
gateway=${GATEWAY_URL:-http://localhost:4566}
curl -fsS "$gateway/api/payments/$id" -H 'Host: axiz-payments.execute-api.localhost.floci.io' -H "X-Api-Key: $api_key" | python3 -c "import json,sys; assert json.load(sys.stdin)['id']=='$id'"
echo 'Se comprobó el proxy de API Gateway con autenticación y parámetros de ruta.'
bash scripts/routes.sh
