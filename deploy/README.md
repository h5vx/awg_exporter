# awg-exporter deploy

[pyinfra](https://pyinfra.com) deploy that installs awg-exporter on a host as a
systemd service: system user, binary, `/etc/awg-exporter` env file and unit.

## Requirements

- Locally: Python 3.11+, `pip install -r deploy/requirements.txt`
- The exporter binary built in the repo root (`./build.sh`)
- Remote: x86_64 Linux with systemd and Docker (AmneziaWG container)

## Usage

Run from the repo root. Host and SSH access come from pyinfra CLI arguments:

```sh
pyinfra HOST deploy/deploy.py --ssh-user USER [--ssh-port 22] [--ssh-key ~/.ssh/id_ed25519] [--sudo]
```

`--sudo` is needed when connecting as a non-root user (plus `--use-sudo-password`
if sudo asks for a password). Several hosts: `pyinfra host1,host2 ...`. Preview changes
without applying them: add `--dry`.

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

The service is restarted only when the binary, config or unit changed.
