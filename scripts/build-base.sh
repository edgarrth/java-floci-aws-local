#!/usr/bin/env bash
set -euo pipefail
mkdir -p .runtime
[[ -f .env ]] || cp .env.example .env
docker compose -f infrastructura/compose.yml --profile build build java-base bootstrap payment settlement
