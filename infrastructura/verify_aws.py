#!/usr/bin/env python3
"""Se comprueban identidad, permisos, mensajería y comprobantes contra Floci."""

import base64
import json
import pathlib
import uuid

import bootstrap
import boto3
from botocore.exceptions import ClientError

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROLE_PREFIX = "arn:aws:iam::000000000000:role/axiz-"


def assume(service, token_service=None):
    token = (ROOT / f".runtime/{token_service or service}.token").read_text()
    return bootstrap.client("sts").assume_role_with_web_identity(
        RoleArn=ROLE_PREFIX + service,
        RoleSessionName="verification",
        WebIdentityToken=token,
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


payment = assume("payment")
assert "assumed-role/axiz-payment/" in scoped("sts", payment).get_caller_identity()["Arn"]
denied(lambda: assume("settlement", "payment"))
denied(lambda: scoped("dynamodb", payment).scan(TableName="axiz-authorizations"))
print("Se comprobó identidad web, sujeto de ServiceAccount y separación de permisos.")
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
s3, kms = bootstrap.client("s3"), bootstrap.client("kms")
results_file = ROOT / ".runtime/dataset-results.json"
if results_file.exists():
    for result in json.loads(results_file.read_text()):
        obj = s3.get_object(Bucket="axiz-receipts", Key="receipts/" + result["id"] + ".json")
        body = obj["Body"].read()
        assert kms.verify(
            KeyId=obj["Metadata"]["key-id"],
            Message=body,
            Signature=base64.b64decode(obj["Metadata"]["signature"]),
            MessageType="RAW",
            SigningAlgorithm="RSASSA_PSS_SHA_256",
        )["SignatureValid"]
    print("Se comprobó la firma KMS de los comprobantes de precarga en S3.")
else:
    raise AssertionError("Se requiere datasets/seed.sh para verificar comprobantes")
print("Se completaron las verificaciones AWS.")
