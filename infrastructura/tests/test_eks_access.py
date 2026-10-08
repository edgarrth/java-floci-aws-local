"""Se comprueba el puente EKS con socat y servidores HTTP reales."""

import concurrent.futures
import http.server
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import unittest
import urllib.request
from pathlib import Path


class Backend(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {
                "service": self.server.service,
                "path": self.path,
                "apiKey": self.headers.get("X-Api-Key"),
                "correlationId": self.headers.get("X-Correlation-Id"),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(201)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Internal-Key", self.headers.get("X-Internal-Key", ""))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *args):
        pass


@unittest.skipUnless(
    shutil.which("socat"), "Se requiere socat; se incluye en la imagen de herramientas."
)
class EksAccessTest(unittest.TestCase):
    def setUp(self):
        for port in (30080, 30081):
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    self.skipTest(
                        f"Se requiere el puerto local {port} libre para comprobar el puente."
                    )
        self.process = None
        self.backends = []
        self.addCleanup(self.cleanup)
        for service in ("pagos", "liquidación"):
            backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
            backend.service = service
            thread = threading.Thread(target=backend.serve_forever, daemon=True)
            thread.start()
            self.backends.append((backend, thread))
        self.start_bridge()

    def start_bridge(self):
        script = Path(__file__).resolve().parents[1] / "eks-access.sh"
        self.process = subprocess.Popen(
            ["bash", str(script), "127.0.0.1", *[str(b.server_port) for b, _t in self.backends]],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 5
        for port in (30080, 30081):
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=0.2):
                        break
                except OSError:
                    if self.process.poll() is not None or time.monotonic() >= deadline:
                        self.fail("No se iniciaron los listeners del puente EKS.")
                    time.sleep(0.02)

    def stop_bridge(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.communicate(timeout=5)
            code = self.process.returncode
            self.process = None
            return code
        return None

    def cleanup(self):
        try:
            self.stop_bridge()
        finally:
            for backend, thread in self.backends:
                backend.shutdown()
                backend.server_close()
                thread.join(timeout=2)

    def assert_ports_closed(self):
        for port in (30080, 30081):
            with self.subTest(port=port), socket.socket() as probe:
                probe.settimeout(0.5)
                self.assertNotEqual(probe.connect_ex(("127.0.0.1", port)), 0)

    def test_routes_concurrent_requests_and_preserves_authentication(self):
        def request(index):
            port = 30080 + index % 2
            path = f"/api/payments/{index}?detalle=true"
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                headers={"X-Api-Key": "clave-prueba", "X-Correlation-Id": str(index)},
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                body = json.load(response)
            self.assertEqual(body["service"], ("pagos", "liquidación")[index % 2])
            self.assertEqual(body["path"], path)
            self.assertEqual(body["apiKey"], "clave-prueba")
            self.assertEqual(body["correlationId"], str(index))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(request, range(16)))

    def test_preserves_binary_bodies_status_and_response_headers(self):
        body = os.urandom(512 * 1024)
        for port in (30080, 30081):
            with self.subTest(port=port):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/payments",
                    data=body,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "X-Internal-Key": "interna",
                    },
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    self.assertEqual(response.status, 201)
                    self.assertEqual(response.headers["X-Internal-Key"], "interna")
                    self.assertEqual(response.read(), body)

    def test_stops_both_listeners_on_sigterm(self):
        self.assertEqual(self.stop_bridge(), 0)
        self.assert_ports_closed()

    def test_propagates_a_bind_failure_and_stops_the_other_listener(self):
        self.stop_bridge()
        script = Path(__file__).resolve().parents[1] / "eks-access.sh"
        with socket.socket() as occupied:
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            occupied.bind(("0.0.0.0", 30080))
            occupied.listen()
            self.process = subprocess.Popen(
                ["bash", str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            _stdout, stderr = self.process.communicate(timeout=5)
            self.assertNotEqual(self.process.returncode, 0)
            self.assertIn("Address already in use", stderr)
        self.assert_ports_closed()


if __name__ == "__main__":
    unittest.main()
