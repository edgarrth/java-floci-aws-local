#!/usr/bin/env bash
set -euo pipefail
# Se fija Maven y se verifica su distribución antes de ejecutarla.
version=3.10.0
cache="${MAVEN_WRAPPER_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/axiz-maven}"
if ! command -v java >/dev/null 2>&1; then
  printf 'Se requiere Java en PATH para ejecutar Maven.\n' >&2
  exit 127
fi
if [[ ! -x "$cache/apache-maven-$version/bin/mvn" ]]; then
  for prerequisite in curl tar gzip sha512sum mkdir cat; do
    if ! command -v "$prerequisite" >/dev/null 2>&1; then
      printf 'Falta el comando %s para instalar Maven. En Fedora se instala con: sudo dnf install curl tar gzip coreutils\n' "$prerequisite" >&2
      exit 127
    fi
  done
  mkdir -p "$cache"
  url="https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/$version/apache-maven-$version-bin.tar.gz"
  curl --fail --silent --show-error --location "$url" -o "$cache/maven.tar.gz"
  curl --fail --silent --show-error --location "$url.sha512" -o "$cache/maven.sha512"
  (cd "$cache" && printf '%s  maven.tar.gz\n' "$(cat maven.sha512)" | sha512sum -c -)
  tar -xzf "$cache/maven.tar.gz" -C "$cache"
fi
exec "$cache/apache-maven-$version/bin/mvn" "$@"
