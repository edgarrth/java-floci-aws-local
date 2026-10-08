#!/usr/bin/env python3
"""Se comprueba la identidad del servicio y el modo TLS de su conexión abierta."""

import json
import sys


def validate(value, service):
    database = value.get("database", {})
    checks = {
        "rol del servicio": f":assumed-role/axiz-{service}/" in value.get("awsRole", ""),
        "usuario SQL": database.get("username") == "axiz_" + service,
        "esquema SQL": database.get("schema") == service,
        "TLS con certificado y hostname verificados": database.get("sslMode") == "verify-full",
    }
    failed = [name for name, valid in checks.items() if not valid]
    if failed:
        raise ValueError("No se comprobó " + ", ".join(failed))


def main():
    service = sys.argv[1]
    try:
        validate(json.load(sys.stdin), service)
    except (ValueError, TypeError, AttributeError) as error:
        print(f"Falló la comprobación de identidad de {service}: {error}", file=sys.stderr)
        return 1
    print(f"Se comprobó identidad y verify-full de {service}; backendTls se informa por separado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
