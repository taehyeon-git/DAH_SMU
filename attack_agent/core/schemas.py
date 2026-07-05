from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .io import read_json, write_json
from .logging_utils import utc_now


LAB_SCOPE = "LOCAL_DOCKER_TESTBED_ONLY"


@dataclass
class Asset:
    asset_id: str
    asset_type: str
    host: str = ""
    ip: str = ""
    ports: list[int] = field(default_factory=list)
    protocols: list[str] = field(default_factory=list)
    observed_state: dict[str, Any] = field(default_factory=dict)
    confidence: str = "LOW"


@dataclass
class Edge:
    src: str
    dst: str
    protocol: str
    port: int
    direction: str
    message_type: str
    trust_boundary: str
    evidence: list[str] = field(default_factory=list)
    confidence: str = "MEDIUM"


@dataclass
class ApiEndpoint:
    service: str
    method: str
    path: str
    url: str
    purpose: str
    read_only: bool
    risk_level: str
    observed_response_fields: list[str] = field(default_factory=list)
    derived_params: dict[str, Any] = field(default_factory=dict)
    reachable: bool = False
    status_code: int | None = None


@dataclass
class GCSModel:
    telemetry_ingress: str = ""
    command_egress: str = ""
    dashboard_command_path: str = ""
    upper_c2_command_path: str = ""
    heartbeat_behavior: dict[str, Any] = field(default_factory=dict)
    failsafe_policy: dict[str, Any] = field(default_factory=dict)
    trust_assumptions: list[str] = field(default_factory=list)
    weak_points: list[str] = field(default_factory=list)
    current_vehicle_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateAction:
    action_id: str
    agent: str
    action_type: str
    reason: str
    required_params: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    preconditions: list[str] = field(default_factory=list)
    expected_effect: str = ""
    risk: str = "MEDIUM"
    confidence: float = 0.5
    dry_run_supported: bool = True


@dataclass
class AttackStep:
    step_id: str
    action_id: str
    agent: str
    action_type: str
    reason: str
    params: dict[str, Any]
    expected_effect: str
    dry_run: bool = True


@dataclass
class AttackPlan:
    plan_id: str
    steps: list[AttackStep] = field(default_factory=list)
    rollback_or_stop_conditions: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    safety_mode: str = "DRY_RUN"
    simulated_only: bool = True
    scope: str = LAB_SCOPE


@dataclass
class IntelDocument:
    schema_version: str = "1.0"
    created_at: str = field(default_factory=utc_now)
    source: str = "unknown"
    environment: dict[str, Any] = field(default_factory=dict)
    observations: list[dict[str, Any]] = field(default_factory=list)
    assets: list[Asset] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    api_surface: list[ApiEndpoint] = field(default_factory=list)
    gcs_model: GCSModel = field(default_factory=GCSModel)
    candidate_actions: list[CandidateAction] = field(default_factory=list)
    recommended_chain: list[str] = field(default_factory=list)
    confidence: float = 0.0
    safety: dict[str, Any] = field(default_factory=lambda: {"simulated_only": True, "scope": LAB_SCOPE, "dry_run_default": True})


def validate_intel(doc: IntelDocument) -> None:
    if not doc.safety.get("simulated_only"):
        raise ValueError("IntelDocument must be simulated_only")
    if doc.safety.get("scope") != LAB_SCOPE:
        raise ValueError("IntelDocument must be scoped to LOCAL_DOCKER_TESTBED_ONLY")


def save_intel(path: str, doc: IntelDocument) -> None:
    validate_intel(doc)
    write_json(path, doc)


def load_intel(path: str) -> IntelDocument:
    raw = read_json(path, {})
    if raw.get("schema_version") and "candidate_actions" in raw:
        return normalize_normalized_json(raw)
    return normalize_legacy_recon_json(raw)


def normalize_normalized_json(raw: dict[str, Any]) -> IntelDocument:
    doc = IntelDocument(
        schema_version=str(raw.get("schema_version", "1.0")),
        created_at=str(raw.get("created_at", utc_now())),
        source=str(raw.get("source", "normalized_json")),
        environment=dict(raw.get("environment", {})),
        observations=list(raw.get("observations", [])),
        confidence=float(raw.get("confidence", 0.0) or 0.0),
        recommended_chain=list(raw.get("recommended_chain", [])),
        safety=dict(raw.get("safety", {"simulated_only": True, "scope": LAB_SCOPE})),
    )
    doc.assets = [Asset(**item) for item in raw.get("assets", [])]
    doc.edges = [Edge(**item) for item in raw.get("edges", [])]
    doc.api_surface = [ApiEndpoint(**item) for item in raw.get("api_surface", [])]
    if raw.get("gcs_model"):
        doc.gcs_model = GCSModel(**raw["gcs_model"])
    doc.candidate_actions = [CandidateAction(**item) for item in raw.get("candidate_actions", [])]
    validate_intel(doc)
    return doc


def normalize_legacy_recon_json(raw: dict[str, Any]) -> IntelDocument:
    doc = IntelDocument(source="legacy_recon_json")
    doc.observations.append({"type": "legacy_recon_loaded", "keys": sorted(raw.keys())})
    target = raw.get("target", {})
    uav = raw.get("uav001", {})
    uav_state = uav.get("state") if isinstance(uav, dict) else None
    if isinstance(target, dict) and target:
        doc.assets.append(Asset(
            asset_id=target.get("platform_id", "UAV-001"),
            asset_type="UAV",
            host=target.get("host", ""),
            ip=target.get("host", ""),
            ports=[int(target.get("cmd_port", 14551))],
            protocols=["MAVLink/UDP"],
            observed_state=target,
            confidence="HIGH",
        ))
    elif isinstance(uav_state, dict) and uav_state:
        doc.assets.append(Asset(
            asset_id="UAV-001",
            asset_type="UAV",
            host="172.31.50.10",
            ip="172.31.50.10",
            ports=[14551],
            protocols=["MAVLink/UDP"],
            observed_state=uav_state,
            confidence="HIGH",
        ))
    baseline = raw.get("api_baseline") or raw.get("phase0_api_baseline") or {}
    if baseline:
        doc.observations.append({"type": "api_baseline", "value": baseline})
    for item in raw.get("follow_on_agents", []):
        params = dict(item.get("params", {}))
        action = CandidateAction(
            action_id=item.get("action", item.get("agent", "legacy-action")).replace(" ", "_"),
            agent=item.get("agent", "unknown"),
            action_type=item.get("action", "LEGACY_FOLLOW_ON"),
            reason=item.get("reason", "legacy recon recommendation"),
            params=params,
            expected_effect=item.get("expected_effect", item.get("timing", "")),
            risk="MEDIUM",
            confidence=0.65,
        )
        doc.candidate_actions.append(action)
    doc.confidence = 0.65 if doc.assets else 0.35
    validate_intel(doc)
    return doc

