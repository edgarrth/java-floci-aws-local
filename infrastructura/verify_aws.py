#!/usr/bin/env python3
"""Se comprueban roles, permisos, autenticación IAM de Aurora y firmas de comprobantes."""

import base64
import json
import pathlib
import time
import uuid

import bootstrap
import boto3
import psycopg
from botocore.exceptions import ClientError

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROLE_PREFIX = "arn:aws:iam::000000000000:role/axiz-"


def assume(service, token_service=None):
    token = (ROOT / f".runtime/{token_service or service}.token").read_text()
    return bootstrap.client("sts").assume_role_with_web_identity(
        RoleArn=ROLE_PREFIX + service, RoleSessionName="verification", WebIdentityToken=token
    )["Credentials"]


def scoped(service, credentials):
    return boto3.client(
        service,
        endpoint_url=bootstrap.ENDPOINT,
        region_name=bootstrap.REGION,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


def denied(action):
    try:
        action()
    except ClientError as error:
        assert (
            "Denied" in error.response["Error"]["Code"]
            or error.response["Error"]["Code"] == "InvalidIdentityToken"
        ), error
    else:
        raise AssertionError("Se aceptó una operación fuera del rol autorizado")


def wait_receipt(payment_id):
    for _attempt in range(90):
        try:
            return bootstrap.client("s3").get_object(
                Bucket="axiz-receipts", Key="receipts/" + payment_id + ".json"
            )
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"NoSuchKey", "404"}:
                raise
            time.sleep(1)
    raise TimeoutError("No se publicó el comprobante " + payment_id)


def verify():
    payment, settlement = assume("payment"), assume("settlement")
    for service, credentials in [("payment", payment), ("settlement", settlement)]:
        assert (
            "assumed-role/axiz-" + service + "/"
            in scoped("sts", credentials).get_caller_identity()["Arn"]
        )
    denied(lambda: assume("settlement", "payment"))
    denied(lambda: scoped("dynamodb", payment).scan(TableName="axiz-payment-audit"))
    denied(
        lambda: scoped("ssm", settlement).get_parameter(Name="/axiz/merchants/merchant-demo/limit")
    )
    assert scoped("secretsmanager", payment).get_secret_value(SecretId="/axiz/api-key")[
        "SecretString"
    ]
    print("Se comprobó identidad web, sujeto de ServiceAccount y separación de permisos AWS.")

    resources = json.loads((ROOT / ".runtime/resources.json").read_text())
    host = "floci" if bootstrap.ENDPOINT.startswith("http://floci") else "localhost"
    for service, credentials in [("payment", payment), ("settlement", settlement)]:
        username = "axiz_" + service
        password = scoped("rds", credentials).generate_db_auth_token(
            DBHostname=host, Port=resources["dbPort"], DBUsername=username
        )
        with psycopg.connect(
            host=host,
            port=resources["dbPort"],
            dbname="axiz",
            user=username,
            password=password,
            sslmode="verify-full",
            sslrootcert=str(ROOT / ".runtime/rds-ca.crt"),
            connect_timeout=5,
            options="-c search_path=" + service,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT current_user,current_schema(),ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
                )
                result = cursor.fetchone()
                assert result == (username, service, True), result
                other = "settlement.inbox" if service == "payment" else "payment.payments"
                try:
                    cursor.execute("SELECT 1 FROM " + other)
                except psycopg.errors.InsufficientPrivilege:
                    connection.rollback()
                else:
                    raise AssertionError("Se permitió leer el esquema de otro servicio")
    print("Se comprobó Aurora con token IAM, TLS y separación de esquemas PostgreSQL.")

    sns, sqs = bootstrap.client("sns"), bootstrap.client("sqs")
    identifier = uuid.uuid4().hex
    queue = sqs.create_queue(
        QueueName="axiz-verification-" + identifier + ".fifo",
        Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "true"},
    )["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    topic = sns.create_topic(
        Name="axiz-verification-" + identifier + ".fifo",
        Attributes={"FifoTopic": "true", "ContentBasedDeduplication": "true"},
    )["TopicArn"]
    subscription = sns.subscribe(
        TopicArn=topic,
        Protocol="sqs",
        Endpoint=queue_arn,
        Attributes={"RawMessageDelivery": "true"},
    )["SubscriptionArn"]
    try:
        sns.publish(
            TopicArn=topic,
            Message='{"schemaVersion":1}',
            MessageGroupId="verification",
            MessageDeduplicationId=identifier,
        )
        messages = sqs.receive_message(QueueUrl=queue, WaitTimeSeconds=2).get("Messages", [])
        assert len(messages) == 1 and json.loads(messages[0]["Body"])["schemaVersion"] == 1
        sqs.delete_message(QueueUrl=queue, ReceiptHandle=messages[0]["ReceiptHandle"])
    finally:
        sns.unsubscribe(SubscriptionArn=subscription)
        sns.delete_topic(TopicArn=topic)
        sqs.delete_queue(QueueUrl=queue)
    print("Se comprobó SNS FIFO, entrega sin envoltorio y confirmación SQS.")

    results = json.loads((ROOT / ".runtime/dataset-results.json").read_text())
    for result in results:
        obj = wait_receipt(result["id"])
        body = obj["Body"].read()
        assert bootstrap.client("kms").verify(
            KeyId=obj["Metadata"]["key-id"],
            Message=body,
            Signature=base64.b64decode(obj["Metadata"]["signature"]),
            MessageType="RAW",
            SigningAlgorithm="RSASSA_PSS_SHA_256",
        )["SignatureValid"]
    print("Se comprobó la firma KMS de los comprobantes precargados en S3.")
    print("Se completaron las verificaciones Floci.")


if __name__ == "__main__":
    verify()
