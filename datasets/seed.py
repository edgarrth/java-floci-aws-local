#!/usr/bin/env python3
"""Se precargan pagos idempotentes incluso durante cambios de endpoints Kubernetes."""

import http.client
import json
import os
import pathlib
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


def request_json(request, operation, *, seconds=120, attempt_seconds=15, initial_delay=1):
    """Se repite exactamente el request ante un fallo transitorio, con espera limitada."""
    post = isinstance(request, urllib.request.Request) and request.get_method() == "POST"
    if post and not request.get_header("Idempotency-key"):
        raise ValueError("Se requiere Idempotency-Key para repetir un POST de precarga")
    deadline = time.monotonic() + seconds
    delay = initial_delay
    attempts = 0
    last_error = "sin respuesta"
    while (remaining := deadline - time.monotonic()) > 0:
        attempts += 1
        try:
            with urllib.request.urlopen(
                request, timeout=min(attempt_seconds, remaining)
            ) as response:
                result = json.load(response)
            if attempts > 1:
                print(f"Se recuperó {operation} después de {attempts} intentos.", flush=True)
            return result
        except urllib.error.HTTPError as error:
            with error:
                if error.code not in RETRYABLE_STATUS:
                    detail = error.read(4096).decode(errors="replace").replace("\n", " ")
                    raise RuntimeError(f"Falló {operation}: HTTP {error.code}; {detail}") from error
            last_error = "HTTP " + str(error.code)
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise RuntimeError(
                    f"Falló la verificación del certificado en {operation}"
                ) from error
            last_error = str(error.reason)
        except ssl.SSLCertVerificationError as error:
            raise RuntimeError(f"Falló la verificación del certificado en {operation}") from error
        except (OSError, http.client.HTTPException) as error:
            last_error = str(error)
        except ValueError as error:
            raise RuntimeError(f"Se recibió JSON inválido en {operation}") from error
        if attempts == 1:
            detail = " Se conserva la clave de idempotencia del POST." if post else ""
            print(
                f"Se espera la recuperación de {operation}: {last_error}.{detail}",
                flush=True,
            )
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(delay, remaining))
        delay = min(delay * 2, 5)
    raise TimeoutError(
        f"No se completó {operation} en {seconds}s ({attempts} intentos): {last_error}"
    )


def save_results(path, results):
    """Se publica el resultado completo de forma atómica sin destruir la precarga anterior."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".dataset-", delete=False
        ) as output:
            temporary = pathlib.Path(output.name)
            json.dump(results, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load(base, root=ROOT):
    base = base.rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Se requiere una URL HTTP o HTTPS válida para pagos")
    health = request_json(
        base + "/actuator/health/readiness", "readiness de pagos", seconds=240, attempt_seconds=2
    )
    if not isinstance(health, dict) or health.get("status") != "UP":
        raise RuntimeError("Readiness de pagos no devolvió el estado UP")
    api_key = (root / ".runtime/api-key").read_text().strip()
    entries = json.loads((root / "datasets/payments.json").read_text())
    results = []
    for original in entries:
        entry = dict(original)
        key = entry.pop("idempotencyKey")
        # Se construye una sola vez: un timeout puede ocurrir después de confirmar el pago.
        request = urllib.request.Request(
            base + "/api/payments",
            data=json.dumps(entry).encode(),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": key,
                "X-Api-Key": api_key,
            },
        )
        result = request_json(request, "precarga " + key)
        if (
            not isinstance(result, dict)
            or not result.get("id")
            or result.get("status") != "ACCEPTED"
        ):
            raise RuntimeError("No se confirmó como ACCEPTED el pago de precarga " + key)
        results.append(result)
    save_results(root / ".runtime/dataset-results.json", results)
    print(f"Se precargaron {len(results)} pagos aceptados.", flush=True)
    return results


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
    try:
        load(base)
    except (OSError, RuntimeError, ValueError) as error:
        print("Falló la precarga de pagos: " + str(error), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
