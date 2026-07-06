from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse

from .config import LabConfig, load_config


class SafetyError(ValueError):
    pass


def _host_from_url_or_host(value: str) -> str:
    parsed = urlparse(value)
    return parsed.hostname or value.split(":")[0]


def is_allowed_host(host: str, config: LabConfig | None = None) -> bool:
    cfg = config or load_config()
    normalized = _host_from_url_or_host(host).lower()
    if normalized in cfg.allowed_hostnames:
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if any(str(ip).startswith(prefix) for prefix in cfg.allowed_lab_prefixes):
        return True
    return False


def is_allowed_port(port: int, config: LabConfig | None = None) -> bool:
    cfg = config or load_config()
    return int(port) in cfg.allowed_ports


def validate_target(host: str, port: int, protocol: str = "udp", config: LabConfig | None = None) -> None:
    if protocol.lower() not in {"udp", "http", "https", "tcp"}:
        raise SafetyError(f"protocol not allowed: {protocol}")
    if not is_allowed_host(host, config):
        raise SafetyError(f"host outside local DAH_SMU lab allowlist: {host}")
    if not is_allowed_port(int(port), config):
        raise SafetyError(f"port outside DAH_SMU lab allowlist: {port}")


def require_execution_enabled(config: LabConfig | None = None) -> None:
    cfg = config or load_config()
    if os.getenv(cfg.execution_env_var, "false").lower() != "true":
        raise SafetyError(f"active execution disabled; set {cfg.execution_env_var}=true and pass --execute")


def assert_lab_only_action(host: str, port: int, protocol: str = "udp", execute: bool = False) -> None:
    validate_target(host, port, protocol)
    if execute:
        require_execution_enabled()

