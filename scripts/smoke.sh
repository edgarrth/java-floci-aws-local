#!/usr/bin/env bash
set -euo pipefail
payment=${PAYMENT_URL:-http://localhost:8080}
acquirer=${ACQUIRER_URL_TEST:-http://localhost:8081}
settlement=${SETTLEMENT_URL:-http://localhost:8082}
api_key=$(cat .runtime/api-key)
internal=$(cat .runtime/internal-key)
run=$(date +%s%N)
request='{"merchantId":"merchant-demo","amount":25.50,"currency":"PEN","token":"tok_smoke"}'
json_field() { python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
fault() { curl -fsS -X PUT "$acquirer/internal/faults" -H "X-Internal-Key: $internal" -H 'Content-Type: application/json' -d "$1" >/dev/null; }
reset() { curl -fsS -X POST "$payment/internal/resilience/reset" -H "X-Internal-Key: $internal" >/dev/null; }
pay() { curl -fsS -X POST "$payment/api/payments" -H "X-Api-Key: $api_key" -H 'Content-Type: application/json' -H "Idempotency-Key: $1" -d "$request"; }
trap 'fault '\''{"mode":"SUCCESS","failures":0,"delayMs":0}'\''; reset' EXIT
fault '{"mode":"SUCCESS","failures":0,"delayMs":0}'
reset
first=$(pay "smoke-$run-success")
id=$(printf '%s' "$first" | json_field id)
[[ $(printf '%s' "$first" | json_field status) == AUTHORIZED ]]
[[ $(pay "smoke-$run-success" | json_field id) == "$id" ]]
code=$(curl -sS -o .runtime/conflict.json -w '%{http_code}' -X POST "$payment/api/payments" -H "X-Api-Key: $api_key" -H 'Content-Type: application/json' -H "Idempotency-Key: smoke-$run-success" -d '{"merchantId":"merchant-demo","amount":26,"currency":"PEN","token":"tok_smoke"}')
[[ "$code" == 409 ]]
echo 'Se comprobó autorización, replay y conflicto de idempotencia.'
fault '{"mode":"TRANSIENT","failures":2,"delayMs":0}'
reset
[[ $(pay "smoke-$run-retry" | json_field status) == AUTHORIZED ]]
calls=$(curl -fsS "$acquirer/internal/faults" -H "X-Internal-Key: $internal" | json_field calls)
[[ "$calls" == 3 ]]
echo 'Se comprobó la recuperación tras dos fallos transitorios.'
fault '{"mode":"TIMEOUT_AFTER_COMMIT","failures":0,"delayMs":2000}'
reset
unknown=$(pay "smoke-$run-unknown")
[[ $(printf '%s' "$unknown" | json_field status) == UNKNOWN ]]
unknown_id=$(printf '%s' "$unknown" | json_field id)
fault '{"mode":"SUCCESS","failures":0,"delayMs":0}'
reset
[[ $(curl -fsS -X POST "$payment/api/payments/$unknown_id/reconcile" -H "X-Api-Key: $api_key" | json_field status) == AUTHORIZED ]]
echo 'Se comprobó la conciliación de una autorización cuya respuesta se perdió.'
fault '{"mode":"DECLINE","failures":0,"delayMs":0}'
reset
[[ $(pay "smoke-$run-decline" | json_field status) == DECLINED ]]
fault '{"mode":"TRANSIENT","failures":100,"delayMs":0}'
reset
pay "smoke-$run-open1" >/dev/null
pay "smoke-$run-open2" >/dev/null
[[ $(curl -fsS "$payment/internal/resilience" -H "X-Internal-Key: $internal" | json_field state) == OPEN ]]
echo 'Se comprobó la apertura del circuit breaker.'
fault '{"mode":"SUCCESS","failures":0,"delayMs":0}'
reset
ready=false
for _attempt in $(seq 1 45); do
  if curl -fsS "$settlement/api/settlements/$id" -H "X-Api-Key: $api_key" > .runtime/settlement-smoke.json; then ready=true; break; fi
  sleep 1
done
[[ "$ready" == true ]]
curl -fsS "$settlement/api/settlements/$id/ledger" -H "X-Api-Key: $api_key" | python3 -c 'import json,sys,decimal; rows=json.load(sys.stdin); assert len(rows)==2; assert sum(decimal.Decimal(str(r.get("amount",r.get("AMOUNT")))) for r in rows)==0'
echo 'Se comprobó liquidación, proyección y balance contable.'
code=$(curl -sS -o /dev/null -w '%{http_code}' "$acquirer/internal/faults")
[[ "$code" == 401 ]]
echo 'Se comprobó el rechazo del acceso interno sin credenciales.'
bash scripts/routes.sh
