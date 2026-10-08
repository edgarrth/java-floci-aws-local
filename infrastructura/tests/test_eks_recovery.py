"""Se comprueba la recuperación de k3s después de recrear su contenedor con el volumen retenido."""

import copy
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from infrastructura import bootstrap, eks


class LocalNodeRecoveryTest(unittest.TestCase):
    current_name = "d4f6b44ddc26"
    old_names = ("2a517880b377", "1cd268c4efeb")

    def runtime(self):
        return {
            "Name": "/" + eks.CLUSTER_CONTAINER,
            "Id": self.current_name + "0" * 52,
            "State": {"Running": True},
            "Config": {
                "Hostname": self.current_name,
                "Image": "rancher/k3s:v1.35.9-k3s1",
                "Cmd": ["server", "--disable=traefik", "--tls-san=localhost"],
                "Env": ["K3S_KUBECONFIG_MODE=644"],
                "Labels": {
                    "io.floci.service": "eks",
                    "io.floci.resource-id": bootstrap.CLUSTER,
                    "io.floci.account": "000000000000",
                    "io.floci.region": bootstrap.REGION,
                },
            },
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": eks.CLUSTER_CONTAINER,
                    "Destination": "/var/lib/rancher/k3s",
                }
            ],
        }

    def node(self, name):
        return {
            "metadata": {
                "name": name,
                "uid": "uid-" + name,
                "labels": {
                    "kubernetes.io/hostname": name,
                    "node.kubernetes.io/instance-type": "k3s",
                },
            },
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }

    def result(self, items):
        return subprocess.CompletedProcess([], 0, stdout=json.dumps({"items": items}))

    def test_accepts_a_container_whose_image_is_recorded_as_an_id(self):
        image_id = "sha256:" + "5" * 64
        container = self.runtime()
        container["Config"]["Image"] = image_id
        tags = subprocess.CompletedProcess([], 0, stdout='["rancher/k3s:v1.35.9-k3s1"]')
        with patch.object(eks, "docker", return_value=tags) as docker:
            self.assertIs(eks.local_runtime([container]), container)
        self.assertEqual(docker.call_args.args[:3], ("image", "inspect", image_id))
        tags.stdout = '["postgres:18.6"]'
        with patch.object(eks, "docker", return_value=tags):
            with self.assertRaises(RuntimeError):
                eks.local_runtime([container])

    def test_reconciles_the_two_ghost_nodes_from_the_log_then_waits_for_pod_gc(self):
        nodes = [self.node(n) for n in (self.current_name, *self.old_names)]
        ghosts = [{"spec": {"nodeName": n}} for n in self.old_names]
        with (
            patch.object(eks, "runtime_inventory", return_value=[self.runtime()]),
            patch.object(
                eks,
                "kube",
                side_effect=[
                    None,
                    None,
                    self.result(nodes),
                    None,
                    None,
                    self.result(ghosts),
                    self.result([]),
                ],
            ) as kube,
            patch.object(eks.time, "sleep") as sleep,
        ):
            self.assertEqual(eks.recover_local_nodes(), self.current_name)
        deletes = [c for c in kube.call_args_list if c.args[0] == "delete"]
        self.assertEqual(len(deletes), 2)
        for call, name in zip(deletes, self.old_names, strict=True):
            self.assertEqual(call.args, ("delete", "--raw=/api/v1/nodes/" + name, "-f", "-"))
            self.assertEqual(
                json.loads(call.kwargs["input"])["preconditions"], {"uid": "uid-" + name}
            )
        self.assertEqual(sleep.call_count, 1)
        self.assertFalse(any("--force" in c.args for c in kube.call_args_list))

    def test_leaves_an_existing_current_node_and_its_pods_untouched(self):
        with (
            patch.object(eks, "runtime_inventory", return_value=[self.runtime()]),
            patch.object(
                eks, "kube", side_effect=[None, None, self.result([self.node(self.current_name)])]
            ) as kube,
        ):
            eks.recover_local_nodes()
        self.assertEqual(kube.call_count, 3)

    def test_refuses_an_unverified_cluster_owner_volume_or_node_name(self):
        changes = [
            ("labels", "io.floci.service", "msk"),
            ("labels", "io.floci.resource-id", "otro-cluster"),
            ("labels", "io.floci.account", "999999999999"),
            ("labels", "io.floci.region", "otra-region"),
            ("volume", "Name", "otro-volumen"),
            ("volume", "Type", "bind"),
            ("volume", "Destination", "/otra-ruta"),
            ("config", "Hostname", "nodo-personalizado"),
            ("config", "Env", ["K3S_NODE_NAME=otro-nodo"]),
            ("config", "Cmd", ["server", "--node-name=otro-nodo"]),
            ("state", "Running", False),
        ]
        for section, key, value in changes:
            runtime = self.runtime()
            target = {
                "labels": runtime["Config"]["Labels"],
                "volume": runtime["Mounts"][0],
                "config": runtime["Config"],
                "state": runtime["State"],
            }[section]
            target[key] = value
            with (
                self.subTest(section=section, key=key),
                patch.object(eks, "runtime_inventory", return_value=[runtime]),
                patch.object(eks, "kube") as kube,
                self.assertRaisesRegex(RuntimeError, "identidad"),
            ):
                eks.recover_local_nodes()
            kube.assert_not_called()

    def test_preserves_an_old_node_with_an_existing_running_or_stopped_container(self):
        for running in (True, False):
            old = {
                "Id": self.old_names[0] + "0" * 52,
                "Config": {"Hostname": self.old_names[0]},
                "State": {"Running": running},
            }
            with (
                self.subTest(running=running),
                patch.object(eks, "runtime_inventory", return_value=[self.runtime(), old]),
                patch.object(
                    eks,
                    "kube",
                    side_effect=[
                        None,
                        None,
                        self.result([self.node(self.current_name), self.node(self.old_names[0])]),
                    ],
                ) as kube,
                self.assertRaisesRegex(RuntimeError, "contenedor existente"),
            ):
                eks.recover_local_nodes()
            self.assertFalse(any(c.args[0] == "delete" for c in kube.call_args_list))

    def test_preserves_an_old_node_if_its_container_appears_during_reconciliation(self):
        old = {"Id": self.old_names[0] + "0" * 52, "Config": {"Hostname": self.old_names[0]}}
        with (
            patch.object(
                eks, "runtime_inventory", side_effect=[[self.runtime()], [self.runtime(), old]]
            ),
            patch.object(
                eks,
                "kube",
                side_effect=[
                    None,
                    None,
                    self.result([self.node(self.current_name), self.node(self.old_names[0])]),
                ],
            ) as kube,
            self.assertRaisesRegex(RuntimeError, "contenedor existente"),
        ):
            eks.recover_local_nodes()
        self.assertEqual(kube.call_count, 3)

    def test_stops_if_the_current_container_changes_before_deletion(self):
        replacement = copy.deepcopy(self.runtime())
        replacement["Id"] = self.current_name + "1" * 52
        with (
            patch.object(eks, "runtime_inventory", side_effect=[[self.runtime()], [replacement]]),
            patch.object(
                eks,
                "kube",
                side_effect=[
                    None,
                    None,
                    self.result([self.node(self.current_name), self.node(self.old_names[0])]),
                ],
            ) as kube,
            self.assertRaisesRegex(RuntimeError, "cambió"),
        ):
            eks.recover_local_nodes()
        self.assertEqual(kube.call_count, 3)

    def test_preserves_nodes_outside_the_single_node_docker_model(self):
        for name, instance in (("worker-real", "k3s"), (self.old_names[0], "m5.large")):
            node = self.node(name)
            node["metadata"]["labels"]["node.kubernetes.io/instance-type"] = instance
            with (
                self.subTest(name=name),
                patch.object(eks, "runtime_inventory", return_value=[self.runtime()]),
                patch.object(
                    eks,
                    "kube",
                    side_effect=[None, None, self.result([self.node(self.current_name), node])],
                ) as kube,
                self.assertRaisesRegex(RuntimeError, "ajeno"),
            ):
                eks.recover_local_nodes()
            self.assertEqual(kube.call_count, 3)

    def test_does_not_delete_any_node_if_the_new_node_is_not_ready(self):
        error = subprocess.CalledProcessError(1, ["kubectl", "wait"])
        with (
            patch.object(eks, "runtime_inventory", return_value=[self.runtime()]),
            patch.object(eks, "kube", side_effect=[None, error]) as kube,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            eks.recover_local_nodes()
        self.assertEqual(kube.call_count, 2)

    def test_fails_if_pod_gc_does_not_complete_without_forcing_pods_or_finalizers(self):
        ghosts = [{"spec": {"nodeName": self.old_names[0]}}]
        with (
            patch.object(eks, "runtime_inventory", return_value=[self.runtime()]),
            patch.object(
                eks,
                "kube",
                side_effect=[
                    None,
                    None,
                    self.result([self.node(self.current_name), self.node(self.old_names[0])]),
                    None,
                    self.result(ghosts),
                ],
            ) as kube,
            patch.object(eks.time, "monotonic", side_effect=[0, 0, 0, 2]),
            patch.object(eks.time, "sleep"),
            self.assertRaisesRegex(TimeoutError, "PodGC"),
        ):
            eks.recover_local_nodes(seconds=2)
        self.assertFalse(any("patch" in c.args or "--force" in c.args for c in kube.call_args_list))


class CurrentPodDiagnosticsTest(unittest.TestCase):
    def test_reads_logs_from_the_current_pods_instead_of_old_ready_or_terminating_pods(self):
        queries = []

        def kube(*args, **kwargs):
            queries.append(args)
            if "-l" in args:
                service = args[args.index("-l") + 1].removeprefix("app=axiz-")

                def pod(suffix, node, terminating=False):
                    return {
                        "metadata": {
                            "name": service + "-" + suffix,
                            **({"deletionTimestamp": "2026-10-08"} if terminating else {}),
                        },
                        "spec": {"nodeName": node},
                    }

                return subprocess.CompletedProcess(
                    [],
                    0,
                    stdout=json.dumps(
                        {
                            "items": [
                                pod("viejo", "2a517880b377"),
                                pod("terminando", "d4f6b44ddc26", True),
                                pod("actual", "d4f6b44ddc26"),
                            ]
                        }
                    ),
                )

        with (
            patch.object(eks, "kube", side_effect=kube),
            patch.object(eks, "docker", return_value=Mock(stdout="d4f6b44ddc26\n")),
        ):
            eks.diagnose_services()
        logs = [args for args in queries if "logs" in args]
        self.assertEqual(
            [args[3] for args in logs], ["pod/payment-actual", "pod/settlement-actual"]
        )


if __name__ == "__main__":
    unittest.main()
