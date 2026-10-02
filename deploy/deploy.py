"""
Install awg-exporter as a systemd service.

    pyinfra HOST deploy/deploy.py --ssh-user USER [--sudo] [--data key=value ...]

Defaults are read from defaults.toml next to this file; every key can be
overridden with `--data key=value`. See deploy/README.md.
"""

import re
import sys
import tomllib
from pathlib import Path

from pyinfra import host
from pyinfra.facts.server import Arch
from pyinfra.operations import files, server, systemd
from pyinfra.operations.util import any_changed

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_DIR = DEPLOY_DIR.parent

sys.path.insert(0, str(DEPLOY_DIR))
from builder import ensure_binary  # noqa: E402
from certs import ensure_cert  # noqa: E402

# Exporter settings: deploy key -> environment variable read by the exporter
ENV_VARS = {
    "scrape_interval": "AWG_EXPORTER_SCRAPE_INTERVAL",
    "http_host": "AWG_EXPORTER_HTTP_HOST",
    "http_port": "AWG_EXPORTER_HTTP_PORT",
    "ops_mode": "AWG_EXPORTER_OPS_MODE",
    "http_auth_user": "AWG_EXPORTER_HTTP_AUTH_USER",
    "http_auth_password": "AWG_EXPORTER_HTTP_AUTH_PASSWORD",
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


def local_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_DIR / path


def tls_source_files():
    """Return local (cert, key) paths to install, generating them if requested."""
    if cfg["tls_generate"]:
        if cfg["tls_cert"] or cfg["tls_key"]:
            raise SystemExit("tls_generate cannot be combined with tls_cert/tls_key")
        if not (cfg["tls_ca_cert"] and cfg["tls_ca_key"]):
            raise SystemExit("tls_generate requires tls_ca_cert and tls_ca_key")

        names = as_list(cfg["tls_san"]) or [host.data.get("ssh_hostname") or host.name]
        cache_dir = local_path(cfg["tls_cache_dir"]) / re.sub(r"[^\w.-]", "_", host.name)
        try:
            return ensure_cert(
                cache_dir,
                local_path(cfg["tls_ca_cert"]),
                local_path(cfg["tls_ca_key"]),
                cfg["tls_ca_key_password"],
                names,
                int(cfg["tls_days"]),
            )
        except (OSError, ValueError, TypeError) as e:
            raise SystemExit(f"Cannot issue TLS certificate: {e}")

    if not (cfg["tls_cert"] and cfg["tls_key"]):
        raise SystemExit("tls_enabled requires tls_cert and tls_key, or tls_generate with a CA")
    cert, key = local_path(cfg["tls_cert"]), local_path(cfg["tls_key"])
    for path in (cert, key):
        if not path.is_file():
            raise SystemExit(f"TLS file not found: {path}")
    return cert, key


cfg = load_settings()

if bool(cfg["http_auth_user"]) != bool(cfg["http_auth_password"]):
    raise SystemExit("http_auth_user and http_auth_password must be set together")

env = {
    var: env_value(cfg[key])
    for key, var in ENV_VARS.items()
    if cfg.get(key) not in (None, "")
}

tls_src = tls_source_files() if cfg["tls_enabled"] else None
if tls_src:
    tls_dir = cfg["tls_dest_dir"].rstrip("/")
    env["AWG_EXPORTER_TLS_CERT_FILE"] = env_value(f"{tls_dir}/cert.pem")
    env["AWG_EXPORTER_TLS_KEY_FILE"] = env_value(f"{tls_dir}/key.pem")

binary_src = local_path(cfg["binary_src"])
ensure_binary(REPO_DIR, binary_src, cfg["build"])
if not binary_src.is_file():
    raise SystemExit(f"awg-exporter binary not found: {binary_src}")

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

# The service user needs to read the TLS key
service_group = service_user if service_user != "root" else "root"
tls_changes = []
if tls_src:
    files.directory(
        name="Create TLS directory",
        path=tls_dir,
        user="root",
        group=service_group,
        mode="750",
    )
    tls_changes.append(
        files.put(
            name="Install TLS certificate",
            src=str(tls_src[0]),
            dest=f"{tls_dir}/cert.pem",
            user="root",
            group=service_group,
            mode="644",
        )
    )
    tls_changes.append(
        files.put(
            name="Install TLS key",
            src=str(tls_src[1]),
            dest=f"{tls_dir}/key.pem",
            user="root",
            group=service_group,
            mode="640",
        )
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
    mode="600",
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
    _if=any_changed(binary, config, unit, *tls_changes),
)
