# awg-exporter

Prometheus exporter for [AmneziaWG](https://github.com/amnezia-vpn/amneziawg-go) servers
deployed by Amnezia VPN in a Docker container. It reports per-client traffic and handshakes,
labeled with the client names from Amnezia's clients table.

## How it works

Every `AWG_EXPORTER_SCRAPE_INTERVAL` seconds the exporter runs `wg show all` inside the AmneziaWG
container through the Docker Engine API (unix socket, no `docker` CLI needed) and parses its
output. If enabled, it also reads the Amnezia clients table from the container to map peer public
keys to client names. Metrics are served over HTTP(S) on `/metrics` (any path works).

The exporter is a single static binary with a small memory footprint (Go soft memory limit of
10 MiB by default).

## Metrics

| Metric | Type | Labels | Description |
|---|---|---|---|
| `awg_received_bytes` | counter | `peer`, `client_name` | Bytes received by the server from the peer |
| `awg_sent_bytes` | counter | `peer`, `client_name` | Bytes sent by the server to the peer |
| `awg_latest_handshake_seconds` | gauge | `peer`, `client_name` | Unix timestamp of the latest handshake |
| `awg_exporter_status` | gauge | | `1` if the last poll succeeded, `0` otherwise |
| `awg_exporter_errors` | counter | `error_type` | Poll errors: `awg_show_exec`, `awg_show_parse` |

`peer` is the peer's public key; `client_name` comes from the clients table, or is `unidentified`
if the peer is not there (or the table is disabled).

Traffic counters start from zero when the exporter starts and grow by the deltas between polls.
`wg show` prints human-readable sizes (e.g. `1.23 GiB`), so values are approximate; use them with
`rate()`/`increase()`.

## Configuration

All settings are environment variables; see [`contrib/etc/awg-exporter`](contrib/etc/awg-exporter)
for an example file.

| Variable | Default | Description |
|---|---|---|
| `AWG_EXPORTER_SCRAPE_INTERVAL` | `60` | Seconds between polls |
| `AWG_EXPORTER_HTTP_HOST` | `127.0.0.1` | Listen address |
| `AWG_EXPORTER_HTTP_PORT` | `9351` | Listen port |
| `AWG_EXPORTER_OPS_MODE` | `http` | `http` serves metrics; other values disable the HTTP server |
| `AWG_EXPORTER_HTTP_AUTH_USER` | | Basic auth user (enabled when user and password are both set) |
| `AWG_EXPORTER_HTTP_AUTH_PASSWORD` | | Basic auth password |
| `AWG_EXPORTER_TLS_CERT_FILE` | | PEM certificate (full chain); HTTPS is served when cert and key are both set |
| `AWG_EXPORTER_TLS_KEY_FILE` | | PEM private key |
| `AWG_CONTAINER_NAME` | `amnezia-awg` | AmneziaWG container name |
| `AWG_EXPORTER_AWG_SHOW_EXEC` | `wg show all` | Command run in the container (space-separated) |
| `AWG_EXPORTER_CLIENTS_TABLE_ENABLED` | `true` | Resolve peer keys to client names |
| `AWG_EXPORTER_CLIENTS_TABLE_FILE` | `/opt/amnezia/awg/clientsTable` | Clients table path inside the container |
| `DOCKER_HOST` | `unix:///var/run/docker.sock` | Docker socket (unix sockets only) |
| `GOMEMLIMIT` | `10MiB` | Go soft memory limit |

The exporter needs access to the Docker socket, e.g. by running as a user in the `docker` group.
Note that this access is effectively root on the host.

Without TLS, basic auth credentials are sent in clear text: enable TLS before exposing the port
beyond localhost.

## Build

```sh
./build.sh
```

Builds a static `awg-exporter` binary in the repo root inside a Docker builder image
(see [`Dockerfile`](Dockerfile)), so only Docker is required.

## Install

### With pyinfra (recommended)

[`deploy/`](deploy) contains a [pyinfra](https://pyinfra.com) deploy that builds the binary if
needed, creates a system user, installs the config and a systemd service, and can set up basic
auth and TLS, including issuing a certificate signed by your CA:

```sh
pip install -r deploy/requirements.txt
pyinfra HOST deploy/deploy.py --ssh-user USER [--sudo] [--data key=value ...]
```

See [`deploy/README.md`](deploy/README.md) for all options.

### Manually

```sh
install -m 755 awg-exporter /usr/local/bin/
install -m 600 contrib/etc/awg-exporter /etc/awg-exporter   # then edit it
```

[`contrib/awg-exporter.service`](contrib/awg-exporter.service) is a systemd user unit; see the
comments in it for installation.

## Prometheus

```yaml
scrape_configs:
  - job_name: awg
    scheme: https                 # if TLS is enabled
    tls_config:
      ca_file: /path/to/ca.pem
    basic_auth:                   # if basic auth is enabled
      username: prometheus
      password: changeme
    static_configs:
      - targets: ["vpn.example.com:9351"]
```

## Grafana

[`contrib/grafana/dashboards/awg-exporter.json`](contrib/grafana/dashboards/awg-exporter.json)
is a dashboard with active clients, traffic totals and rates per client, and exporter errors.

The exporter refreshes its data once per `AWG_EXPORTER_SCRAPE_INTERVAL` (60s), so a Prometheus scrape
interval of 1m is enough. Rate panels use a 2m minimum interval so that `rate()` always sees at
least two samples at that scrape interval. If you scrape less often, set the Prometheus datasource's
*Scrape interval* in Grafana to match, otherwise those panels show "No data" on short time ranges.

## License

[MIT](LICENSE)
