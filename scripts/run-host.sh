#!/usr/bin/env bash
set -euo pipefail
[[ -f .runtime/resources.json ]] || { echo 'Se requiere completar el aprovisionamiento de infraestructura.' >&2; exit 1; }
[[ -f .env ]] || cp .env.example .env
set -a
# Se carga la configuración local provista por el operador.
# shellcheck source=.env.example
source .env
set +a
./mvnw -B -ntp package -DskipTests
export AWS_ENDPOINT_URL=http://localhost:4566
export DB_HOST=localhost DB_CA="$PWD/.runtime/rds-ca.crt"
export KAFKA_BOOTSTRAP_FILE="$PWD/.runtime/kafka-bootstrap"
export REDIS_HOST=localhost
export AWS_REGION="${AWS_REGION:-us-east-1}"
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; wait || true; }
trap cleanup EXIT INT TERM
for service in payment settlement; do
  AWS_ROLE_ARN="arn:aws:iam::000000000000:role/axiz-$service" \
  AWS_WEB_IDENTITY_TOKEN_FILE="$PWD/.runtime/$service.token" \
  DB_USER="axiz_$service" java -jar "$service-service/target/$service-service-2.0.0.jar" &
  pids+=("$!")
done
bash datasets/seed.sh
bash scripts/routes.sh
wait -n "${pids[@]}"
