from __future__ import annotations

from attack_agent.core.schemas import CandidateAction, GCSModel, IntelDocument


def _latest_api(doc: IntelDocument, service: str, path: str) -> dict:
    for obs in doc.observations:
        if obs.get("type") == "api_response" and obs.get("service") == service and obs.get("path") == path:
            return obs.get("body", {})
    return {}


def _current_vehicle_state(doc: IntelDocument) -> dict:
    live = _latest_api(doc, "dashboard", "/api/live")
    for item in live.get("platforms", []):
        if item.get("platform_id") == "UAV-001":
            return item
    for asset in doc.assets:
        if asset.asset_id == "UAV-001":
            return asset.observed_state
    return {}


def _observation_value(doc: IntelDocument, obs_type: str) -> dict:
    for obs in reversed(doc.observations):
        if obs.get("type") == obs_type and isinstance(obs.get("value"), dict):
            return obs["value"]
    return {}


def _has_recon_tag(doc: IntelDocument, tag: str) -> bool:
    tags = _observation_value(doc, "recon_tags").get("tags", [])
    return tag in tags


def _api_baseline(doc: IntelDocument) -> dict:
    baseline = _observation_value(doc, "api_baseline")
    if baseline:
        return baseline
    failsafe = _latest_api(doc, "dashboard", "/api/failsafe")
    live = _latest_api(doc, "dashboard", "/api/live")
    if failsafe or live:
        return {"api_available": bool(failsafe or live), "failsafe_action": failsafe.get("failsafe_action")}
    return {}


def reconstruct_gcs(doc: IntelDocument) -> IntelDocument:
    failsafe = _latest_api(doc, "dashboard", "/api/failsafe")
    baseline = _api_baseline(doc)
    current = _current_vehicle_state(doc)
    model = GCSModel(
        telemetry_ingress="UAV-001 -> dah-companion -> dah-gcs -> dah-dashboard",
        command_egress="dah-dashboard/mission-control -> dah-gcs/dah-companion -> UAV-001",
        dashboard_command_path="dah-dashboard /api/command -> MAVLink COMMAND_LONG -> UAV-001:14551",
        upper_c2_command_path="mission-control -> dah-tactical-router:14546 -> dah-gcs:14562 -> dah-companion:14552 -> UAV-001:14551",
        heartbeat_behavior=failsafe.get("heartbeat", {"interval_sec": 1, "timeout_sec": 5}),
        failsafe_policy=failsafe,
        trust_assumptions=[
            "GCS accepts companion JSON telemetry on UDP 14555 inside the lab.",
            "Dashboard trusts GCS /api/dashboard and direct UDP fan-out state.",
            "Router link metrics influence dashboard communication-loss presentation.",
            "Command source validation is evaluated by defense-agent, not enforced everywhere by default.",
        ],
        weak_points=[
            "TMMR/TICN link state can be degraded through dah-jammer.",
            "Protocol parser resilience can be tested through synthetic tamper module.",
        ],
        current_vehicle_state=current,
    )
    doc.gcs_model = model
    doc.candidate_actions = build_candidate_actions(model, doc)
    doc.recommended_chain = [action.action_id for action in doc.candidate_actions[:4]]
    doc.confidence = 0.8 if current else 0.6
    doc.observations.append({
        "type": "gcs_reconstruction",
        "weak_point_count": len(model.weak_points),
        "candidate_action_count": len(doc.candidate_actions),
        "selection_source": "InitialAccessAgent",
        "api_baseline_available": bool(baseline),
    })
    return doc


def build_candidate_actions(model: GCSModel, doc: IntelDocument) -> list[CandidateAction]:
    actions: list[CandidateAction] = []
    baseline = _api_baseline(doc)
    current = model.current_vehicle_state
    recon_tags = _observation_value(doc, "recon_tags")
    tags = recon_tags.get("tags", [])

    link_metrics_available = (
        bool(current)
        or bool(baseline.get("ticn_loss_pct") is not None)
        or _has_recon_tag(doc, "LINK_METRICS_AVAILABLE")
    )
    protocol_test_grounded = (
        bool(baseline)
        or _has_recon_tag(doc, "PROTOCOL_FRAME_METADATA_AVAILABLE")
        or _has_recon_tag(doc, "API_BASELINE_AVAILABLE")
    )

    if link_metrics_available:
        actions.append(CandidateAction(
            action_id="ACT-JAMMER-001",
            agent="dah-jammer",
            action_type="EW_LINK_DEGRADATION_SIM",
            reason=(
                "InitialAccessAgent selected this from recon evidence: "
                f"tags={tags or ['live_dashboard_state']} and router/TMMR link metrics influence dashboard communication state."
            ),
            required_params=["router_host", "jam_port", "channels"],
            params={"router_host": "dah-tactical-router", "jam_port": 14590, "channels": ["VHF", "UHF", "HF"], "duration_sec": 14},
            expected_effect="loss_pct rises and link_quality drops in dashboard.",
            risk="MEDIUM",
            confidence=0.86,
        ))

    if protocol_test_grounded:
        actions.append(CandidateAction(
            action_id="ACT-TAMPER-001",
            agent="tamper",
            action_type="PROTOCOL_FRAME_INTEGRITY_SIM",
            reason=(
                "InitialAccessAgent selected this from recon/API evidence: "
                f"tags={tags or ['api_surface']} and synthetic frame validation can test parser resilience without live packet attacks."
            ),
            required_params=["dst_host", "dst_port", "mutation"],
            params={"dst_asset": "local-parser", "dst_host": "localhost", "dst_port": 14550, "mutation": "FRAME_CRC_BREAK", "protocol": "MAVLink-like"},
            expected_effect="Synthetic parser/integrity report records CRC/STX/signature validation failure.",
            risk="LOW",
            confidence=0.9,
        ))

    return actions
