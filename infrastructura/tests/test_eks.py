"""Se verifica la importación de imágenes y el acceso a las herramientas del nodo k3s."""

import io
import subprocess
import unittest
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()
