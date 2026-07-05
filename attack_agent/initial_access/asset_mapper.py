from __future__ import annotations

from attack_agent.core.schemas import Asset, IntelDocument


def _asset_exists(doc: IntelDocument, asset_id: str) -> bool:
    return any(asset.asset_id == asset_id for asset in doc.assets)


def _platform_assets_from_live(doc: IntelDocument) -> list[Asset]:
    assets: list[Asset] = []
    for obs in doc.observations:
        if obs.get("type") != "live_snapshot":
            continue
        for item in obs.get("platforms", []):
            pid = item.get("platform_id", "UNKNOWN")
            ptype = item.get("platform_type", "UNKNOWN")
            assets.append(Asset(
                asset_id=pid,
                asset_type=ptype,
                host="",
                ip="",
                ports=[],
                protocols=["JSON/UDP"],
                observed_state=item,
                confidence="HIGH",
            ))
    return assets


def map_assets(doc: IntelDocument) -> IntelDocument:
    known_assets = [
        Asset("UAV-001", "UAV", host="172.31.50.10", ip="172.31.50.10", ports=[14550, 14551], protocols=["MAVLink/UDP"], confidence="MEDIUM"),
        Asset("UGV-001", "UGV", host="172.31.50.20", ip="172.31.50.20", ports=[14660, 14661], protocols=["JSON/UDP"], confidence="MEDIUM"),
        Asset("dah-companion", "COMPANION", host="dah-companion", ip="172.31.50.30", ports=[14550, 14552], protocols=["MAVLink/UDP", "JSON/UDP"], confidence="MEDIUM"),
        Asset("dah-gcs", "GCS", host="dah-gcs", ports=[14555, 14562, 8080], protocols=["JSON/UDP", "HTTP"], confidence="MEDIUM"),
        Asset("dah-dashboard", "DASHBOARD", host="dah-dashboard", ip="172.31.50.70", ports=[14571, 8080], protocols=["HTTP", "JSON/UDP", "MAVLink/UDP"], confidence="MEDIUM"),
        Asset("dah-tactical-router", "ROUTER", host="dah-tactical-router", ports=[14560, 14590, 14660, 8080], protocols=["JSON/UDP", "HTTP"], confidence="MEDIUM"),
        Asset("mission-control", "C2", host="mission-control", ports=[14545, 14546, 8080], protocols=["JSON/UDP", "HTTP"], confidence="MEDIUM"),
        Asset("telemetry-collector", "COLLECTOR", host="telemetry-collector", ports=[14541], protocols=["JSON/UDP"], confidence="MEDIUM"),
        Asset("dah-defense", "DEFENSE_AGENT", host="dah-defense", ip="172.31.50.60", ports=[14551], protocols=["MAVLink/UDP"], confidence="MEDIUM"),
        Asset("dah-recon", "ATTACK_AGENT", host="dah-recon", ip="172.31.50.40", ports=[14550], protocols=["MAVLink/UDP"], confidence="MEDIUM"),
        Asset("dah-jammer", "FOLLOWUP_MODULE", host="local-chain", ports=[14590], protocols=["JSON/UDP"], confidence="MEDIUM"),
        Asset("tamper", "FOLLOWUP_MODULE", host="local-chain", ports=[], protocols=["synthetic-frame"], confidence="HIGH"),
    ]
    for asset in _platform_assets_from_live(doc) + known_assets:
        if not _asset_exists(doc, asset.asset_id):
            doc.assets.append(asset)
    doc.observations.append({"type": "asset_mapping", "asset_count": len(doc.assets)})
    return doc
