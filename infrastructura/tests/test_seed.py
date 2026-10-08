"""Se prueba la precarga con cortes después de readiness y respuestas perdidas tras el commit."""

import collections
import contextlib
import http.client
import io
import json
import socket
import ssl
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from datasets import seed


class SeedRetryTest(unittest.TestCase):
    def request(self):
        return urllib.request.Request(
            "http://localhost:30080/api/payments",
            data=b'{"amount":25.5}',
            headers={"Idempotency-Key": "dataset-payment-001"},
        )

    def test_repeats_the_same_post_after_refusal_timeout_and_transient_http(self):
        request = self.request()
        failures = [
            urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
            TimeoutError("Se agotó el tiempo de respuesta"),
            http.client.RemoteDisconnected("Se perdió la respuesta"),
            urllib.error.HTTPError(request.full_url, 503, "No disponible", {}, io.BytesIO(b"{}")),
        ]
        with (
            patch.object(
                seed.urllib.request,
                "urlopen",
                side_effect=[*failures, io.BytesIO(b'{"id":"pago-1","status":"ACCEPTED"}')],
            ) as send,
            patch.object(seed.time, "sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            result = seed.request_json(request, "precarga")
        self.assertEqual(result["id"], "pago-1")
        self.assertEqual(send.call_count, 5)
        self.assertTrue(all(call.args[0] is request for call in send.call_args_list))
        self.assertEqual(request.get_header("Idempotency-key"), "dataset-payment-001")
        self.assertEqual(request.data, b'{"amount":25.5}')

    def test_does_not_retry_permanent_http_failures_or_certificate_validation(self):
        for code in (400, 401, 403, 404, 409, 422):
            error = urllib.error.HTTPError(
                self.request().full_url, code, "Error permanente", {}, io.BytesIO(b"{}")
            )
            with (
                self.subTest(code=code),
                patch.object(seed.urllib.request, "urlopen", side_effect=error) as send,
                self.assertRaisesRegex(RuntimeError, "HTTP " + str(code)),
            ):
                seed.request_json(self.request(), "precarga")
            self.assertEqual(send.call_count, 1)
        certificate = ssl.SSLCertVerificationError("Certificado no confiable")
        for error in (certificate, urllib.error.URLError(certificate)):
            with (
                self.subTest(error=type(error).__name__),
                patch.object(seed.urllib.request, "urlopen", side_effect=error) as send,
                self.assertRaisesRegex(RuntimeError, "certificado"),
            ):
                seed.request_json(self.request(), "precarga")
            self.assertEqual(send.call_count, 1)

    def test_stops_at_the_deadline_and_limits_each_request_timeout(self):
        with (
            patch.object(seed.time, "monotonic", side_effect=[0, 0, 1, 2, 3, 4]),
            patch.object(seed.time, "sleep") as sleep,
            patch.object(
                seed.urllib.request, "urlopen", side_effect=ConnectionRefusedError(111, "Corte")
            ) as send,
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(TimeoutError, "4s.*2 intentos"),
        ):
            seed.request_json(self.request(), "precarga", seconds=4)
        self.assertEqual([c.kwargs["timeout"] for c in send.call_args_list], [4, 2])
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 1])

    def test_refuses_a_mutation_without_an_idempotency_key(self):
        with self.assertRaisesRegex(ValueError, "Idempotency-Key"):
            seed.request_json(urllib.request.Request("http://localhost/pagos", data=b"{}"), "pago")

    def test_preserves_previous_results_when_a_later_payment_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".runtime").mkdir()
            (root / "datasets").mkdir()
            (root / ".runtime/api-key").write_text("clave-prueba")
            previous = root / ".runtime/dataset-results.json"
            previous.write_text('[{"id":"pago-anterior"}]')
            (root / "datasets/payments.json").write_text(
                json.dumps([{"idempotencyKey": "uno"}, {"idempotencyKey": "dos"}])
            )
            with (
                patch.object(
                    seed,
                    "request_json",
                    side_effect=[
                        {"status": "UP"},
                        {"id": "uno", "status": "ACCEPTED"},
                        RuntimeError("Fallo"),
                    ],
                ),
                self.assertRaisesRegex(RuntimeError, "Fallo"),
            ):
                seed.load("http://localhost:30080", root)
            self.assertEqual(json.loads(previous.read_text()), [{"id": "pago-anterior"}])


class LostResponseTest(unittest.TestCase):
    def test_recovers_a_committed_payment_without_creating_a_second_order(self):
        orders = {}
        requests = collections.Counter()

        class Handler(BaseHTTPRequestHandler):
            def reply(self, value):
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self.reply({"status": "UP"})

            def do_POST(self):
                key = self.headers["Idempotency-Key"]
                body = self.rfile.read(int(self.headers["Content-Length"]))
                requests[key] += 1
                if key not in orders:
                    orders[key] = (body, {"id": "pago-" + key, "status": "ACCEPTED"})
                elif orders[key][0] != body:
                    self.send_error(409)
                    return
                if requests[key] == 1:
                    # Se confirma la orden y se corta TCP antes de devolver su respuesta.
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.reply(orders[key][1])

            def log_message(self, _format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / ".runtime").mkdir()
                (root / "datasets").mkdir()
                (root / ".runtime/api-key").write_text("clave-prueba")
                (root / "datasets/payments.json").write_bytes(
                    (seed.ROOT / "datasets/payments.json").read_bytes()
                )
                with patch.object(seed.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
                    results = seed.load(f"http://127.0.0.1:{server.server_port}", root)
                self.assertEqual(len(results), 3)
                self.assertEqual(len(orders), 3)
                self.assertEqual(list(requests.values()), [2, 2, 2])
                self.assertEqual(
                    json.loads((root / ".runtime/dataset-results.json").read_text()), results
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
