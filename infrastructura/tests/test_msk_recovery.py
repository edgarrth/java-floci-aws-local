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
            "Name": "/" + self.broker_name,
            "State": {"Running": running},
            "Config": {"Labels": {"pe.axiz.msk-recovered": "true"} if recovered else {}},
            "Mounts": [
                {
                    "Type": "volume",
                    "Name": self.broker_name,
                    "Destination": "/var/lib/redpanda/data",
                }
            ],
        }

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
                self.result("broker-id\n"),
            ],
        ) as run:
            self.assertEqual(bootstrap.restore_msk_runtime(self.cluster_arn), self.broker_name)
        self.assertEqual(run.call_args_list[-1].args[0], ["docker", "start", self.broker_name])
        self.assertFalse(any("create" in call.args[0] for call in run.call_args_list))
        self.ready.assert_called_once_with(self.broker_name)

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
