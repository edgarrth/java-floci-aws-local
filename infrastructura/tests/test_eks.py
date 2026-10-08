"""Se verifica la importación de imágenes y el acceso a las herramientas del nodo k3s."""

import io
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from infrastructura import eks


class EksCommandTest(unittest.TestCase):
    def test_uses_kubectl_directly_with_the_node_kubeconfig(self):
        with patch.object(eks, "docker") as docker:
            eks.kube("get", "--raw=/readyz", capture_output=True, text=True)
        docker.assert_called_once_with(
            "exec",
            "-i",
            eks.CLUSTER_CONTAINER,
            "/bin/kubectl",
            "--kubeconfig",
            "/etc/rancher/k3s/k3s.yaml",
            "get",
            "--raw=/readyz",
            capture_output=True,
            text=True,
        )

    def test_uses_ctr_directly_with_the_k3s_socket_and_kubernetes_namespace(self):
        with patch.object(eks, "docker") as docker:
            eks.containerd("images", "list", "--quiet", capture_output=True, text=True)
        docker.assert_called_once_with(
            "exec",
            "-i",
            eks.CLUSTER_CONTAINER,
            "/bin/ctr",
            "--address",
            "/run/k3s/containerd/containerd.sock",
            "--namespace",
            "k8s.io",
            "images",
            "list",
            "--quiet",
            capture_output=True,
            text=True,
        )


class EksImageImportTest(unittest.TestCase):
    image = "axiz-payment-service:2.0.0"

    def archive(self, exit_code=0):
        process = Mock(stdout=io.BytesIO(b"contenido-del-archivo"))
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.wait.return_value = exit_code
        return process

    def test_streams_the_archive_and_verifies_its_canonical_reference(self):
        archive = self.archive()
        result = subprocess.CompletedProcess([], 0, stdout="docker.io/library/" + self.image + "\n")
        with (
            patch.object(eks.subprocess, "Popen", return_value=archive) as popen,
            patch.object(eks, "containerd", side_effect=[Mock(), result]) as containerd,
        ):
            eks.import_image(self.image)
        popen.assert_called_once_with(["docker", "save", self.image], stdout=subprocess.PIPE)
        self.assertEqual(containerd.call_args_list[0].args, ("images", "import", "--local", "-"))
        self.assertIs(containerd.call_args_list[0].kwargs["stdin"], archive.stdout)
        self.assertEqual(containerd.call_args_list[1].args, ("images", "list", "--quiet"))
        self.assertTrue(archive.stdout.closed)
        archive.wait.assert_called_once()

    def test_closes_the_pipe_and_propagates_an_import_failure(self):
        archive = self.archive()
        error = subprocess.CalledProcessError(3, ["ctr", "images", "import"])
        with (
            patch.object(eks.subprocess, "Popen", return_value=archive),
            patch.object(eks, "containerd", side_effect=error) as containerd,
            self.assertRaises(subprocess.CalledProcessError) as raised,
        ):
            eks.import_image(self.image)
        self.assertIs(raised.exception, error)
        self.assertTrue(archive.stdout.closed)
        self.assertEqual(containerd.call_count, 1)
        archive.__exit__.assert_called_once()

    def test_rejects_a_failed_export_and_a_missing_imported_reference(self):
        for exit_code, message in ((1, "exportar"), (0, "namespace k8s.io")):
            archive = self.archive(exit_code)
            with (
                self.subTest(exit_code=exit_code),
                patch.object(eks.subprocess, "Popen", return_value=archive),
                patch.object(
                    eks, "containerd", return_value=subprocess.CompletedProcess([], 0, stdout="")
                ) as containerd,
                self.assertRaisesRegex(RuntimeError, message),
            ):
                eks.import_image(self.image)
            self.assertTrue(archive.stdout.closed)
            self.assertEqual(containerd.call_count, 1 if exit_code else 2)


class EksServiceReadinessTest(unittest.TestCase):
    def endpoints(self, ready=True, terminating=False):
        return subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "endpoints": [
                                {
                                    "addresses": ["10.42.0.10"],
                                    "conditions": {"ready": ready, "terminating": terminating},
                                }
                            ]
                        }
                    ]
                }
            ),
        )

    def test_waits_for_ready_endpoints_and_three_complete_nodeport_checks(self):
        with (
            patch.object(eks, "kube", return_value=self.endpoints()) as kube,
            patch.object(
                eks.urllib.request,
                "urlopen",
                side_effect=lambda *a, **k: io.BytesIO(b'{"status":"UP"}'),
            ) as http,
            patch.object(eks.time, "sleep"),
        ):
            eks.wait_services_ready()
        self.assertEqual(kube.call_count, 6)
        self.assertEqual(http.call_count, 6)
        self.assertTrue(all("endpointslices" in c.args for c in kube.call_args_list))
        self.assertEqual(
            [c.args[0] for c in http.call_args_list],
            [
                f"http://{eks.CLUSTER_CONTAINER}:{port}/actuator/health/readiness"
                for _ in range(3)
                for port in (30080, 30081)
            ],
        )

    def test_ignores_terminating_endpoints_and_restarts_stability_after_a_tcp_cut(self):
        endpoints = [self.endpoints(terminating=True), *[self.endpoints() for _ in range(9)]]
        calls = [io.BytesIO(b'{"status":"UP"}'), ConnectionRefusedError(111, "Corte")]
        calls.extend(io.BytesIO(b'{"status":"UP"}') for _ in range(6))
        with (
            patch.object(eks, "kube", side_effect=endpoints) as kube,
            patch.object(eks.urllib.request, "urlopen", side_effect=calls) as http,
            patch.object(eks.time, "sleep"),
        ):
            eks.wait_services_ready()
        self.assertEqual(http.call_count, 8)
        self.assertEqual(kube.call_count, 9)

    def test_times_out_instead_of_accepting_a_rollout_without_ready_services(self):
        with (
            patch.object(eks.time, "monotonic", side_effect=[0, 0, 0, 1, 2]),
            patch.object(eks.time, "sleep"),
            patch.object(eks, "kube", return_value=self.endpoints(ready=False)),
            patch.object(eks.urllib.request, "urlopen") as http,
            self.assertRaisesRegex(TimeoutError, "endpoints listos"),
        ):
            eks.wait_services_ready(seconds=2)
        http.assert_not_called()

    def test_diagnostics_continue_when_one_query_fails(self):
        with patch.object(
            eks,
            "kube",
            side_effect=[
                subprocess.CalledProcessError(1, ["kubectl"]),
                None,
                None,
                None,
                subprocess.CompletedProcess([], 0, stdout='{"items":[]}'),
                subprocess.CompletedProcess([], 0, stdout='{"items":[]}'),
            ],
        ) as kube:
            with patch.object(eks, "docker", return_value=Mock(stdout="nodo-actual")):
                eks.diagnose_services()
        self.assertEqual(kube.call_count, 6)
        self.assertTrue(all(c.kwargs["timeout"] == 8 for c in kube.call_args_list))


