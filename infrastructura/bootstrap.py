#!/usr/bin/env python3
"""Se aprovisionan recursos emulados y se renuevan identidades de servicio."""

import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

import boto3
import psycopg
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
RUNTIME.mkdir(exist_ok=True)
REGION = os.environ.get("AWS_REGION", "us-east-1")
ENDPOINT = os.environ.get("AWS_ENDPOINT_URL", "http://localhost:4566")
ACCOUNT = "000000000000"
CLUSTER = "axiz-payments"


def positive_seconds(name, default):
    value = int(os.environ.get(name, default))
    if value <= 0:
        raise ValueError(name + " debe ser un número entero mayor que cero")
    return value


READ_TIMEOUT = positive_seconds("FLOCI_BOOTSTRAP_READ_TIMEOUT_SECONDS", 900)
WAIT_TIMEOUT = positive_seconds("FLOCI_BOOTSTRAP_WAIT_TIMEOUT_SECONDS", 1800)
# Se espera la descarga y el arranque del motor sin repetir mutaciones desde el SDK.
CONFIG = Config(
    retries={"total_max_attempts": 1, "mode": "standard"},
    connect_timeout=5,
    read_timeout=READ_TIMEOUT,
)
TRANSPORT_ERRORS = (
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
WAIT_ERRORS = TRANSPORT_ERRORS + (OSError, psycopg.OperationalError)
EXISTING_CODES = frozenset(
    {
        "DBClusterAlreadyExistsFault",
        "ResourceInUseException",
        "EntityAlreadyExists",
        "EntityAlreadyExistsException",
        "ResourceExistsException",
        "ParameterAlreadyExists",
        "BucketAlreadyOwnedByYou",
        "CacheClusterAlreadyExists",
        "CacheClusterAlreadyExistsFault",
        "ReplicationGroupAlreadyExistsFault",
    }
)
TRANSIENT_CODES = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "TooManyRequestsException",
        "RequestTimeout",
        "RequestTimeoutException",
        "ServiceUnavailable",
        "ServiceUnavailableException",
    }
)


def client(service):
    return boto3.client(service, region_name=REGION, endpoint_url=ENDPOINT, config=CONFIG)


def ensure(describe, create, missing):
    try:
        resource = describe()
        if resource is not None:
            return resource
    except ClientError as error:
        if error.response["Error"]["Code"] not in missing:
            raise
    try:
        return create()
    except ClientError as error:
        if error.response["Error"]["Code"] not in EXISTING_CODES:
            raise
    except (ConnectionClosedError, ReadTimeoutError):
        pass
    # Una respuesta perdida no cancela la operación que sigue ejecutándose en Floci.
    # Se reconcilia por lectura; no se vuelve a enviar la creación.
    print("Se consulta el recurso tras una creación sin respuesta concluyente.", flush=True)
    return wait(describe, "reconciliación del recurso", retryable_codes=missing)


def wait(check, description, seconds=WAIT_TIMEOUT, retryable_codes=frozenset()):
    deadline = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except ClientError as error:
            if error.response["Error"]["Code"] not in TRANSIENT_CODES | set(retryable_codes):
                raise
            last_error = error
        except WAIT_ERRORS as error:
            last_error = error
        time.sleep(2)
    raise TimeoutError("Se agotó la espera de " + description) from last_error


def ready(resource, field, expected, description):
    state = resource[field]
    if state.upper() in {"FAILED", "DELETING", "DELETED", "INCOMPATIBLE-NETWORK"}:
        raise RuntimeError(description + " se encuentra en estado " + state)
    return resource if state == expected else None


