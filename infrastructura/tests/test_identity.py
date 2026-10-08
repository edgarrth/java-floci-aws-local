"""Se comprueban diagnósticos TLS del cliente y el aislamiento de identidades."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg

from infrastructura import check_identity, verify_aws


class IdentityCheckTest(unittest.TestCase):
    def identity(self, service="payment", mode="verify-full", backend_tls=False):
        return {
            "awsRole": f"arn:aws:sts::000000000000:assumed-role/axiz-{service}/sesion",
            "database": {
                "username": "axiz_" + service,
                "schema": service,
                "sslMode": mode,
                "backendTls": backend_tls,
            },
        }

    def test_accepts_verified_client_connections_with_a_plaintext_backend(self):
        for service in ("payment", "settlement"):
            with self.subTest(service=service):
                check_identity.validate(self.identity(service), service)

    def test_rejects_tls_modes_without_full_certificate_and_hostname_validation(self):
        for mode in (None, "disable", "allow", "prefer", "require", "verify-ca"):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "TLS"):
                check_identity.validate(self.identity(mode=mode), "payment")

    def test_rejects_a_different_role_user_or_schema(self):
        for field in ("role", "username", "schema"):
            value = self.identity()
            if field == "role":
                value["awsRole"] = self.identity("settlement")["awsRole"]
            else:
                value["database"][field] = "otro"
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_identity.validate(value, "payment")

    def test_cli_reports_a_tls_failure_without_an_opaque_traceback(self):
        script = Path(check_identity.__file__).resolve()
        for mode, code in (("verify-full", 0), ("prefer", 1)):
            with self.subTest(mode=mode):
                result = subprocess.run(
                    [sys.executable, str(script), "payment"],
                    input=json.dumps(self.identity(mode=mode)),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, code)
                self.assertNotIn("Traceback", result.stderr)
                if code:
                    self.assertIn("TLS", result.stderr)


class DatabaseVerificationTest(unittest.TestCase):
    def connection(self, service, client_tls=True, backend_tls=False, cross_access=False):
        connection = Mock()
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=False)
        connection.pgconn.ssl_in_use = client_tls
        cursor = connection.cursor.return_value
        cursor.__enter__ = Mock(return_value=cursor)
        cursor.__exit__ = Mock(return_value=False)
        cursor.fetchone.return_value = ("axiz_" + service, service, backend_tls)
        cursor.execute.side_effect = [
            None,
            None,
            None if cross_access else psycopg.errors.InsufficientPrivilege("Acceso denegado"),
        ]
        return connection, cursor

    def test_measures_client_tls_and_selects_the_schema_after_authentication(self):
        for service in ("payment", "settlement"):
            connection, cursor = self.connection(service)
            with (
                self.subTest(service=service),
                patch.object(verify_aws, "scoped") as scoped,
                patch.object(verify_aws.psycopg, "connect", return_value=connection) as connect,
            ):
                scoped.return_value.generate_db_auth_token.return_value = "token-temporal"
                verify_aws.verify_database(service, {}, {"dbPort": 7001})
            self.assertEqual(connect.call_args.kwargs["sslmode"], "verify-full")
            self.assertTrue(connect.call_args.kwargs["sslrootcert"].endswith("rds-ca.crt"))
            self.assertNotIn("options", connect.call_args.kwargs)
            self.assertEqual(
                cursor.execute.call_args_list[0].args[0].as_string(),
                'SET search_path TO "' + service + '"',
            )
            connection.rollback.assert_called_once()

    def test_rejects_a_plaintext_client_even_if_the_backend_reports_tls(self):
        connection, _cursor = self.connection("payment", client_tls=False, backend_tls=True)
        with (
            patch.object(verify_aws, "scoped"),
            patch.object(verify_aws.psycopg, "connect", return_value=connection),
            self.assertRaisesRegex(AssertionError, "cliente no utiliza TLS"),
        ):
            verify_aws.verify_database("payment", {}, {"dbPort": 7001})

    def test_rejects_access_to_the_other_service_schema(self):
        connection, _cursor = self.connection("payment", cross_access=True)
        with (
            patch.object(verify_aws, "scoped"),
            patch.object(verify_aws.psycopg, "connect", return_value=connection),
            self.assertRaisesRegex(AssertionError, "otro servicio"),
        ):
            verify_aws.verify_database("payment", {}, {"dbPort": 7001})


if __name__ == "__main__":
    unittest.main()
