# awg-exporter deploy

[pyinfra](https://pyinfra.com) deploy that installs awg-exporter on a host as a
systemd service: system user, binary, `/etc/awg-exporter` env file and unit.

## Requirements

- Locally: Python 3.11+, `pip install -r deploy/requirements.txt`
- Docker, to build the exporter binary (or a prebuilt binary with `--data build=never`)
- Remote: x86_64 Linux with systemd and Docker (AmneziaWG container)

## Usage

Run from the repo root. Host and SSH access come from pyinfra CLI arguments:

```sh
pyinfra HOST deploy/deploy.py --ssh-user USER [--ssh-port 22] [--ssh-key ~/.ssh/id_ed25519] [--sudo]
```

`--sudo` is needed when connecting as a non-root user (plus `--use-sudo-password`
if sudo asks for a password). Several hosts: `pyinfra host1,host2 ...`. Preview changes
without applying them: add `--dry`.

## Build

By default (`build=auto`) the deploy runs `./build.sh` when any Go source, `go.mod`, `go.sum`,
`Dockerfile` or `build.sh` is newer than the `awg-exporter` binary in the repo root, so a stale
binary is never shipped. `build=always` rebuilds on every run (once, regardless of the number of
hosts); `build=never` deploys `binary_src` as is, e.g. a binary built in CI. The build runs even
with `--dry`.

## Settings

Defaults live in [`defaults.toml`](defaults.toml). Any key can be overridden with `--data`:

```sh
pyinfra HOST deploy/deploy.py --ssh-user root \
    --data http_host=0.0.0.0 \
    --data http_port=9400 \
    --data container_name=my-awg \
    --data 'awg_show_exec=awg show all'
```

Exporter settings (`scrape_interval`, `http_*`, `ops_mode`, `container_name`, `awg_show_exec`,
`clients_table_*`, `docker_host`, `gomemlimit`) are written to `/etc/awg-exporter`; empty values
are omitted so the exporter's built-in default applies. Installation settings (`binary_src`,
`binary_dest`, `config_dest`, `service_name`, `service_user`, `service_groups`) control where
and how the service is installed. List values are passed comma-separated: `--data service_groups=docker,adm`.

HTTP basic auth is enabled when both `http_auth_user` and `http_auth_password` are set.
The config file is installed with mode 600 since it may contain the password. Keep in
mind that `--data` values end up in shell history; without TLS the credentials are
sent unencrypted, so enable TLS (see below) before exposing the port.

## TLS

Enable with `tls_enabled=true` and either upload an existing certificate:

```sh
pyinfra HOST deploy/deploy.py --ssh-user root --data tls_enabled=true \
    --data tls_cert=path/to/fullchain.pem --data tls_key=path/to/key.pem
```

or let the deploy issue one signed by your CA:

```sh
pyinfra HOST deploy/deploy.py --ssh-user root --data tls_enabled=true \
    --data tls_generate=true \
    --data tls_ca_cert=path/to/ca.pem --data tls_ca_key=path/to/ca.key \
    [--data tls_ca_key_password=...] [--data tls_san=metrics.example.com,203.0.113.7] [--data tls_days=365]
```

The certificate (ECDSA P-256, `serverAuth`) is generated locally, so the CA key never leaves
your machine. SANs default to the host address passed to pyinfra. If the CA is an intermediate,
it is included in the served chain. Generated certificates are cached in `deploy/certs/<host>/`
(git-ignored) and reused until the CA or SANs change or less than 30 days of validity remain,
so repeated deploys don't restart the service needlessly.

On the host the files go to `tls_dest_dir` (`/etc/ssl/awg-exporter`); the key is readable only
by root and the service group. Prometheus then scrapes with:

```yaml
scheme: https
tls_config:
  ca_file: /path/to/ca.pem
```

Setting `tls_enabled=false` switches the exporter back to plain HTTP; installed certificate files
are left in place.

The service is restarted only when the binary, config, unit or TLS files changed.
