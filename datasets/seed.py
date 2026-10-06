#!/usr/bin/env python3
"""Se precargan pagos mediante la API con claves de idempotencia estables."""

import json
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
for _attempt in range(120):
    try:
        urllib.request.urlopen(base + "/actuator/health/readiness", timeout=2).close()
        break
    except OSError:
        time.sleep(2)
else:
    raise TimeoutError("El servicio de pagos no está disponible")
results = []
for entry in json.loads((ROOT / "datasets/payments.json").read_text()):
    entry = dict(entry)
    key = entry.pop("idempotencyKey")
    request = urllib.request.Request(
        base + "/api/payments",
        data=json.dumps(entry).encode(),
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": key,
            "X-Api-Key": (ROOT / ".runtime/api-key").read_text().strip(),
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
        if result["status"] != "ACCEPTED":
            raise RuntimeError("El pago de precarga no fue aceptado: " + result["id"])
        results.append(result)
(ROOT / ".runtime/dataset-results.json").write_text(json.dumps(results, indent=2))
print("Se precargaron " + str(len(results)) + " pagos aceptados.")
