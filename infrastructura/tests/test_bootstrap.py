"""Se comprueba que una creación lenta o con respuesta perdida no se duplica."""

import contextlib
import json
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.stub import Stubber

from infrastructura import bootstrap


def aws_error(code):
    return ClientError({"Error": {"Code": code, "Message": "Respuesta de prueba"}}, "Prueba")


class BootstrapTest(unittest.TestCase):
    def test_reconciles_already_exists_while_creation_is_pending(self):
        resource = {"Status": "available", "Port": 7001}
        describe = Mock(
            side_effect=[
                aws_error("DBClusterNotFoundFault"),
                aws_error("DBClusterNotFoundFault"),
                resource,
            ]
        )
        create = Mock(side_effect=aws_error("DBClusterAlreadyExistsFault"))
        with patch.object(bootstrap.time, "sleep"):
            result = bootstrap.ensure(describe, create, {"DBClusterNotFoundFault"})
        self.assertEqual(result, resource)
        create.assert_called_once_with()

    def test_reconciles_a_lost_reply_using_list_without_recreating(self):
        resource = {"ClusterArn": "arn:aws:kafka:us-east-1:000000000000:cluster/axiz-events"}
        describe = Mock(side_effect=[None, None, resource])
        create = Mock(side_effect=ReadTimeoutError(endpoint_url="http://floci:4566"))
        with patch.object(bootstrap.time, "sleep"):
            self.assertEqual(bootstrap.ensure(describe, create, set()), resource)
        create.assert_called_once_with()

    def test_does_not_hide_permission_errors(self):
        for describe, create in (
            (Mock(side_effect=aws_error("AccessDenied")), Mock()),
            (Mock(return_value=None), Mock(side_effect=aws_error("AccessDenied"))),
            (
                Mock(side_effect=[None, aws_error("AccessDenied")]),
                Mock(side_effect=aws_error("DBClusterAlreadyExistsFault")),
            ),
        ):
            with self.subTest(describe=describe), self.assertRaises(ClientError) as raised:
                bootstrap.ensure(describe, create, set())
            self.assertEqual(raised.exception.response["Error"]["Code"], "AccessDenied")

    def test_limits_reconciliation_without_repeating_creation(self):
        describe = Mock(return_value=None)
        create = Mock(side_effect=ReadTimeoutError(endpoint_url="http://floci:4566"))
        with (
            patch.object(
                bootstrap.time, "monotonic", side_effect=[0, 0, bootstrap.WAIT_TIMEOUT + 1]
            ),
            patch.object(bootstrap.time, "sleep"),
            self.assertRaises(TimeoutError),
        ):
            bootstrap.ensure(describe, create, set())
        create.assert_called_once_with()

    def test_fails_immediately_for_a_failed_cluster(self):
        with self.assertRaisesRegex(RuntimeError, "FAILED"):
            bootstrap.ready({"status": "FAILED"}, "status", "ACTIVE", "EKS")


