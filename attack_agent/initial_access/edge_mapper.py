from __future__ import annotations

from attack_agent.core.schemas import Edge, IntelDocument


def map_edges(doc: IntelDocument) -> IntelDocument:
    doc.edges = [
        Edge("UAV-001", "dah-companion", "MAVLink/UDP", 14550, "OUTBOUND", "telemetry", "UAV_TO_COMPANION", ["docker-compose", "recon"]),
        Edge("dah-companion", "dah-gcs", "JSON/UDP", 14555, "OUTBOUND", "telemetry", "COMPANION_TO_GCS", ["docker-compose"]),
        Edge("dah-gcs", "dah-dashboard", "JSON/UDP", 14571, "OUTBOUND", "dashboard_state", "GCS_TO_DASHBOARD", ["docker-compose"]),
        Edge("dah-gcs", "telemetry-collector", "JSON/UDP", 14541, "OUTBOUND", "log", "GCS_TO_COLLECTOR", ["docker-compose"]),
        Edge("dah-gcs", "dah-tactical-router", "JSON/UDP", 14560, "OUTBOUND", "tactical_report", "GCS_TO_ROUTER", ["docker-compose"]),
        Edge("dah-tactical-router", "mission-control", "JSON/UDP", 14545, "OUTBOUND", "situation_report", "ROUTER_TO_C2", ["docker-compose"]),
        Edge("mission-control", "dah-tactical-router", "JSON/UDP", 14546, "OUTBOUND", "tasking", "C2_TO_ROUTER", ["docker-compose"]),
        Edge("dah-tactical-router", "dah-gcs", "JSON/UDP", 14562, "OUTBOUND", "c2_command", "ROUTER_TO_GCS", ["docker-compose"]),
        Edge("dah-dashboard", "UAV-001", "MAVLink/UDP", 14551, "OUTBOUND", "direct_command", "DASHBOARD_TO_UAV", ["dashboard/app.py"]),
        Edge("dah-jammer", "dah-tactical-router", "JSON/UDP", 14590, "OUTBOUND", "link_degradation_sim", "SAFE_FOLLOWUP_TO_ROUTER", ["attack_agent/adapters/jammer_adapter.py"], "HIGH"),
        Edge("tamper", "dah-dashboard", "HTTP/JSON", 9000, "OUTBOUND", "protocol_integrity_alert", "SYNTHETIC_ALERT_TO_DASHBOARD", ["attack_agent/tamper"], "HIGH"),
    ]
    doc.observations.append({"type": "edge_mapping", "edge_count": len(doc.edges)})
    return doc
