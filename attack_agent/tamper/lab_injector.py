from __future__ import annotations

import json
import urllib.request
from urllib.parse import urlparse
from typing import Any

from attack_agent.core.config import load_config
from attack_agent.core.safety import assert_lab_only_action
from attack_agent.tamper.packet_model import LabPacket


def integrity_alert(packet: LabPacket) -> dict[str, Any]:
    annotation = packet.annotations[-1] if packet.annotations else {}
    mode = annotation.get("mode", "UNKNOWN")
    status_map = {
        "FRAME_STX_CORRUPT": "STX_FAIL",
        "FRAME_LENGTH_MISMATCH": "LENGTH_FAIL",
        "FRAME_CRC_BREAK": "CRC_FAIL",
        "FRAME_SIGNATURE_INVALID": "SIGNATURE_FAIL",
        "FRAME_SEQUENCE_ROLLBACK": "SEQUENCE_FAIL",
        "FRAME_REPLAY_OLD_TIMESTAMP": "REPLAY_TIMESTAMP_FAIL",
        "FRAME_PAYLOAD_BITFLIP_SIM": "PAYLOAD_INTEGRITY_FAIL",
    }
    return {
        "message_type": "protocol_integrity_alert",
        "vehicle_id": "UAV-001",
        "integrity_status": status_map.get(mode, "INTEGRITY_FAIL"),
        "frame_mutation_mode": mode,
        "severity": "HIGH" if mode in {"FRAME_CRC_BREAK", "FRAME_STX_CORRUPT", "FRAME_SIGNATURE_INVALID"} else "MEDIUM",
        "simulated_only": True,
        "scope": "LOCAL_DOCKER_TESTBED_ONLY",
        "evidence": {
            **annotation,
            "seq": packet.metadata.get("seq"),
            "expected_crc": packet.metadata.get("expected_crc"),
            "frame_len": len(packet.raw_bytes),
        },
    }


def inject_or_dry_run(packet: LabPacket, dry_run: bool = True) -> dict[str, Any]:
    alert = integrity_alert(packet)
    if dry_run:
        return {
            "adapter": "tamper",
            "dry_run": True,
            "target": f"{packet.dst_host}:{packet.dst_port}",
            "alert": alert,
            "simulated_frame_bytes": len(packet.raw_bytes),
        }
    dashboard_url = load_config().dashboard_url.rstrip("/")
    parsed = urlparse(dashboard_url)
    assert_lab_only_action(parsed.hostname or "", parsed.port or 80, parsed.scheme or "http", execute=True)
    req = urllib.request.Request(
        f"{dashboard_url}/api/agent-event",
        data=json.dumps(alert).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3) as response:
        response.read()
    return {
        "adapter": "tamper",
        "dry_run": False,
        "target": f"{dashboard_url}/api/agent-event",
        "forwarded_alert": True,
        "simulated_frame_bytes": len(packet.raw_bytes),
        "alert": alert,
    }
