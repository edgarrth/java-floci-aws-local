#!/usr/bin/env python3
"""Se despliegan los servicios en el clúster Kubernetes creado por Floci."""

import hashlib
import json
import os
import pathlib
import subprocess

import bootstrap
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
CLUSTER_CONTAINER = "floci-eks-axiz-payments"


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, **kwargs)


def kube(*args, **kwargs):
    return docker("exec", "-i", CLUSTER_CONTAINER, "k3s", "kubectl", *args, **kwargs)


def ip(container):
    return docker(
        "inspect",
        container,
        "--format",
        '{{(index .NetworkSettings.Networks "axiz-network").IPAddress}}',
        capture_output=True,
        text=True,
    ).stdout.strip()


resources = json.loads((RUNTIME / "resources.json").read_text())
broker = resources["brokers"].rsplit(":", 1)[0]
aliases = [
    {"ip": ip("axiz-floci"), "hostnames": ["floci"]},
    {"ip": ip(broker), "hostnames": [broker]},
]
for image in [
    "axiz-tools:2.0.0",
    "axiz-payment-service:2.0.0",
    "axiz-settlement-service:2.0.0",
]:
    with subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE) as archive:
        docker(
            "exec",
            "-i",
            CLUSTER_CONTAINER,
            "k3s",
            "ctr",
            "images",
            "import",
            "-",
            stdin=archive.stdout,
        )
        archive.stdout.close()
        if archive.wait():
            raise RuntimeError("No se pudo exportar " + image)
image_ids = {
    service: docker(
        "image",
        "inspect",
        f"axiz-{service}-service:2.0.0",
        "--format",
        "{{.Id}}",
        capture_output=True,
        text=True,
    ).stdout.strip()
    for service in ("payment", "settlement")
}
config_digest = hashlib.sha256(
    (RUNTIME / "aws.properties").read_bytes() + (RUNTIME / "rds-ca.crt").read_bytes()
).hexdigest()
objects = [
    {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "axiz"}},
    {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "axiz-runtime", "namespace": "axiz"},
        "data": {
            "rds-ca.crt": (RUNTIME / "rds-ca.crt").read_text(),
            "kafka-bootstrap": resources["brokers"],
            "aws.properties": (RUNTIME / "aws.properties").read_text(),
        },
    },
]
for service, port, nodeport in [
    ("payment", 8080, 30080),
    ("settlement", 8081, 30081),
]:
    name = "axiz-" + service
    meta = {"name": name, "namespace": "axiz"}
    objects.append(
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                **meta,
                "annotations": {
                    "eks.amazonaws.com/role-arn": f"arn:aws:iam::000000000000:role/{name}"
                },
            },
            "automountServiceAccountToken": False,
        }
    )
    variables = {
        "AWS_REGION": bootstrap.REGION,
        "AXIZ_RUNTIME_DIRECTORY": "/runtime/",
        "AWS_ENDPOINT_URL": "http://floci:4566",
        "AWS_ROLE_ARN": f"arn:aws:iam::000000000000:role/{name}",
        "AWS_WEB_IDENTITY_TOKEN_FILE": "/identity/web-identity.token",
        "DB_HOST": "floci",
        "DB_PORT": str(resources["dbPort"]),
        "DB_USER": "axiz_" + service,
        "DB_CA": "/runtime/rds-ca.crt",
        "REDIS_HOST": "floci",
        "KAFKA_BOOTSTRAP_FILE": "/runtime/kafka-bootstrap",
    }
    variables.update({key: value for key, value in os.environ.items() if key == "JOBS_ENABLED"})
    app = {
        "name": service,
        "image": f"axiz-{service}-service:2.0.0",
        "imagePullPolicy": "Never",
        "env": [{"name": k, "value": v} for k, v in variables.items()],
        "ports": [{"containerPort": port}],
        "volumeMounts": [
            {"name": "identity", "mountPath": "/identity", "readOnly": True},
            {"name": "runtime", "mountPath": "/runtime", "readOnly": True},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
        "resources": {
            "requests": {"cpu": "100m", "memory": "256Mi"},
            "limits": {"cpu": "2", "memory": "768Mi"},
        },
        "securityContext": {
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "runAsNonRoot": True,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        },
        "startupProbe": {
            "httpGet": {"path": "/actuator/health/liveness", "port": port},
            "failureThreshold": 60,
            "periodSeconds": 3,
        },
        "readinessProbe": {
            "httpGet": {"path": "/actuator/health/readiness", "port": port},
            "periodSeconds": 10,
        },
        "livenessProbe": {
            "httpGet": {"path": "/actuator/health/liveness", "port": port},
            "periodSeconds": 20,
        },
    }

    def identity_container(action, svc=service):
        return {
            "name": "identity-" + action,
            "image": "axiz-tools:2.0.0",
            "imagePullPolicy": "Never",
            "command": [
                "python3",
                "/workspace/infrastructura/bootstrap.py",
                action,
                svc,
                "/identity/web-identity.token",
            ],
            "env": [
                {"name": "AWS_ENDPOINT_URL", "value": "http://floci:4566"},
                {
                    "name": "SERVICE_ACCOUNT",
                    "valueFrom": {"fieldRef": {"fieldPath": "spec.serviceAccountName"}},
                },
                {
                    "name": "POD_NAMESPACE",
                    "valueFrom": {"fieldRef": {"fieldPath": "metadata.namespace"}},
                },
            ],
            "volumeMounts": [{"name": "identity", "mountPath": "/identity"}],
            "resources": {
                "requests": {"cpu": "10m", "memory": "64Mi"},
                "limits": {"cpu": "100m", "memory": "128Mi"},
            },
        }

    pod = {
        "serviceAccountName": name,
        "automountServiceAccountToken": False,
        "hostAliases": aliases,
        "terminationGracePeriodSeconds": 30,
        "initContainers": [identity_container("token")],
        "containers": [app, identity_container("token-loop")],
        "volumes": [
            {"name": "identity", "emptyDir": {"medium": "Memory"}},
            {"name": "runtime", "configMap": {"name": "axiz-runtime"}},
            {"name": "tmp", "emptyDir": {}},
        ],
    }
    objects.append(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": meta,
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": name}},
                "template": {
                    "metadata": {
                        "labels": {"app": name},
                        "annotations": {
                            "axiz.pe/image-id": image_ids[service],
                            "axiz.pe/config-digest": config_digest,
                        },
                    },
                    "spec": pod,
                },
            },
        }
    )
    objects.append(
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": meta,
            "spec": {
                "type": "NodePort",
                "selector": {"app": name},
                "ports": [{"port": port, "targetPort": port, "nodePort": nodeport}],
            },
        }
    )
manifest = yaml.safe_dump_all(objects, sort_keys=False)
(RUNTIME / "deployment.local.yml").write_text(manifest)
kube("apply", "-f", "-", input=manifest.encode())
for service in ("payment", "settlement"):
    kube(
        "-n",
        "axiz",
        "rollout",
        "status",
        "deployment/axiz-" + service,
        "--timeout=240s",
    )
# Se precargan datos mediante los Services publicados en el nodo Kubernetes.
subprocess.run(
    [
        "python3",
        "datasets/seed.py",
        "http://" + CLUSTER_CONTAINER + ":30080",
    ],
    check=True,
    cwd=ROOT,
)
subprocess.run(
    [
        "python3",
        "infrastructura/routes.py",
        "http://" + CLUSTER_CONTAINER + ":30080",
        "http://" + CLUSTER_CONTAINER + ":30081",
    ],
    check=True,
    cwd=ROOT,
)
print("Se completó el despliegue EKS. API Gateway: " + resources["gateway"])
