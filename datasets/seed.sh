#!/usr/bin/env bash
set -euo pipefail
python3 datasets/seed.py "${1:-http://localhost:8080}" "${2:-http://localhost:8081}"
