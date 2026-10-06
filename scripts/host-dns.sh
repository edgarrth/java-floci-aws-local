#!/usr/bin/env bash
set -euo pipefail
# Se registra únicamente el nombre anunciado por Kafka en la red de Docker local.
[[ "$EUID" -eq 0 ]] || { echo 'Se requiere sudo para actualizar /etc/hosts.' >&2; exit 1; }
broker=$(python3 -c 'import json; print(json.load(open(".runtime/resources.json"))["brokers"].rsplit(":",1)[0])')
address=$(docker inspect "$broker" --format '{{(index .NetworkSettings.Networks "axiz-network").IPAddress}}')
[[ -n "$address" ]] || { echo 'No se encontró la dirección del broker.' >&2; exit 1; }
if ! getent hosts "$broker" >/dev/null; then printf '%s %s # axiz-kafka-local\n' "$address" "$broker" >> /etc/hosts; fi