def token(service, path=None):
    body = json.dumps(
        {
            "namespace": os.environ.get("POD_NAMESPACE", "axiz"),
            "serviceAccount": os.environ.get("SERVICE_ACCOUNT", "axiz-" + service),
            "expirySeconds": 3600,
        }
    ).encode()
    request = urllib.request.Request(
        ENDPOINT + f"/_floci/eks/clusters/{CLUSTER}/oidc-token",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    result = json.load(urllib.request.urlopen(request, timeout=15))
    target = pathlib.Path(path) if path else RUNTIME / f"{service}.token"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_text(result["token"])
    temp.chmod(0o644)
    temp.replace(target)


def networking():
    ec2 = client("ec2")
    found = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": ["axiz-vpc"]}])["Vpcs"]
    if found:
        vpc = found[0]["VpcId"]
    else:
        vpc = ec2.create_vpc(CidrBlock="10.80.0.0/16")["Vpc"]["VpcId"]
        ec2.create_tags(Resources=[vpc], Tags=[{"Key": "Name", "Value": "axiz-vpc"}])
    subnets = []
    for index, az in [(1, REGION + "a"), (2, REGION + "b")]:
        cidr = f"10.80.{index}.0/24"
        found = ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc]},
                {"Name": "cidr-block", "Values": [cidr]},
            ]
        )["Subnets"]
        subnet = (
            found[0]["SubnetId"]
            if found
            else ec2.create_subnet(VpcId=vpc, CidrBlock=cidr, AvailabilityZone=az)["Subnet"][
                "SubnetId"
            ]
        )
        subnets.append(subnet)
    return subnets


def identity(iam, eks):
    subnets = networking()
    control = ensure(
        lambda: iam.get_role(RoleName="axiz-eks-control"),
        lambda: iam.create_role(
            RoleName="axiz-eks-control",
            AssumeRolePolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "eks.amazonaws.com"},
                            "Action": "sts:AssumeRole",
                        }
                    ],
                }
            ),
        ),
        {"NoSuchEntity", "NoSuchEntityException"},
    )
    ensure(
        lambda: eks.describe_cluster(name=CLUSTER),
        lambda: eks.create_cluster(
            name=CLUSTER,
            roleArn=control["Role"]["Arn"],
            resourcesVpcConfig={"subnetIds": subnets},
            version="1.35",
        ),
        {"ResourceNotFoundException"},
    )
    cluster = wait(
        lambda: ready(eks.describe_cluster(name=CLUSTER)["cluster"], "status", "ACTIVE", "EKS"),
        "EKS",
        retryable_codes={"ResourceNotFoundException"},
    )
    issuer = cluster["identity"]["oidc"]["issuer"].removeprefix("https://")
    provider_arn = f"arn:aws:iam::{ACCOUNT}:oidc-provider/{issuer}"
    ensure(
        lambda: iam.get_open_id_connect_provider(OpenIDConnectProviderArn=provider_arn),
        lambda: iam.create_open_id_connect_provider(
            Url="https://" + issuer,
            ClientIDList=["sts.amazonaws.com"],
            ThumbprintList=["0" * 40],
        ),
        {"NoSuchEntity", "NoSuchEntityException"},
    )
    for service in ("payment", "settlement"):
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Federated": provider_arn},
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            issuer + ":sub": f"system:serviceaccount:axiz:axiz-{service}",
                            issuer + ":aud": "sts.amazonaws.com",
                        }
                    },
                }
            ],
        }
        ensure(
            lambda s=service: iam.get_role(RoleName="axiz-" + s),
            lambda s=service, t=trust: iam.create_role(
                RoleName="axiz-" + s, AssumeRolePolicyDocument=json.dumps(t)
            ),
            {"NoSuchEntity", "NoSuchEntityException"},
        )
        iam.update_assume_role_policy(RoleName="axiz-" + service, PolicyDocument=json.dumps(trust))
        token(service)
    return cluster


