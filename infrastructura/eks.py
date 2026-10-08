#!/usr/bin/env python3
"""Se despliegan los servicios en el clúster Kubernetes creado por Floci."""

import hashlib
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.request

import yaml

if __package__:
    from . import bootstrap
else:
    import bootstrap

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime"
CLUSTER_CONTAINER = "floci-eks-axiz-payments"


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, **kwargs)


def kube(*args, **kwargs):
    return docker(
        "exec",
        "-i",
        CLUSTER_CONTAINER,
        "/bin/kubectl",
        "--kubeconfig",
        "/etc/rancher/k3s/k3s.yaml",
        *args,
        **kwargs,
    )


def containerd(*args, **kwargs):
    return docker(
        "exec",
        "-i",
        CLUSTER_CONTAINER,
        "/bin/ctr",
        "--address",
        "/run/k3s/containerd/containerd.sock",
        "--namespace",
        "k8s.io",
        *args,
        **kwargs,
    )


def import_image(image):
    """Se importa la imagen en el almacén de Kubernetes y se comprueba su referencia."""
    with subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE) as archive:
        try:
            containerd("images", "import", "--local", "-", stdin=archive.stdout)
        finally:
            archive.stdout.close()
        if archive.wait():
            raise RuntimeError("No se pudo exportar " + image)
    names = containerd(
        "images", "list", "--quiet", capture_output=True, text=True
    ).stdout.splitlines()
    if not {image, "docker.io/library/" + image} & set(names):
        raise RuntimeError("La imagen no quedó disponible en el namespace k8s.io: " + image)
    print("Se importó la imagen Kubernetes: " + image, flush=True)


def ip(container):
    return docker(
        "inspect",
        container,
        "--format",
        '{{(index .NetworkSettings.Networks "axiz-network").IPAddress}}',
        capture_output=True,
        text=True,
    ).stdout.strip()


def runtime_inventory():
    """Se inspeccionan también contenedores detenidos para conservar nodos recuperables."""
    ids = docker(
        "ps", "--all", "--quiet", "--no-trunc", capture_output=True, text=True
    ).stdout.split()
    if not ids:
        raise RuntimeError("No se encontró el contenedor del clúster EKS local")
    return json.loads(docker("inspect", *ids, capture_output=True, text=True).stdout)


def k3s_image(image):
    """Docker puede registrar la imagen del contenedor como ID; se resuelve por sus etiquetas."""
    if image.startswith("rancher/k3s:"):
        return True
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        return False
    tags = json.loads(
        docker(
            "image",
            "inspect",
            image,
            "--format",
            "{{json .RepoTags}}",
            capture_output=True,
            text=True,
        ).stdout
    )
    return any(tag.startswith("rancher/k3s:") for tag in tags or [])


def local_runtime(containers):
    """Se verifica propiedad, identidad y volumen del único servidor k3s de esta PoC."""
    matching = [c for c in containers if c.get("Name") == "/" + CLUSTER_CONTAINER]
    if len(matching) != 1:
        raise RuntimeError("No se identificó un único contenedor EKS local")
    container = matching[0]
    config = container.get("Config", {})
    labels = config.get("Labels") or {}
    expected = {
        "io.floci.service": "eks",
        "io.floci.resource-id": bootstrap.CLUSTER,
        "io.floci.account": "000000000000",
        "io.floci.region": bootstrap.REGION,
    }
    hostname = config.get("Hostname", "")
    if (
        not container.get("State", {}).get("Running")
        or any(labels.get(key) != value for key, value in expected.items())
        or not k3s_image(config.get("Image", ""))
        or not re.fullmatch(r"[0-9a-f]{12}", hostname)
        or not container.get("Id", "").startswith(hostname)
        or any(value.startswith("K3S_NODE_NAME=") for value in config.get("Env") or [])
        or any(value.startswith("--node-name") for value in config.get("Cmd") or [])
        or not any(
            mount.get("Type") == "volume"
            and mount.get("Name") == CLUSTER_CONTAINER
            and mount.get("Destination") == "/var/lib/rancher/k3s"
            for mount in container.get("Mounts", [])
        )
    ):
        raise RuntimeError("No se pudo verificar la identidad del runtime k3s de Floci")
    return container