class EksDeploymentFlowTest(unittest.TestCase):
    def run_deployment(self, readiness_error=None, seed_error=None, recovery_error=None):
        events = []
        manifests = []

        def kube(*args, **kwargs):
            if args[0] == "apply":
                manifests.extend(yaml.safe_load_all(kwargs["input"]))
            if "rollout" in args:
                events.append(args[4])

        def ready():
            events.append("services-ready")
            if readiness_error:
                raise readiness_error

        def recover():
            events.append("node-recovery")
            if recovery_error:
                raise recovery_error

        def run(args, **kwargs):
            events.append(args[1])
            if args[1] == "datasets/seed.py" and seed_error:
                raise seed_error

        with tempfile.TemporaryDirectory() as directory:
            runtime = pathlib.Path(directory)
            (runtime / "resources.json").write_text(
                json.dumps({"brokers": "floci-msk-test:9092", "dbPort": 5432, "gateway": "local"})
            )
            (runtime / "aws.properties").write_text("aws.region=us-east-1\n")
            (runtime / "rds-ca.crt").write_text("certificado-local\n")
            with (
                patch.object(eks, "RUNTIME", runtime),
                patch.object(eks, "ip", return_value="10.0.0.2"),
                patch.object(eks, "kube", side_effect=kube),
                patch.object(eks, "containerd"),
                patch.object(eks, "import_image"),
                patch.object(eks, "recover_local_nodes", side_effect=recover),
                patch.object(eks, "docker", return_value=Mock(stdout="sha256:imagen")),
                patch.object(eks, "wait_services_ready", side_effect=ready),
                patch.object(eks.subprocess, "run", side_effect=run),
                patch.object(
                    eks, "diagnose_services", side_effect=lambda: events.append("diagnosis")
                ),
            ):
                failure = recovery_error or readiness_error or seed_error
                if failure:
                    with self.assertRaises(type(failure)) as raised:
                        eks.deploy()
                    self.assertIs(raised.exception, failure)
                else:
                    eks.deploy()
        return events, manifests

    def test_waits_for_both_rollouts_and_service_routes_before_seed_and_catalogue(self):
        events, manifests = self.run_deployment()
        self.assertEqual(
            events,
            [
                "node-recovery",
                "deployment/axiz-payment",
                "deployment/axiz-settlement",
                "services-ready",
                "datasets/seed.py",
                "infrastructura/routes.py",
            ],
        )
        deployments = [item for item in manifests if item["kind"] == "Deployment"]
        self.assertEqual(len(deployments), 2)
        for deployment in deployments:
            self.assertEqual(deployment["spec"]["minReadySeconds"], 5)
            self.assertEqual(
                deployment["spec"]["strategy"]["rollingUpdate"],
                {"maxUnavailable": 0, "maxSurge": 1},
            )

    def test_collects_diagnostics_and_stops_before_seed_when_nodeport_never_recovers(self):
        events, _ = self.run_deployment(readiness_error=TimeoutError("Sin ruta disponible"))
        self.assertEqual(events[-2:], ["services-ready", "diagnosis"])
        self.assertNotIn("datasets/seed.py", events)
        self.assertNotIn("infrastructura/routes.py", events)

    def test_collects_diagnostics_and_does_not_print_routes_after_a_failed_seed(self):
        events, _ = self.run_deployment(
            seed_error=subprocess.CalledProcessError(1, ["python3", "datasets/seed.py"])
        )
        self.assertEqual(events[-2:], ["datasets/seed.py", "diagnosis"])
        self.assertNotIn("infrastructura/routes.py", events)

    def test_diagnoses_a_node_failure_before_applying_manifests_or_starting_rollout(self):
        events, manifests = self.run_deployment(
            recovery_error=RuntimeError("Runtime no verificado")
        )
        self.assertEqual(events, ["node-recovery", "diagnosis"])
        self.assertEqual(manifests, [])


if __name__ == "__main__":
    unittest.main()
