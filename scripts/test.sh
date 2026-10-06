#!/usr/bin/env bash
set -euo pipefail
./mvnw -B -ntp verify
python3 -m compileall -q infrastructura datasets
for script in scripts/*.sh datasets/*.sh; do bash -n "$script"; done
if [[ "${1:-unit}" == "all" ]]; then
  bash scripts/smoke.sh
  docker compose -f infrastructura/compose.yml run --rm --no-deps bootstrap python3 infrastructura/verify_aws.py
fi
