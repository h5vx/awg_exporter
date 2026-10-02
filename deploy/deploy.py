"""
Install awg-exporter as a systemd service.

    pyinfra HOST deploy/deploy.py --ssh-user USER [--sudo] [--data key=value ...]

Defaults are read from defaults.toml next to this file; every key can be
overridden with `--data key=value`. See deploy/README.md.
"""

import tomllib
from pathlib import Path

from pyinfra import host
from pyinfra.facts.server import Arch
from pyinfra.operations import files, server, systemd
from pyinfra.operations.util import any_changed

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_DIR = DEPLOY_DIR.parent

# Exporter settings: deploy key -> environment variable read by the exporter
ENV_VARS = {
    "scrape_interval": "AWG_EXPORTER_SCRAPE_INTERVAL",
    "http_host": "AWG_EXPORTER_HTTP_HOST",
    "http_port": "AWG_EXPORTER_HTTP_PORT",
    "ops_mode": "AWG_EXPORTER_OPS_MODE",
    "container_name": "AWG_CONTAINER_NAME",
    "awg_show_exec": "AWG_EXPORTER_AWG_SHOW_EXEC",
    "clients_table_enabled": "AWG_EXPORTER_CLIENTS_TABLE_ENABLED",
    "clients_table_file": "AWG_EXPORTER_CLIENTS_TABLE_FILE",
    "docker_host": "DOCKER_HOST",
    "gomemlimit": "GOMEMLIMIT",
}


def load_settings():
    with open(DEPLOY_DIR / "defaults.toml", "rb") as f:
        defaults = tomllib.load(f)
    # --data values (and inventory data) take precedence over the defaults file
    return {key: host.data.get(key, default) for key, default in defaults.items()}


def env_value(value):
    if isinstance(value, bool):
        value = "true" if value else "false"
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def as_list(value):
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return list(value)


cfg = load_settings()

env = {
    var: env_value(cfg[key])
    for key, var in ENV_VARS.items()
    if cfg.get(key) not in (None, "")
}

binary_src = Path(cfg["binary_src"])
if not binary_src.is_absolute():
    binary_src = REPO_DIR / binary_src
if not binary_src.is_file():
    raise SystemExit(f"awg-exporter binary not found: {binary_src} (build it first or set --data binary_src=...)")

arch = host.get_fact(Arch)
if arch not in ("x86_64", "amd64"):
    raise SystemExit(f"{host.name}: unsupported architecture {arch!r}, the binary is built for x86_64")

service_user = cfg["service_user"]
service_name = cfg["service_name"]

if service_user != "root":
    server.user(
        name=f"Create {service_user} system user",
        user=service_user,
        system=True,
        shell="/usr/sbin/nologin",
        create_home=False,
        groups=as_list(cfg["service_groups"]),
    )

binary = files.put(
    name="Install awg-exporter binary",
    src=str(binary_src),
    dest=cfg["binary_dest"],
    user="root",
    group="root",
    mode="755",
)

config = files.template(
    name="Install awg-exporter config",
    src=str(DEPLOY_DIR / "templates/awg-exporter.env.j2"),
    dest=cfg["config_dest"],
    user="root",
    group="root",
    mode="644",
    env=env,
)

unit = files.template(
    name="Install awg-exporter systemd unit",
    src=str(DEPLOY_DIR / "templates/awg-exporter.service.j2"),
    dest=f"/etc/systemd/system/{service_name}.service",
    user="root",
    group="root",
    mode="644",
    binary_dest=cfg["binary_dest"],
    config_dest=cfg["config_dest"],
    service_user=service_user,
)

systemd.daemon_reload(
    name="Reload systemd units",
    _if=unit.did_change,
)

systemd.service(
    name="Enable and start awg-exporter",
    service=f"{service_name}.service",
    running=True,
    enabled=True,
)

systemd.service(
    name="Restart awg-exporter on changes",
    service=f"{service_name}.service",
    restarted=True,
    _if=any_changed(binary, config, unit),
)