def resources():
    kms = client("kms")
    aliases = kms.list_aliases()["Aliases"]
    key = next(
        (a["TargetKeyId"] for a in aliases if a["AliasName"] == "alias/axiz-receipts"),
        None,
    )
    if not key:
        key = kms.create_key(
            KeyUsage="SIGN_VERIFY",
            KeySpec="RSA_2048",
            Description="Firma de comprobantes Axiz",
        )["KeyMetadata"]["KeyId"]
        kms.create_alias(AliasName="alias/axiz-receipts", TargetKeyId=key)
    kms_arn = kms.describe_key(KeyId=key)["KeyMetadata"]["Arn"]
    sm = client("secretsmanager")
    ensure(
        lambda: sm.get_secret_value(SecretId="/axiz/internal-key"),
        lambda: sm.create_secret(Name="/axiz/internal-key", SecretString=secrets.token_urlsafe(32)),
        {"ResourceNotFoundException"},
    )
    internal_value = sm.get_secret_value(SecretId="/axiz/internal-key")["SecretString"]
    (RUNTIME / "internal-key").write_text(internal_value)
    (RUNTIME / "internal-key").chmod(0o600)
    ensure(
        lambda: sm.get_secret_value(SecretId="/axiz/api-key"),
        lambda: sm.create_secret(Name="/axiz/api-key", SecretString=secrets.token_urlsafe(32)),
        {"ResourceNotFoundException"},
    )
    (RUNTIME / "api-key").write_text(sm.get_secret_value(SecretId="/axiz/api-key")["SecretString"])
    (RUNTIME / "api-key").chmod(0o600)
    ensure(
        lambda: client("ssm").get_parameter(Name="/axiz/merchants/merchant-demo/limit"),
        lambda: client("ssm").put_parameter(
            Name="/axiz/merchants/merchant-demo/limit",
            Value="5000.00",
            Type="String",
        ),
        {"ParameterNotFound"},
    )
    ddb = client("dynamodb")
    for table in ("axiz-payment-audit", "axiz-settlements"):
        ensure(
            lambda t=table: ddb.describe_table(TableName=t),
            lambda t=table: ddb.create_table(
                TableName=t,
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
                BillingMode="PAY_PER_REQUEST",
            ),
            {"ResourceNotFoundException"},
        )
    s3 = client("s3")
    ensure(
        lambda: s3.head_bucket(Bucket="axiz-receipts"),
        lambda: s3.create_bucket(
            Bucket="axiz-receipts",
            **(
                {"CreateBucketConfiguration": {"LocationConstraint": REGION}}
                if REGION != "us-east-1"
                else {}
            ),
        ),
        {"404", "NoSuchBucket", "NotFound"},
    )
    s3.put_bucket_versioning(Bucket="axiz-receipts", VersioningConfiguration={"Status": "Enabled"})
    sqs = client("sqs")
    dlq = sqs.create_queue(
        QueueName="axiz-settlement-dlq.fifo",
        Attributes={"FifoQueue": "true", "ContentBasedDeduplication": "true"},
    )["QueueUrl"]
    dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    queue = sqs.create_queue(
        QueueName="axiz-settlement.fifo",
        Attributes={
            "FifoQueue": "true",
            "ContentBasedDeduplication": "true",
            "VisibilityTimeout": "30",
            "RedrivePolicy": json.dumps({"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "5"}),
        },
    )["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue, AttributeNames=["QueueArn"])["Attributes"][
        "QueueArn"
    ]
    sns = client("sns")
    topic = sns.create_topic(
        Name="axiz-payments.fifo",
        Attributes={"FifoTopic": "true", "ContentBasedDeduplication": "true"},
    )["TopicArn"]
    if not any(
        s.get("Endpoint") == queue_arn
        for s in sns.list_subscriptions_by_topic(TopicArn=topic)["Subscriptions"]
    ):
        sns.subscribe(
            TopicArn=topic,
            Protocol="sqs",
            Endpoint=queue_arn,
            Attributes={"RawMessageDelivery": "true"},
        )
    sqs.set_queue_attributes(
        QueueUrl=queue,
        Attributes={
            "Policy": json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "sns.amazonaws.com"},
                            "Action": "sqs:SendMessage",
                            "Resource": queue_arn,
                            "Condition": {"ArnEquals": {"aws:SourceArn": topic}},
                        }
                    ],
                }
            )
        },
    )
    cw = client("cloudwatch")
    cw.put_metric_alarm(
        AlarmName="axiz-payment-volume",
        Namespace="Axiz/Payments",
        MetricName="HttpRequests",
        Dimensions=[{"Name": "Service", "Value": "payment-service"}],
        Statistic="Sum",
        Period=60,
        EvaluationPeriods=1,
        Threshold=1000,
        ComparisonOperator="GreaterThanThreshold",
        TreatMissingData="notBreaching",
    )
    return {
        "topic": topic,
        "queue": queue,
        "kms": kms_arn,
        "secret": sm.describe_secret(SecretId="/axiz/internal-key")["ARN"],
        "apiSecret": sm.describe_secret(SecretId="/axiz/api-key")["ARN"],
    }


