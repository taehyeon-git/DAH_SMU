from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlparse

from attack_agent.core.config import LabConfig, load_config
from attack_agent.core.logging_utils import log
from attack_agent.core.safety import SafetyError, validate_target
from attack_agent.core.schemas import ApiEndpoint, IntelDocument


def _url_port(url: str) -> int:
    parsed = urlparse(url)
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def _purpose_for(path: str) -> tuple[str, bool, str]:
    if path in {"/health"}:
        return "health check", True, "LOW"
    if "failsafe" in path:
        return "read fail-safe policy", True, "MEDIUM"
    if "live" in path or "dashboard" in path or "status" in path:
        return "read current operational state", True, "MEDIUM"
    if "ticn" in path:
        return "read tactical link state", True, "MEDIUM"
    return "read lab API state", True, "LOW"


def _safe_get(url: str, timeout: float = 0.5) -> tuple[bool, int | None, list[str], dict]:
    parsed = urlparse(url)
    validate_target(parsed.hostname or "", _url_port(url), parsed.scheme or "http")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            status = int(response.status)
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception:
                data = {"_non_json_body_len": len(body)}
            keys = sorted(data.keys()) if isinstance(data, dict) else ["_list"]
            return True, status, keys, data if isinstance(data, dict) else {"items": data}
    except (urllib.error.URLError, TimeoutError, ValueError, SafetyError):
        return False, None, [], {}


def discover_api_surface(doc: IntelDocument, config: LabConfig | None = None) -> IntelDocument:
    cfg = config or load_config()
    endpoints: list[ApiEndpoint] = []
    for service in cfg.service_targets():
        for path in service.safe_get_paths:
            url = urljoin(service.base_url.rstrip("/") + "/", path.lstrip("/"))
            purpose, read_only, risk = _purpose_for(path)
            reachable, status, keys, data = _safe_get(url)
            derived = {}
            if reachable:
                doc.observations.append({
                    "type": "api_response",
                    "service": service.name,
                    "path": path,
                    "url": url,
                    "body": data,
                })
                if "platforms" in data:
                    doc.observations.append({"type": "live_snapshot", "platforms": data.get("platforms", [])})
                    derived["platform_count"] = len(data.get("platforms", []))
                if "heartbeat" in data:
                    derived["heartbeat_timeout_sec"] = data.get("heartbeat", {}).get("timeout_sec")
                if "packet_loss" in data:
                    derived["packet_loss_critical_pct"] = data.get("packet_loss", {}).get("critical_pct")
            endpoints.append(ApiEndpoint(
                service=service.name,
                method="GET",
                path=path,
                url=url,
                purpose=purpose,
                read_only=read_only,
                risk_level=risk,
                observed_response_fields=keys,
                derived_params=derived,
                reachable=reachable,
                status_code=status,
            ))
    doc.api_surface = endpoints
    doc.observations.append({
        "type": "api_surface_discovery",
        "reachable_endpoints": sum(1 for ep in endpoints if ep.reachable),
        "total_candidates": len(endpoints),
    })
    log("INITIAL-ACCESS", f"API discovery reachable={sum(1 for ep in endpoints if ep.reachable)}/{len(endpoints)}")
    return doc
