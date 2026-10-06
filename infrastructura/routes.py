#!/usr/bin/env python3
"""Se imprime el catálogo de rutas de cada servicio una vez finalizado el arranque."""

import json
import sys
import time
import urllib.request

for index, base in enumerate(sys.argv[1:]):
    for _attempt in range(90):
        try:
            with urllib.request.urlopen(base + "/routes", timeout=3) as response:
                routes = json.load(response)
            break
        except OSError:
            time.sleep(2)
    else:
        raise TimeoutError("No se pudo obtener el catálogo de " + base)
    print("Servicio: " + ["pagos", "liquidación"][index])
    for route in routes:
        print("  " + route)
    print("  GET /actuator/health/liveness")
    print("  GET /actuator/health/readiness")
    print("  GET /actuator/prometheus")
print("API Gateway: http://axiz-payments.execute-api.localhost.floci.io:4566")