def database():
    rds = client("rds")
    password = os.environ.get("DB_ADMIN_PASSWORD", "LocalBootstrapOnly-25!")
    ensure(
        lambda: rds.describe_db_clusters(DBClusterIdentifier="axiz-ledger")["DBClusters"][0],
        lambda: rds.create_db_cluster(
            DBClusterIdentifier="axiz-ledger",
            Engine="aurora-postgresql",
            EngineVersion="18",
            DatabaseName="axiz",
            MasterUsername="axiz_admin",
            MasterUserPassword=password,
            Port=7001,
            EnableIAMDatabaseAuthentication=True,
        )["DBCluster"],
        {"DBClusterNotFoundFault"},
    )
    cluster = wait(
        lambda: ready(
            rds.describe_db_clusters(DBClusterIdentifier="axiz-ledger")["DBClusters"][0],
            "Status",
            "available",
            "Aurora",
        ),
        "Aurora",
        retryable_codes={"DBClusterNotFoundFault"},
    )
    db_host = "floci" if ENDPOINT.startswith("http://floci") else "localhost"
    db_port = int(cluster["Port"])
    ca_source = pathlib.Path("/floci-data/tls/rds-ca.crt")
    if ca_source.exists():
        shutil.copyfile(ca_source, RUNTIME / "rds-ca.crt")
    ca = RUNTIME / "rds-ca.crt"
    if not ca.exists():
        raise RuntimeError(
            "No se encontró la CA de RDS; se requiere montar floci-data en el aprovisionador"
        )

    def connect():
        return psycopg.connect(
            host=db_host,
            port=db_port,
            dbname="axiz",
            user="axiz_admin",
            password=password,
            sslmode="verify-full",
            sslrootcert=str(ca),
            connect_timeout=4,
        )

    conn = wait(connect, "conexión PostgreSQL")
    with conn:
        with conn.cursor() as cur:
            for service in ("payment", "settlement"):
                user = "axiz_" + service
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (user,))
                if not cur.fetchone():
                    cur.execute(
                        psycopg.sql.SQL("CREATE ROLE {} WITH LOGIN").format(
                            psycopg.sql.Identifier(user)
                        )
                    )
                cur.execute(
                    psycopg.sql.SQL("GRANT rds_iam TO {}").format(psycopg.sql.Identifier(user))
                )
                cur.execute(
                    psycopg.sql.SQL(
                        "CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION axiz_admin"
                    ).format(psycopg.sql.Identifier(service))
                )
                cur.execute(
                    psycopg.sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                        psycopg.sql.Identifier(service), psycopg.sql.Identifier(user)
                    )
                )
        conn.commit()
    # Se ejecuta Flyway como propietario de esquema; los servicios conservan permisos DML.
    for service in ("payment", "settlement"):
        url = f"jdbc:postgresql://{db_host}:{db_port}/axiz?sslmode=verify-full&sslrootcert={ca}"
        env = os.environ.copy()
        env.update(
            FLYWAY_URL=url,
            FLYWAY_USER="axiz_admin",
            FLYWAY_PASSWORD=password,
            FLYWAY_SCHEMAS=service,
            FLYWAY_LOCATIONS=f"filesystem:/workspace/{service}-service/src/main/resources/db/migration",
        )
        subprocess.run(
            ["./mvnw", "-B", "-ntp", "-N", "flyway:migrate"],
            check=True,
            env=env,
            cwd=ROOT,
        )
    with connect() as conn, conn.cursor() as cur:
        for service in ("payment", "settlement"):
            cur.execute(
                psycopg.sql.SQL(
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO {}"
                ).format(
                    psycopg.sql.Identifier(service),
                    psycopg.sql.Identifier("axiz_" + service),
                )
            )
            cur.execute(
                psycopg.sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                    psycopg.sql.Identifier(service),
                    psycopg.sql.Identifier("axiz_" + service),
                )
            )
            cur.execute(
                psycopg.sql.SQL("REVOKE ALL ON TABLE {}.flyway_schema_history FROM {}").format(
                    psycopg.sql.Identifier(service),
                    psycopg.sql.Identifier("axiz_" + service),
                )
            )
    return cluster.get("DbClusterResourceId", cluster.get("DBClusterResourceId")), db_port