def recover_local_nodes(seconds=120, interval=2):
    """Se retiran registros de nodos Docker desaparecidos y se espera la limpieza PodGC."""
    containers = runtime_inventory()
    current = local_runtime(containers)
    hostname = current["Config"]["Hostname"]
    kube("wait", "--for=create", "node/" + hostname, "--timeout=60s", timeout=70)
    kube("wait", "--for=condition=Ready", "node/" + hostname, "--timeout=180s", timeout=190)
    nodes = json.loads(kube("get", "nodes", "-o", "json", capture_output=True, text=True).stdout)
    actual = [n for n in nodes["items"] if n["metadata"]["name"] == hostname]
    if len(actual) != 1 or actual[0].get("spec", {}).get("unschedulable"):
        raise RuntimeError("El nodo k3s actual no está disponible para programar pods")
    stale = [n for n in nodes["items"] if n["metadata"]["name"] != hostname]
    for node in stale:
        name = node["metadata"]["name"]
        labels = node["metadata"].get("labels", {})
        # Se admite sólo la identidad Docker que utiliza el proveedor local de un nodo.
        if (
            not re.fullmatch(r"[0-9a-f]{12}", name)
            or labels.get("kubernetes.io/hostname") != name
            or labels.get("node.kubernetes.io/instance-type") != "k3s"
            or not node["metadata"].get("uid")
        ):
            raise RuntimeError("Se encontró un nodo ajeno al modelo local de Floci: " + name)
        if any(
            c.get("Config", {}).get("Hostname") == name or c.get("Id", "").startswith(name)
            for c in containers
        ):
            raise RuntimeError("Se conserva el nodo con contenedor existente: " + name)
    for node in stale:
        name = node["metadata"]["name"]
        fresh = runtime_inventory()
        if local_runtime(fresh)["Id"] != current["Id"]:
            raise RuntimeError("El runtime EKS cambió durante su recuperación")
        if any(
            c.get("Config", {}).get("Hostname") == name or c.get("Id", "").startswith(name)
            for c in fresh
        ):
            raise RuntimeError("Se conserva el nodo con contenedor existente: " + name)
        # Se protege la operación contra la sustitución del objeto desde la lectura.
        options = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {
                "uid": node["metadata"]["uid"],
            },
        }
        kube(
            "delete",
            "--raw=/api/v1/nodes/" + name,
            "-f",
            "-",
            input=json.dumps(options).encode(),
            timeout=15,
        )
        print("Se retiró el registro del nodo cuyo contenedor desapareció: " + name, flush=True)
    if stale:
        names = {node["metadata"]["name"] for node in stale}
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            pods = json.loads(
                kube(
                    "get",
                    "pods",
                    "--all-namespaces",
                    "-o",
                    "json",
                    "--request-timeout=5s",
                    capture_output=True,
                    text=True,
                    timeout=min(7, remaining),
                ).stdout
            )
            if not any(p.get("spec", {}).get("nodeName") in names for p in pods["items"]):
                break
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        else:
            raise TimeoutError("PodGC no completó la limpieza de los nodos locales desaparecidos")
    print("Se comprobó el nodo EKS actual: " + hostname, flush=True)
    return hostname


def wait_services_ready(seconds=120, interval=2):
    """Se comprueban endpoints no terminantes y readiness por la ruta NodePort real."""
    deadline = time.monotonic() + seconds
    last_error = "sin endpoints listos"
    consecutive = 0
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            for service, port in (("payment", 30080), ("settlement", 30081)):
                response = kube(
                    "-n",
                    "axiz",
                    "get",
                    "endpointslices",
                    "-l",
                    "kubernetes.io/service-name=axiz-" + service,
                    "-o",
                    "json",
                    "--request-timeout=5s",
                    capture_output=True,
                    text=True,
                    timeout=min(7, max(0.1, deadline - time.monotonic())),
                )
                slices = json.loads(response.stdout)
                ready = any(
                    endpoint.get("addresses")
                    and endpoint.get("conditions", {}).get("ready") is True
                    and endpoint.get("conditions", {}).get("terminating") is not True
                    for item in slices.get("items", [])
                    for endpoint in item.get("endpoints", [])
                )
                if not ready:
                    raise ConnectionError("No se dispone de endpoints listos para " + service)
                url = f"http://{CLUSTER_CONTAINER}:{port}/actuator/health/readiness"
                with urllib.request.urlopen(
                    url, timeout=min(3, max(0.1, deadline - time.monotonic()))
                ) as health:
                    status = json.load(health)
                    if not isinstance(status, dict) or status.get("status") != "UP":
                        raise ConnectionError("Readiness no está UP para " + service)
            consecutive += 1
            if consecutive == 3:
                print("Se comprobaron ambos NodePorts y sus endpoints listos.", flush=True)
                return
        except urllib.error.HTTPError as error:
            error.close()
            if error.code not in {408, 429, 500, 502, 503, 504}:
                raise RuntimeError(
                    "Falló readiness por NodePort: HTTP " + str(error.code)
                ) from error
            consecutive = 0
            last_error = "HTTP " + str(error.code)
        except (OSError, subprocess.SubprocessError) as error:
            consecutive = 0
            last_error = str(error)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval, remaining))
    raise TimeoutError(f"No se estabilizaron los Services EKS en {seconds}s: {last_error}")