@contextlib.contextmanager
def rds_endpoint():
    state = {"created": False, "creations": 0}
    namespace = 'xmlns="http://rds.amazonaws.com/doc/2014-10-31/"'
    resource = (
        "<DBCluster><DBClusterIdentifier>axiz-ledger</DBClusterIdentifier>"
        "<Status>available</Status><Port>7001</Port></DBCluster>"
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            params = urllib.parse.parse_qs(
                self.rfile.read(int(self.headers["Content-Length"])).decode()
            )
            action = params["Action"][0]
            status = 200
            if action == "CreateDBCluster":
                state["created"] = True
                state["creations"] += 1
                time.sleep(0.15)
                body = (
                    f"<CreateDBClusterResponse {namespace}><CreateDBClusterResult>"
                    f"{resource}</CreateDBClusterResult></CreateDBClusterResponse>"
                )
            elif action == "DescribeDBClusters" and state["created"]:
                body = (
                    f"<DescribeDBClustersResponse {namespace}><DescribeDBClustersResult>"
                    f"<DBClusters>{resource}</DBClusters>"
                    "</DescribeDBClustersResult></DescribeDBClustersResponse>"
                )
            elif action == "DescribeDBClusters":
                status = 400
                body = (
                    f"<ErrorResponse {namespace}><Error><Type>Sender</Type>"
                    "<Code>DBClusterNotFoundFault</Code><Message>Se espera la creación</Message>"
                    "</Error><RequestId>prueba</RequestId></ErrorResponse>"
                )
            else:
                self.send_error(400)
                return
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/xml; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class ControlPlaneWireTest(unittest.TestCase):
    def exercise_creation(self, read_timeout):
        with rds_endpoint() as (endpoint, state):
            with contextlib.closing(
                boto3.client(
                    "rds",
                    region_name="us-east-1",
                    endpoint_url=endpoint,
                    aws_access_key_id="floci",
                    aws_secret_access_key="floci",
                    config=bootstrap.CONFIG.merge(Config(read_timeout=read_timeout)),
                )
            ) as rds:
                result = bootstrap.ensure(
                    lambda: rds.describe_db_clusters(DBClusterIdentifier="axiz-ledger")[
                        "DBClusters"
                    ][0],
                    lambda: rds.create_db_cluster(
                        DBClusterIdentifier="axiz-ledger", Engine="aurora-postgresql"
                    )["DBCluster"],
                    {"DBClusterNotFoundFault"},
                )
                self.assertEqual(result["Status"], "available")
                self.assertEqual(state["creations"], 1)
                self.assertEqual(rds.meta.config.retries["total_max_attempts"], 1)

    def test_slow_response_completes_with_one_sdk_request(self):
        self.exercise_creation(1)

    def test_lost_response_is_reconciled_with_real_sdk_without_duplicate_request(self):
        self.exercise_creation(0.05)


class ManagedCacheTest(unittest.TestCase):
    def exercise_cache(self, existing):
        group = {
            "ReplicationGroupId": "axiz-policies",
            "Status": "available",
            "NodeGroups": [
                {"NodeGroupId": "0001", "PrimaryEndpoint": {"Address": "floci", "Port": 6379}}
            ],
        }
        describe_params = {"ReplicationGroupId": "axiz-policies"}
        with contextlib.closing(
            boto3.client(
                "elasticache",
                region_name="us-east-1",
                endpoint_url="http://localhost:4566",
                aws_access_key_id="floci",
                aws_secret_access_key="floci",
                config=bootstrap.CONFIG,
            )
        ) as cache:
            with Stubber(cache) as stubber:
                if existing:
                    stubber.add_response(
                        "describe_replication_groups",
                        {"ReplicationGroups": [group]},
                        describe_params,
                    )
                else:
                    stubber.add_client_error(
                        "describe_replication_groups",
                        "ReplicationGroupNotFoundFault",
                        expected_params=describe_params,
                    )
                    stubber.add_response(
                        "create_replication_group",
                        {"ReplicationGroup": {**group, "Status": "creating"}},
                        {
                            **describe_params,
                            "ReplicationGroupDescription": "Caché de políticas de comercios Axiz",
                            "Engine": "redis",
                            "CacheNodeType": "cache.t3.micro",
                            "NumCacheClusters": 1,
                            "AutomaticFailoverEnabled": False,
                            "MultiAZEnabled": False,
                            "Port": 6379,
                        },
                    )
                stubber.add_response(
                    "describe_replication_groups",
                    {"ReplicationGroups": [group]},
                    describe_params,
                )
                with patch.object(bootstrap, "client", return_value=cache):
                    self.assertEqual(bootstrap.managed_cache(), group)
                stubber.assert_no_pending_responses()

    def test_creates_redis_with_the_replication_group_api(self):
        self.exercise_cache(False)

    def test_reuses_an_existing_replication_group(self):
        self.exercise_cache(True)


class ManagedBrokerTest(unittest.TestCase):
    cluster_arn = "arn:aws:kafka:us-east-1:000000000000:cluster/axiz-events/identificador"

    def container(self, name="floci-msk-dfda92", address="172.20.0.8", running=True):
        return {
            "Name": "/" + name,
            "State": {"Running": running},
            "NetworkSettings": {"Networks": {"axiz-network": {"IPAddress": address}}},
        }

    def command_results(self, containers):
        return [
            subprocess.CompletedProcess([], 0, stdout="broker-id\n"),
            subprocess.CompletedProcess([], 0, stdout=json.dumps(containers)),
        ]

    def test_resolves_random_and_legacy_names_scoped_to_resource_identity(self):
        for name in ("floci-msk-dfda92", "floci-msk-axiz-events"):
            with (
                self.subTest(name=name),
                patch.object(
                    bootstrap.subprocess,
                    "run",
                    side_effect=self.command_results([self.container(name)]),
                ) as run,
            ):
                self.assertEqual(
                    bootstrap.msk_brokers(self.cluster_arn, "172.20.0.8:9092"), name + ":9092"
                )
                command = run.call_args_list[0].args[0]
                for expected in (
                    "label=io.floci=aws",
                    "label=io.floci.service=msk",
                    "label=io.floci.resource-id=axiz-events",
                    "label=io.floci.account=000000000000",
                    "label=io.floci.region=us-east-1",
                    "network=axiz-network",
                ):
                    self.assertIn(expected, command)

    def test_uses_the_api_endpoint_to_disambiguate_surviving_containers(self):
        containers = [self.container("floci-msk-old", "172.20.0.7"), self.container()]
        with patch.object(
            bootstrap.subprocess, "run", side_effect=self.command_results(containers)
        ):
            self.assertEqual(
                bootstrap.msk_brokers(self.cluster_arn, "172.20.0.8:9092"),
                "floci-msk-dfda92:9092",
            )

    def test_accepts_the_container_dns_name_returned_by_the_api(self):
        with patch.object(
            bootstrap.subprocess, "run", side_effect=self.command_results([self.container()])
        ):
            self.assertEqual(
                bootstrap.msk_brokers(self.cluster_arn, "floci-msk-dfda92:9092"),
                "floci-msk-dfda92:9092",
            )

    def test_rejects_missing_mismatched_stopped_and_ambiguous_brokers(self):
        with (
            patch.object(
                bootstrap.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, stdout=""),
            ) as run,
            self.assertRaisesRegex(RuntimeError, "en ejecución"),
        ):
            bootstrap.msk_brokers(self.cluster_arn, "172.20.0.8:9092")
        self.assertEqual(run.call_count, 1)
        for containers in (
            [self.container(address="172.20.0.9")],
            [self.container(running=False)],
            [self.container(), self.container("floci-msk-duplicate")],
        ):
            with (
                self.subTest(containers=containers),
                patch.object(
                    bootstrap.subprocess, "run", side_effect=self.command_results(containers)
                ),
                self.assertRaisesRegex(RuntimeError, "único contenedor"),
            ):
                bootstrap.msk_brokers(self.cluster_arn, "172.20.0.8:9092")

    def test_does_not_substitute_a_mock_endpoint_or_hide_docker_failures(self):
        with (
            patch.object(
                bootstrap.subprocess, "run", side_effect=self.command_results([self.container()])
            ),
            self.assertRaisesRegex(RuntimeError, "único contenedor"),
        ):
            bootstrap.msk_brokers(self.cluster_arn, "localhost:9092")
        with (
            patch.object(
                bootstrap.subprocess,
                "run",
                side_effect=subprocess.CalledProcessError(1, ["docker"]),
            ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            bootstrap.msk_brokers(self.cluster_arn, "172.20.0.8:9092")

    def test_reuses_the_sdk_cluster_and_persists_the_announced_dns_name(self):
        with (
            contextlib.closing(
                boto3.client(
                    "kafka",
                    region_name="us-east-1",
                    endpoint_url="http://localhost:4566",
                    aws_access_key_id="floci",
                    aws_secret_access_key="floci",
                    config=bootstrap.CONFIG,
                )
            ) as kafka,
            Stubber(kafka) as stubber,
            tempfile.TemporaryDirectory() as directory,
        ):
            cluster = {
                "ClusterArn": self.cluster_arn,
                "ClusterName": "axiz-events",
                "State": "ACTIVE",
            }
            params = {"ClusterArn": self.cluster_arn}
            stubber.add_response("list_clusters", {"ClusterInfoList": [cluster]}, {})
            stubber.add_response("describe_cluster", {"ClusterInfo": cluster}, params)
            stubber.add_response(
                "get_bootstrap_brokers", {"BootstrapBrokerString": "172.20.0.8:9092"}, params
            )
            with (
                patch.object(bootstrap, "client", return_value=kafka),
                patch.object(bootstrap, "managed_cache"),
                patch.object(bootstrap, "restore_msk_runtime", return_value=None),
                patch.object(bootstrap, "RUNTIME", Path(directory)),
                patch.object(
                    bootstrap.subprocess,
                    "run",
                    side_effect=self.command_results([self.container()]),
                ),
            ):
                result = bootstrap.managed_engines()
            self.assertEqual(result, "floci-msk-dfda92:9092")
            self.assertEqual((Path(directory) / "kafka-bootstrap").read_text(), result)
            stubber.assert_no_pending_responses()


if __name__ == "__main__":
    unittest.main()
