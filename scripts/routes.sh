#!/usr/bin/env bash
set -euo pipefail
python3 infrastructura/routes.py "${PAYMENT_URL:-http://localhost:8080}" "${SETTLEMENT_URL:-http://localhost:8081}"
