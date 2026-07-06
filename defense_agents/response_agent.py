from __future__ import annotations

import os
import time
from queue import Empty
from typing import Any

try:
    from pymavlink import mavutil
except Exception:  # pragma: no cover
    mavutil = None

from .shared.event_bus import DefenseContext
from .shared.models import DefenseAction, IdFactory, Threat
from .shared.utils import http_post_json


class DefenseResponseAgent:
    """Response agent: execute only predefined DAH_SMU lab-safe playbooks."""

    source = "RESPONSE-AGENT"

    def __init__(self, context: DefenseContext, ids: IdFactory) -> None:
        self.context = context
        self.ids = ids
        self.uav_host = os.getenv("UAV_HOST", "172.31.50.10")
        self.uav_port = int(os.getenv("UAV_PORT", "14551"))
        self.gcs_sys_id = int(os.getenv("GCS_SYS_ID", "255"))
        self.router_url = f"http://{os.getenv('ROUTER_HOST', 'dah-tactical-router')}:{os.getenv('ROUTER_PORT', '8080')}"

    def run(self) -> None:
        self.context.event_bus.send(self.source, "대응 에이전트 시작", detail="predefined safe playbooks active", status="OK")
        while self.context.running:
            try:
                threat: Threat = self.context.threat_queue.get(timeout=1)
            except Empty:
                continue
            for playbook in self._select_playbooks(threat):
                action = self._execute_playbook(threat, playbook)
                self.context.action_queue.put(action)
                self.context.recovery_queue.put(action)
                time.sleep(0.2)

    def _select_playbooks(self, threat: Threat) -> list[str]:
        playbook_map = self.context.policy.get("playbooks", {})
        candidates = playbook_map.get(threat.scenario, [threat.recommended_playbook or "IGNORE_AND_MONITOR"])
        allowed = {"BLOCK_COMMAND", "FORCE_RTL", "SAFE_MODE", "FREQ_HOP", "INS_FALLBACK", "HOLD_POSITION", "IGNORE_AND_MONITOR"}
        return [item for item in candidates if item in allowed] or ["IGNORE_AND_MONITOR"]

    def _execute_playbook(self, threat: Threat, playbook: str) -> DefenseAction:
        status = "BLOCKED"
        detail = f"scenario={threat.scenario}, action={playbook}"
        evidence: dict[str, Any] = {"threat": threat.to_dict()}

        if playbook == "BLOCK_COMMAND":
            detail += " | command trust gate marked suspicious in lab event stream"
        elif playbook == "FORCE_RTL":
            evidence["mavlink_result"] = self._send_rtl()
            detail += " | predefined RTL command attempted to lab UAV"
        elif playbook == "SAFE_MODE":
            evidence["mavlink_result"] = self._send_safe_mode()
            detail += " | predefined SAFE_MODE command attempted to lab UAV"
        elif playbook == "FREQ_HOP":
            evidence["router_result"] = self._freq_hop()
            detail += " | Router TICN clear/hop simulation executed"
        elif playbook == "INS_FALLBACK":
            detail += " | GPS trust reduced, INS fallback simulated"
        elif playbook == "HOLD_POSITION":
            detail += " | hold-position recommendation emitted, no arbitrary command generated"
        else:
            status = "OK"
            detail += " | monitoring only"

        action = DefenseAction(
            action_id=self.ids.action_id(),
            threat_id=threat.threat_id,
            scenario=threat.scenario,
            playbook=playbook,
            status=status,
            detail=detail,
            evidence=evidence,
        )
        self.context.event_bus.send(
            self.source,
            self._message_for(playbook, threat),
            level="warn" if status == "BLOCKED" else "info",
            detail=detail,
            status=status,
        )
        return action

    def _send_rtl(self) -> dict[str, Any]:
        if mavutil is None:
            return {"status": "skipped", "reason": "pymavlink unavailable"}
        try:
            mav = mavutil.mavlink_connection(f"udpout:{self.uav_host}:{self.uav_port}", source_system=self.gcs_sys_id)
            mav.mav.command_long_send(
                target_system=1,
                target_component=1,
                command=mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
                confirmation=0,
                param1=0,
                param2=0,
                param3=0,
                param4=0,
                param5=0,
                param6=0,
                param7=0,
            )
            return {"status": "sent", "target": f"{self.uav_host}:{self.uav_port}", "command": "MAV_CMD_NAV_RETURN_TO_LAUNCH"}
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def _send_safe_mode(self) -> dict[str, Any]:
        if mavutil is None:
            return {"status": "skipped", "reason": "pymavlink unavailable"}
        try:
            mav = mavutil.mavlink_connection(f"udpout:{self.uav_host}:{self.uav_port}", source_system=self.gcs_sys_id)
            mav.mav.set_mode_send(target_system=1, base_mode=mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED, custom_mode=0)
            return {"status": "sent", "target": f"{self.uav_host}:{self.uav_port}", "command": "SAFE_MODE"}
        except Exception as exc:
            return {"status": "failed", "error": str(exc)}

    def _freq_hop(self) -> list[dict[str, Any]]:
        results = []
        for channel in ("VHF", "UHF"):
            results.append({
                "channel": channel,
                "result": http_post_json(f"{self.router_url}/api/ticn/clear", {"channel": channel}),
            })
        return results

    @staticmethod
    def _message_for(playbook: str, threat: Threat) -> str:
        if playbook == "FORCE_RTL":
            return "LAND 주입 차단 — RTL playbook 실행"
        if playbook == "SAFE_MODE":
            return "Replay 차단 — SAFE_MODE playbook 실행"
        if playbook == "FREQ_HOP":
            return "TICN 재밍 대응 — FREQ_HOP playbook 실행"
        if playbook == "INS_FALLBACK":
            return "GPS spoofing 대응 — INS fallback playbook 실행"
        if playbook == "HOLD_POSITION":
            return "Fail-safe 유도 대응 — HOLD_POSITION 권고"
        if playbook == "BLOCK_COMMAND":
            return f"{threat.scenario} 차단 — command trust gate 적용"
        return "위협 모니터링 지속"

