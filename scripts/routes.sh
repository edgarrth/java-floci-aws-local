#!/usr/bin/env bash
set -euo pipefail
python3 infrastructura/routes.py "${PAYMENT_URL:-http://localhost:8080}" "${ACQUIRER_URL_TEST:-http://localhost:8081}" "${SETTLEMENT_URL:-http://localhost:8082}"
