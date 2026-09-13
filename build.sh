docker build .

docker run --rm \
  -v .:/src \
  -w /src \
  -e CGO_ENABLED=1 \
  -e CC=gcc \
  awg-exporter-builder \
  go build -ldflags "-s -w -linkmode external -extldflags '"'-static'"'"
