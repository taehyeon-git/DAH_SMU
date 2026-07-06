from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field


def running_inside_docker() -> bool:
    """Return True when the chain is executing inside a DAH_SMU container."""
    runtime = os.getenv("DAH_RUNTIME", "").lower()
    if runtime in {"docker", "container"}:
        return True
    if runtime in {"host", "local"}:
        return False
    return Path("/.dockerenv").exists()


def _default_url(env_name: str, host_url: str, docker_url: str) -> str:
    configured = os.getenv(env_name)
    if configured:
        return configured
    return docker_url if running_inside_docker() else host_url


def map_lab_host(host: str | None) -> str:
    """Map Docker service names to host-accessible names when run from PowerShell."""
    if not host or running_inside_docker():
        return host or ""
    host_map = {
        "dah-dashboard": os.getenv("DAH_HOST_DASHBOARD_HOST", "localhost"),
        "dah-gcs": os.getenv("DAH_HOST_GCS_HOST", "localhost"),
        "dah-companion": os.getenv("DAH_HOST_COMPANION_HOST", "localhost"),
        "dah-uav": os.getenv("DAH_HOST_UAV_HOST", "localhost"),
        "dah-tactical-router": os.getenv("DAH_HOST_ROUTER_HOST", "localhost"),
        "tactical-router": os.getenv("DAH_HOST_ROUTER_HOST", "localhost"),
        "mission-control": os.getenv("DAH_HOST_C2_HOST", "localhost"),
        "dah-mission-control": os.getenv("DAH_HOST_C2_HOST", "localhost"),
    }
    return host_map.get(host, host)


@dataclass(frozen=True)
class ServiceTarget:
    name: str
    base_url: str
    purpose: str
    safe_get_paths: tuple[str, ...]


@dataclass(frozen=True)
class LabConfig:
    dashboard_url: str = field(default_factory=lambda: _default_url("DAH_DASHBOARD_URL", "http://localhost:9000", "http://dah-dashboard:8080"))
    gcs_url: str = field(default_factory=lambda: _default_url("DAH_GCS_URL", "http://localhost:9000/gcs", "http://dah-gcs:8080"))
    c2_url: str = field(default_factory=lambda: _default_url("DAH_C2_URL", "http://localhost:9000/c2", "http://mission-control:8080"))
    router_url: str = field(default_factory=lambda: _default_url("DAH_ROUTER_URL", "http://localhost:9000/router", "http://dah-tactical-router:8080"))
    router_direct_url: str = field(default_factory=lambda: _default_url("DAH_ROUTER_DIRECT_URL", "http://localhost:8084", "http://dah-tactical-router:8080"))
    output_dir: str = os.getenv("DAH_ATTACK_OUTPUT_DIR", "output")
    execution_env_var: str = "ENABLE_LAB_ATTACKS"
    max_udp_events_per_step: int = int(os.getenv("DAH_MAX_UDP_EVENTS_PER_STEP", "1"))
    allowed_hostnames: set[str] = field(default_factory=lambda: {
        "localhost",
        "127.0.0.1",
        "dah-dashboard",
        "dah-gcs",
        "dah-companion",
        "dah-uav",
        "dah-ugv",
        "dah-tactical-router",
        "tactical-router",
        "mission-control",
        "dah-mission-control",
        "telemetry-collector",
        "dah-telemetry-collector",
        "dah-defense",
        "dah-recon",
    })
    allowed_ports: set[int] = field(default_factory=lambda: {
        8080, 8084, 9000,
        14541, 14545, 14546, 14550, 14551, 14552, 14555, 14560, 14562, 14571, 14590,
        14660, 14661,
    })
    allowed_lab_prefixes: tuple[str, ...] = ("172.31.50.", "127.")

    def service_targets(self) -> list[ServiceTarget]:
        return [
            ServiceTarget("dashboard", self.dashboard_url, "Dashboard/API gateway", ("/health", "/api/live", "/api/failsafe", "/api/topology")),
            ServiceTarget("gcs", self.gcs_url, "Ground Control Station API", ("/health", "/api/status", "/api/dashboard")),
            ServiceTarget("upper_c2", self.c2_url, "Upper C2/BMS API", ("/health", "/api/dashboard", "/api/platforms", "/api/events")),
            ServiceTarget("router", self.router_url, "Tactical Router API via gateway", ("/api/ticn/status", "/api/ticn")),
            ServiceTarget("router_direct", self.router_direct_url, "Tactical Router direct API", ("/api/ticn/status", "/api/ticn")),
        ]


def load_config() -> LabConfig:
    return LabConfig()