def diagnose_services():
    """Se imprime estado del nodo y se consultan logs de pods no terminantes."""
    commands = [
        ("get", "nodes", "-o", "wide"),
        ("describe", "nodes"),
        ("-n", "axiz", "get", "pods,services,endpointslices", "-o", "wide"),
        ("-n", "axiz", "get", "events", "--sort-by=.lastTimestamp"),
    ]
    print("Se recopila el diagnóstico EKS sin modificar recursos ni volúmenes.", flush=True)
    for command in commands:
        try:
            kube(*command, "--request-timeout=5s", timeout=8)
        except (OSError, subprocess.SubprocessError) as error:
            print("No se pudo completar una consulta de diagnóstico: " + str(error), flush=True)
    try:
        current = docker(
            "inspect",
            CLUSTER_CONTAINER,
            "--format",
            "{{.Config.Hostname}}",
            capture_output=True,
            text=True,
            timeout=8,
        ).stdout.strip()
        docker("logs", "--tail=80", CLUSTER_CONTAINER, timeout=8)
    except (OSError, subprocess.SubprocessError) as error:
        current = None
        print("No se pudo consultar el runtime EKS: " + str(error), flush=True)
    for service in ("payment", "settlement"):
        try:
            pods = json.loads(
                kube(
                    "-n",
                    "axiz",
                    "get",
                    "pods",
                    "-l",
                    "app=axiz-" + service,
                    "-o",
                    "json",
                    "--request-timeout=5s",
                    capture_output=True,
                    text=True,
                    timeout=8,
                ).stdout
            )
            selected = [
                p
                for p in pods["items"]
                if not p["metadata"].get("deletionTimestamp")
                and (not current or p.get("spec", {}).get("nodeName") == current)
            ]
            for pod in selected[:3]:
                kube(
                    "-n",
                    "axiz",
                    "logs",
                    "pod/" + pod["metadata"]["name"],
                    "-c",
                    service,
                    "--tail=60",
                    "--request-timeout=5s",
                    timeout=8,
                )
            if not selected:
                print(
                    "No se dispone de pods actuales para consultar logs de " + service, flush=True
                )
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print("No se pudo consultar la aplicación " + service + ": " + str(error), flush=True)


def deploy_resources():
    resources = json.loads((RUNTIME / "resources.json").read_text())
    broker = resources["brokers"].rsplit(":", 1)[0]
    aliases = [
        {"ip": ip("axiz-floci"), "hostnames": ["floci"]},
        {"ip": ip(broker), "hostnames": [broker]},
    ]
    kube("get", "--raw=/readyz")
    containerd("version")
    for image in [
        "axiz-tools:2.0.0",
        "axiz-payment-service:2.0.0",
        "axiz-settlement-service:2.0.0",
    ]:
        import_image(image)
    recover_local_nodes()
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
                    "minReadySeconds": 5,
                    "strategy": {
                        "type": "RollingUpdate",
                        "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
                    },
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
            timeout=250,
        )
    wait_services_ready()
    # Se precargan datos con replay idempotente ante un corte posterior a readiness.
    subprocess.run(
        ["python3", "datasets/seed.py", "http://" + CLUSTER_CONTAINER + ":30080"],
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


def deploy():
    try:
        deploy_resources()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        diagnose_services()
        raise


if __name__ == "__main__":
    deploy()