def policies(iam, resource, db_resource):
    def arn(service, value):
        return f"arn:aws:{service}:{REGION}:{ACCOUNT}:{value}"

    for service in ("payment", "settlement"):
        statements = [
            {
                "Effect": "Allow",
                "Action": ["secretsmanager:GetSecretValue"],
                "Resource": [
                    resource["secret"],
                    arn("secretsmanager", "secret:/axiz/internal-key"),
                    arn("secretsmanager", "secret:/axiz/api-key"),
                    resource["apiSecret"],
                ],
            },
            {
                "Effect": "Allow",
                "Action": ["cloudwatch:PutMetricData"],
                "Resource": "*",
                "Condition": {"StringEquals": {"cloudwatch:namespace": "Axiz/Payments"}},
            },
        ]
        # Se contempla el prefijo de protocolo usado por Floci 2.1.0 para CloudWatch.
        # La política cloudwatch conserva el prefijo y la condición válidos en AWS.
        statements.append(
            {"Effect": "Allow", "Action": ["monitoring:PutMetricData"], "Resource": "*"}
        )
        if service == "payment":
            statements += [
                {
                    "Effect": "Allow",
                    "Action": ["ssm:GetParameter"],
                    "Resource": arn("ssm", "parameter/axiz/merchants/*"),
                },
                {
                    "Effect": "Allow",
                    "Action": ["sns:Publish"],
                    "Resource": resource["topic"],
                },
                # Se compensa la resolución de recursos SNS del protocolo JSON en Floci 2.1.0.
                # Se retira esta concesión al adaptar la política a AWS.
                {
                    "Sid": "FlociSnsProtocolCompatibility",
                    "Effect": "Allow",
                    "Action": ["sns:Publish"],
                    "Resource": arn("sns", "*"),
                },
            ]
        else:
            statements += [
                {
                    "Effect": "Allow",
                    "Action": [
                        "sqs:ReceiveMessage",
                        "sqs:DeleteMessage",
                        "sqs:GetQueueAttributes",
                        "sqs:ChangeMessageVisibility",
                    ],
                    "Resource": arn("sqs", "axiz-settlement.fifo"),
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:PutObject"],
                    "Resource": "arn:aws:s3:::axiz-receipts/receipts/*",
                },
                {
                    "Effect": "Allow",
                    "Action": ["kms:Sign"],
                    "Resource": resource["kms"],
                },
                # Se compensa la resolución global de KeyId del protocolo KMS en Floci 2.1.0.
                # Se conserva la acción Sign y se limita la concesión a claves de la misma cuenta y región.
                {
                    "Sid": "FlociKmsProtocolCompatibility",
                    "Effect": "Allow",
                    "Action": ["kms:Sign"],
                    "Resource": arn("kms", "key/*"),
                },
                {
                    "Effect": "Allow",
                    "Action": ["dynamodb:GetItem", "dynamodb:PutItem"],
                    "Resource": [
                        arn("dynamodb", "table/axiz-settlements"),
                        arn("dynamodb", "table/axiz-payment-audit"),
                    ],
                },
            ]
        if not db_resource:
            raise RuntimeError("Aurora no devolvió DbClusterResourceId para acotar rds-db:connect")
        statements.append(
            {
                "Effect": "Allow",
                "Action": ["rds-db:connect"],
                "Resource": arn("rds-db", f"dbuser:{db_resource}/axiz_{service}"),
            }
        )
        iam.put_role_policy(
            RoleName="axiz-" + service,
            PolicyName="axiz-runtime",
            PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": statements}),
        )


def managed_cache():
    cache = client("elasticache")
    ensure(
        lambda: cache.describe_replication_groups(ReplicationGroupId="axiz-policies")[
            "ReplicationGroups"
        ][0],
        lambda: cache.create_replication_group(
            ReplicationGroupId="axiz-policies",
            ReplicationGroupDescription="Caché de políticas de comercios Axiz",
            Engine="redis",
            CacheNodeType="cache.t3.micro",
            NumCacheClusters=1,
            AutomaticFailoverEnabled=False,
            MultiAZEnabled=False,
            Port=6379,
        )["ReplicationGroup"],
        {"ReplicationGroupNotFoundFault"},
    )
    return wait(
        lambda: ready(
            cache.describe_replication_groups(ReplicationGroupId="axiz-policies")[
                "ReplicationGroups"
            ][0],
            "Status",
            "available",
            "ElastiCache Redis",
        ),
        "ElastiCache Redis",
        retryable_codes={"ReplicationGroupNotFoundFault"},
    )


