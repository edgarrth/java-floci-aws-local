#!/usr/bin/env bash
set -euo pipefail
# Se fija Maven y se verifica su distribución antes de ejecutarla.
version=3.10.0
cache="${MAVEN_WRAPPER_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/axiz-maven}"
if [[ ! -x "$cache/apache-maven-$version/bin/mvn" ]]; then
  mkdir -p "$cache"
  url="https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/$version/apache-maven-$version-bin.tar.gz"
  curl --fail --silent --show-error --location "$url" -o "$cache/maven.tar.gz"
  curl --fail --silent --show-error --location "$url.sha512" -o "$cache/maven.sha512"
  (cd "$cache" && printf '%s  maven.tar.gz\n' "$(cat maven.sha512)" | sha512sum -c -)
  tar -xzf "$cache/maven.tar.gz" -C "$cache"
fi
exec "$cache/apache-maven-$version/bin/mvn" "$@"
