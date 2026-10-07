"""Se comprueba que una creación lenta o con respuesta perdida no se duplica."""

import contextlib
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError

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


if __name__ == "__main__":
    unittest.main()
