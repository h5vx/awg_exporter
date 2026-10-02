#!/bin/sh
# Build a static awg-exporter binary in the repo root using the builder image.
set -eu

cd "$(dirname "$0")"

docker build -t awg-exporter-builder .

# Run as the repo owner so the binary is not owned by root
docker run --rm \
  -u "$(stat -c %u:%g .)" \
  -v "$PWD":/src \
  -w /src \
  -e CGO_ENABLED=1 \
  -e CC=gcc \
  -e GOCACHE=/tmp/go-cache \
  awg-exporter-builder \
  go build -o awg-exporter -ldflags "-s -w -linkmode external -extldflags '-static'"