def managed_engines():
    managed_cache()
    kafka = client("kafka")
    msk = ensure(
        lambda: next(
            (
                c
                for c in kafka.list_clusters()["ClusterInfoList"]
                if c["ClusterName"] == "axiz-events"
            ),
            None,
        ),
        lambda: kafka.create_cluster(
            ClusterName="axiz-events",
            KafkaVersion="4.2.1",
            NumberOfBrokerNodes=1,
            BrokerNodeGroupInfo={
                "InstanceType": "kafka.m5.large",
                "ClientSubnets": [networking()[0]],
            },
        ),
        set(),
    )
    cluster_arn = msk["ClusterArn"]
    wait(
        lambda: ready(
            kafka.describe_cluster(ClusterArn=cluster_arn)["ClusterInfo"],
            "State",
            "ACTIVE",
            "MSK",
        ),
        "MSK",
        retryable_codes={"NotFoundException"},
    )
    brokers = kafka.get_bootstrap_brokers(ClusterArn=cluster_arn)["BootstrapBrokerString"]
    names = subprocess.run(
        ["docker", "ps", "--filter", "name=floci-msk-", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    broker_name = next((name for name in names if name.endswith("axiz-events")), None)
    if not broker_name:
        raise RuntimeError("No se encontró el contenedor real de MSK")
    brokers = broker_name + ":9092"
    (RUNTIME / "kafka-bootstrap").write_text(brokers)
    return brokers


def gateway(target):
    api = client("apigatewayv2")
    existing = next((a for a in api.get_apis()["Items"] if a["Name"] == "axiz-payments"), None)
    if existing:
        api.delete_api(ApiId=existing["ApiId"])
    identifier = api.create_api(
        Name="axiz-payments",
        ProtocolType="HTTP",
        Tags={"floci:override-id": "axiz-payments"},
    )["ApiId"]
    if target == "host":
        payment_base, settlement_base = (
            "http://host.docker.internal:8080",
            "http://host.docker.internal:8081",
        )
    elif target == "eks":
        payment_base, settlement_base = (
            "http://floci-eks-axiz-payments:30080",
            "http://floci-eks-axiz-payments:30081",
        )
    else:
        payment_base, settlement_base = "http://payment:8080", "http://settlement:8081"
    for method, path, base in [
        ("POST", "/api/payments", payment_base),
        ("GET", "/api/payments/{id}", payment_base),
        ("GET", "/api/settlements/{id}", settlement_base),
        ("GET", "/api/settlements/{id}/ledger", settlement_base),
        ("GET", "/api/settlements/{id}/audit", settlement_base),
    ]:
        integration = api.create_integration(
            ApiId=identifier,
            IntegrationType="HTTP_PROXY",
            IntegrationMethod=method,
            IntegrationUri=base + path,
            PayloadFormatVersion="1.0",
        )["IntegrationId"]
        api.create_route(
            ApiId=identifier,
            RouteKey=method + " " + path,
            Target="integrations/" + integration,
        )
    api.create_stage(ApiId=identifier, StageName="$default", AutoDeploy=True)
    return "http://axiz-payments.execute-api.localhost.floci.io:4566"


def provision():
    wait(
        lambda: urllib.request.urlopen(ENDPOINT + "/_floci/health", timeout=2).status == 200,
        "Floci",
    )
    iam, eks = client("iam"), client("eks")
    identity(iam, eks)
    resource = resources()
    db_resource, db_port = database()
    policies(iam, resource, db_resource)
    brokers = managed_engines()
    target = os.environ.get("BOOTSTRAP_TARGET", "compose")
    queue_host = "localhost" if target == "host" else "floci"
    (RUNTIME / "aws.properties").write_text(
        "axiz.aws.region=" + REGION + "\n"
        "axiz.database.port=" + str(db_port) + "\n"
        "axiz.topic-arn=" + resource["topic"] + "\n"
        "axiz.kms-key=" + resource["kms"] + "\n"
        "axiz.queue-url=http://" + queue_host + ":4566/" + ACCOUNT + "/axiz-settlement.fifo\n"
    )
    endpoint = gateway(target)
    (RUNTIME / "resources.json").write_text(
        json.dumps(
            {
                **resource,
                "dbPort": db_port,
                "dbResourceId": db_resource,
                "brokers": brokers,
                "gateway": endpoint,
                "target": target,
            },
            indent=2,
        )
    )
    print("Se completó el aprovisionamiento y las migraciones.")
    print("API Gateway: " + endpoint)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "provision"
    if action == "provision":
        provision()
    elif action == "tokens-loop":
        while True:
            for svc in ("payment", "settlement"):
                token(svc)
            time.sleep(600)
    elif action in ("token", "token-loop"):
        while True:
            token(sys.argv[2], sys.argv[3])
            if action == "token":
                break
            time.sleep(600)
    else:
        raise ValueError("Acción desconocida")
