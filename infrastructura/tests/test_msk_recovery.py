"""Se comprueba la recuperación del motor MSK sin sustituir su volumen de datos."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from infrastructura import bootstrap


class MskRecoveryTest(unittest.TestCase):
    cluster_arn = "arn:aws:kafka:us-east-1:000000000000:cluster/axiz-events/identificador"
    broker_name = "floci-msk-dfda92"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.metadata = {
            "clusterArn": self.cluster_arn,
            "clusterName": "axiz-events",
            "accountId": "000000000000",
            "resourceRegion": "us-east-1",
            "volumeId": "dfda92",
            "state": "ACTIVE",
            "bootstrapBrokers": "172.19.0.7:9092",
            "containerId": "contenedor-eliminado",
        }
        self.write_metadata()
        self.data_patch = patch.object(bootstrap, "FLOCI_DATA", self.path)
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        self.ready_patch = patch.object(bootstrap, "wait_msk_ready")
        self.ready = self.ready_patch.start()
        self.addCleanup(self.ready_patch.stop)

    def write_metadata(self):
        (self.path / "msk-clusters.json").write_text(
            json.dumps({"000000000000:" + self.cluster_arn: self.metadata})
        )

    def container(self, running, recovered=False):
        return {
            "Id": "broker-id",
            "Name": "/" + self.broker_name,
            "State": {"Running": running},
            "HostConfig": {"NetworkMode": "axiz-network"},
            "NetworkSettings": {
                "Networks": {
                    "axiz-network": {"NetworkID": "network-current", "IPAddress": "172.19.0.7"}
                }
            },
            "Config": {"Labels": {"pe.axiz.msk-recovered": "true"} if recovered else {}},
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": self.broker_name,
                    "Destination": "/var/lib/redpanda/data",
                }
            ],
        }

    def network(self):
        return [{"Name": "axiz-network", "Id": "network-current"}]

    def result(self, payload=""):
        return subprocess.CompletedProcess(
            [], 0, stdout=payload if isinstance(payload, str) else json.dumps(payload)
        )

    def emulator(self):
        return [
            {
                "Config": {
                    "Image": "floci/floci:2.1.0",
                    "Env": ["FLOCI_SERVICES_MSK_DEFAULT_IMAGE=redpandadata/redpanda:v26.2.3"],
                }
            }
        ]

    def test_reuses_an_active_broker_without_changing_its_runtime(self):
        with patch.object(
            bootstrap.subprocess,
            "run",
            side_effect=[
                self.result("broker-id\n"),
                self.result([self.container(True)]),
            ],
        ) as run:
            self.assertIsNone(bootstrap.restore_msk_runtime(self.cluster_arn))
        self.assertEqual(run.call_count, 2)
        self.ready.assert_not_called()

    def test_reconciles_a_running_broker_when_the_api_preserves_its_old_ip(self):
        container = self.container(True)
        container["NetworkSettings"] = {"Networks": {"axiz-network": {"IPAddress": "172.19.0.9"}}}
        with patch.object(
            bootstrap.subprocess,
            "run",
            side_effect=[
                self.result("broker-id\n"),
                self.result([container]),
                self.result([]),
            ],
        ) as run:
            self.assertEqual(
                bootstrap.restore_msk_runtime(self.cluster_arn, "172.19.0.7:9092"),
                self.broker_name,
            )
        self.assertEqual(run.call_count, 3)
        self.assertFalse(any("start" in call.args[0] for call in run.call_args_list))
        self.ready.assert_called_once_with(self.broker_name)

    def test_reuses_a_recovered_broker_after_its_ip_changes(self):
        with patch.object(
            bootstrap.subprocess,
            "run",
            side_effect=[
                self.result("broker-id\n"),
                self.result([self.container(True, recovered=True)]),
            ],
        ) as run:
            self.assertEqual(bootstrap.restore_msk_runtime(self.cluster_arn), self.broker_name)
        self.assertEqual(run.call_count, 2)
        self.ready.assert_called_once_with(self.broker_name)
        container = {
            **self.container(True, recovered=True),
            "NetworkSettings": {"Networks": {"axiz-network": {"IPAddress": "172.19.0.9"}}},
        }
        with patch.object(
            bootstrap.subprocess,
            "run",
            side_effect=[
                self.result("broker-id\n"),
                self.result([container]),
            ],
        ):
            self.assertEqual(
                bootstrap.msk_brokers(self.cluster_arn, "172.19.0.7:9092", self.broker_name),
                self.broker_name + ":9092",
            )

    def test_starts_a_stopped_container_with_the_original_volume(self):
        with patch.object(
            bootstrap.subprocess,
            "run",
            side_effect=[
                self.result("broker-id\n"),
                self.result([self.container(False)]),
                self.result([]),
                self.result(self.network()),
                self.result("broker-id\n"),
            ],
        ) as run:
            self.assertEqual(bootstrap.restore_msk_runtime(self.cluster_arn), self.broker_name)
        self.assertEqual(run.call_args_list[-1].args[0], ["docker", "start", self.broker_name])
        self.assertFalse(any("create" in call.args[0] for call in run.call_args_list))
        self.ready.assert_called_once_with(self.broker_name)

    def test_recreates_a_stopped_broker_when_the_network_name_has_a_new_identity(self):
        before = (self.path / "msk-clusters.json").read_bytes()
        container = self.container(False)
        container["NetworkSettings"]["Networks"]["axiz-network"]["NetworkID"] = "network-deleted"
        state = {"container": container}

        def daemon(command, **kwargs):
            self.assertTrue(kwargs["check"])
            if command[:2] == ["docker", "ps"]:
                return self.result("broker-id\n")
            if command == ["docker", "inspect", "broker-id"]:
                return self.result([state["container"]])
            if command == ["docker", "inspect", "axiz-floci"]:
                return self.result(self.emulator())
            if command == ["docker", "volume", "inspect", self.broker_name]:
                return self.result([{"Name": self.broker_name}])
            if command == ["docker", "network", "inspect", "axiz-network"]:
                return self.result(self.network())
            if command == ["docker", "rm", "broker-id"]:
                self.assertFalse(state["container"]["State"]["Running"])
                state["container"] = None
                return self.result(self.broker_name)
            if command[:2] == ["docker", "create"]:
                self.assertIsNone(state["container"])
                self.assertIn(
                    "type=volume,src="
                    + self.broker_name
                    + ",dst=/var/lib/redpanda/data,volume-nocopy",
                    command,
                )
                state["container"] = self.container(False, recovered=True)
                state["container"]["Id"] = "new-id"
                return self.result("new-id\n")
            if command == ["docker", "start", self.broker_name]:
                attached = state["container"]["NetworkSettings"]["Networks"]["axiz-network"]
                if attached["NetworkID"] != "network-current":
                    raise subprocess.CalledProcessError(
                        1,
                        command,
                        stderr="failed to set up container networking: network network-deleted not found",
                    )
                state["container"]["State"]["Running"] = True
                return self.result(self.broker_name)
            self.fail("Operación Docker inesperada: " + " ".join(command))

        with patch.object(bootstrap.subprocess, "run", side_effect=daemon) as run:
            self.assertEqual(bootstrap.restore_msk_runtime(self.cluster_arn), self.broker_name)
        self.assertTrue(state["container"]["State"]["Running"])
        commands = [call.args[0] for call in run.call_args_list]
        self.assertNotIn("network=axiz-network", commands[0])
        self.assertEqual(commands[3], ["docker", "network", "inspect", "axiz-network"])
        self.assertEqual(commands[5], ["docker", "rm", "broker-id"])
        self.assertNotIn("--force", commands[5])
        self.assertNotIn("--volumes", commands[5])
        self.assertIn(
            "type=volume,src=" + self.broker_name + ",dst=/var/lib/redpanda/data,volume-nocopy",
            commands[6],
        )
        self.assertEqual(commands[7], ["docker", "start", self.broker_name])
        self.assertEqual((self.path / "msk-clusters.json").read_bytes(), before)
        self.ready.assert_called_once_with(self.broker_name)

    def test_recreates_a_stopped_broker_if_its_network_mode_retains_an_old_id(self):
        container = self.container(False)
        container["HostConfig"]["NetworkMode"] = "network-deleted"
        with patch.object(
            bootstrap.subprocess,
            "run",
            side_effect=[
                self.result("broker-id\n"),
                self.result([container]),
                self.result([]),
                self.result(self.network()),
                self.result(self.emulator()),
                self.result(),
                self.result(),
                self.result(),
            ],
        ) as run:
            self.assertEqual(bootstrap.restore_msk_runtime(self.cluster_arn), self.broker_name)
        self.assertEqual(run.call_args_list[5].args[0], ["docker", "rm", "broker-id"])

    def test_preserves_an_active_broker_on_an_unexpected_network(self):
        container = self.container(True)
        container["NetworkSettings"]["Networks"] = {"otra-red": {"NetworkID": "other"}}
        with (
            patch.object(
                bootstrap.subprocess,
                "run",
                side_effect=[self.result("broker-id\n"), self.result([container])],
            ) as run,
            self.assertRaisesRegex(RuntimeError, "activo no pertenece"),
        ):
            bootstrap.restore_msk_runtime(self.cluster_arn)
        self.assertEqual(run.call_count, 2)
        self.ready.assert_not_called()

    def test_preserves_the_stopped_container_if_the_current_network_is_unavailable(self):
        with (
            patch.object(
                bootstrap.subprocess,
                "run",
                side_effect=[
                    self.result("broker-id\n"),
                    self.result([self.container(False)]),
                    self.result([]),
                    subprocess.CalledProcessError(
                        1, ["docker", "network", "inspect"], stderr="network axiz-network not found"
                    ),
                ],
            ) as run,
            self.assertRaisesRegex(RuntimeError, "network axiz-network not found"),
        ):
            bootstrap.restore_msk_runtime(self.cluster_arn)
        self.assertFalse(any("rm" in c.args[0] for c in run.call_args_list))

    def test_preserves_a_broker_if_the_image_cannot_be_verified_before_recreation(self):
        container = self.container(False)
        container["NetworkSettings"]["Networks"] = {}
        emulator = self.emulator()
        emulator[0]["Config"]["Image"] = "floci/floci:otra-version"
        with (
            patch.object(
                bootstrap.subprocess,
                "run",
                side_effect=[
                    self.result("broker-id\n"),
                    self.result([container]),
                    self.result([]),
                    self.result(self.network()),
                    self.result(emulator),
                ],
            ) as run,
            self.assertRaisesRegex(RuntimeError, "validada para Floci 2.1.0"),
        ):
            bootstrap.restore_msk_runtime(self.cluster_arn)
        self.assertFalse(any("rm" in c.args[0] for c in run.call_args_list))

    def test_does_not_force_removal_if_the_broker_starts_during_reconciliation(self):
        container = self.container(False)
        container["NetworkSettings"]["Networks"] = {}
        with (
            patch.object(
                bootstrap.subprocess,
                "run",
                side_effect=[
                    self.result("broker-id\n"),
                    self.result([container]),
                    self.result([]),
                    self.result(self.network()),
                    self.result(self.emulator()),
                    subprocess.CalledProcessError(
                        1, ["docker", "rm", "broker-id"], stderr="container is running"
                    ),
                ],
            ) as run,
            self.assertRaisesRegex(RuntimeError, "container is running"),
        ):
            bootstrap.restore_msk_runtime(self.cluster_arn)
        self.assertEqual(run.call_args_list[-1].args[0], ["docker", "rm", "broker-id"])
        self.assertFalse(any("create" in c.args[0] for c in run.call_args_list))

    def test_reports_start_failure_and_preserves_the_runtime_for_diagnosis(self):
        with (
            patch.object(
                bootstrap.subprocess,
                "run",
                side_effect=[
                    self.result("broker-id\n"),
                    self.result([self.container(False)]),
                    self.result([]),
                    self.result(self.network()),
                    subprocess.CalledProcessError(
                        1,
                        ["docker", "start", self.broker_name],
                        stderr="OCI runtime create failed: permiso denegado",
                    ),
                ],
            ) as run,
            self.assertRaisesRegex(RuntimeError, "OCI runtime create failed: permiso denegado"),
        ):
            bootstrap.restore_msk_runtime(self.cluster_arn)
        self.assertFalse(
            any("rm" in c.args[0] or "create" in c.args[0] for c in run.call_args_list)
        )

    def test_recreates_only_the_missing_container_and_reuses_the_retained_volume(self):
        before = (self.path / "msk-clusters.json").read_bytes()
        with patch.object(
            bootstrap.subprocess,
            "run",
            side_effect=[
                self.result(),
                self.result([]),
                self.result(self.emulator()),
                self.result("new-id\n"),
                self.result("new-id\n"),
            ],
        ) as run:
            self.assertEqual(bootstrap.restore_msk_runtime(self.cluster_arn), self.broker_name)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[1], ["docker", "volume", "inspect", self.broker_name])
        creation = commands[3]
        self.assertIn(
            "type=volume,src=" + self.broker_name + ",dst=/var/lib/redpanda/data,volume-nocopy",
            creation,
        )
        self.assertIn("pe.axiz.msk-recovered=true", creation)
        self.assertIn("io.floci.account=000000000000", creation)
        self.assertIn("io.floci.region=us-east-1", creation)
        self.assertIn("io.floci.resource-id=axiz-events", creation)
        self.assertIn("redpandadata/redpanda:v26.2.3", creation)
        self.assertEqual(creation[-2:], ["--advertise-kafka-addr", self.broker_name + ":9092"])
        self.assertEqual(commands[-1], ["docker", "start", self.broker_name])
        self.assertFalse(any("rm" in c or "prune" in c for c in commands))
        self.assertEqual((self.path / "msk-clusters.json").read_bytes(), before)
        self.ready.assert_called_once_with(self.broker_name)

    def test_refuses_recovery_if_the_retained_volume_is_missing(self):
        with (
            patch.object(
                bootstrap.subprocess,
                "run",
                side_effect=[
                    self.result(),
                    subprocess.CalledProcessError(1, ["docker", "volume", "inspect"]),
                ],
            ) as run,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            bootstrap.restore_msk_runtime(self.cluster_arn)
        self.assertEqual(run.call_count, 2)
        self.ready.assert_not_called()

    def test_refuses_unverifiable_resource_ownership_and_volume_identity(self):
        for field, value in (
            ("accountId", "999999999999"),
            ("resourceRegion", "us-west-2"),
            ("volumeId", "../../otro"),
            ("volumeId", None),
        ):
            original = self.metadata[field]
            self.metadata[field] = value
            self.write_metadata()
            with (
                self.subTest(field=field, value=value),
                patch.object(bootstrap.subprocess, "run", return_value=self.result()) as run,
                self.assertRaisesRegex(RuntimeError, "identidad del volumen"),
            ):
                bootstrap.restore_msk_runtime(self.cluster_arn)
            self.assertEqual(run.call_count, 1)
            self.metadata[field] = original
        self.ready.assert_not_called()

    def test_refuses_a_stopped_container_using_a_different_volume(self):
        container = self.container(False)
        container["Mounts"][0]["Name"] = "otro-volumen"
        with (
            patch.object(
                bootstrap.subprocess,
                "run",
                side_effect=[
                    self.result("broker-id\n"),
                    self.result([container]),
                    self.result([]),
                ],
            ) as run,
            self.assertRaisesRegex(RuntimeError, "volumen original"),
        ):
            bootstrap.restore_msk_runtime(self.cluster_arn)
        self.assertEqual(run.call_count, 3)
        self.ready.assert_not_called()

    def test_refuses_ambiguous_containers_and_absent_metadata(self):
        with patch.object(bootstrap.subprocess, "run", return_value=self.result("a\nb\n")) as run:
            with self.assertRaisesRegex(RuntimeError, "único contenedor"):
                bootstrap.restore_msk_runtime(self.cluster_arn)
            self.assertEqual(run.call_count, 1)
        (self.path / "msk-clusters.json").unlink()
        with patch.object(bootstrap.subprocess, "run", return_value=self.result()) as run:
            with self.assertRaisesRegex(RuntimeError, "metadatos persistidos"):
                bootstrap.restore_msk_runtime(self.cluster_arn)
            self.assertEqual(run.call_count, 1)

    def test_preserves_a_failed_runtime_for_diagnosis(self):
        self.ready.side_effect = TimeoutError("El motor MSK no está disponible")
        with (
            patch.object(
                bootstrap.subprocess,
                "run",
                side_effect=[
                    self.result("broker-id\n"),
                    self.result([self.container(False)]),
                    self.result([]),
                    self.result(self.network()),
                    self.result("broker-id\n"),
                ],
            ) as run,
            self.assertRaises(TimeoutError),
        ):
            bootstrap.restore_msk_runtime(self.cluster_arn)
        self.assertFalse(any("rm" in call.args[0] for call in run.call_args_list))


class MskReadinessTest(unittest.TestCase):
    def test_uses_the_redpanda_readiness_api_and_closes_the_response(self):
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(bootstrap.urllib.request, "urlopen", return_value=response) as urlopen:
            bootstrap.wait_msk_ready("floci-msk-dfda92")
        urlopen.assert_called_once_with("http://floci-msk-dfda92:9644/v1/status/ready", timeout=3)
        response.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
