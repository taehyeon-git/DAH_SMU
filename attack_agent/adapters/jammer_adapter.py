from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from attack_agent.adapters.base import AgentAdapter
from attack_agent.core.config import load_config, map_lab_host
from attack_agent.core.safety import assert_lab_only_action
from attack_agent.core.schemas import AttackStep, IntelDocument


class JammerAdapter(AgentAdapter):
    name = "dah-jammer"

    def supports(self, step: AttackStep) -> bool:
        return step.agent == self.name or step.action_type == "EW_LINK_DEGRADATION_SIM"

    def build_command(self, step: AttackStep, intel: IntelDocument) -> dict[str, Any]:
        params = step.params
        host = map_lab_host(params.get("router_host", "dah-tactical-router"))
        return {
            "transport": "udp-json",
            "host": host,
            "port": int(params.get("jam_port", 14590)),
            "events": [{"channel": ch, "duration": params.get("duration_sec", 14)} for ch in params.get("channels", ["VHF"])],
        }

    def execute(self, step: AttackStep, intel: IntelDocument) -> dict[str, Any]:
        cmd = self.build_command(step, intel)
        host, port = cmd["host"], int(cmd["port"])
        assert_lab_only_action(host, port, "udp", execute=True)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sent = 0
        for event in cmd["events"][:1]:
            sock.sendto(json.dumps(event).encode(), (host, port))
            sent += 1
        dashboard_result = self._report_dashboard_event(step, cmd, sent)
        return {
            "adapter": self.name,
            "step_id": step.step_id,
            "dry_run": False,
            "sent_events": sent,
            "target": f"{host}:{port}",
            **dashboard_result,
        }

    def _report_dashboard_event(self, step: AttackStep, cmd: dict[str, Any], sent: int) -> dict[str, Any]:
        config = load_config()
        event = cmd["events"][0] if cmd["events"] else {}
        payload = {
            "message_type": "link_degradation_alert",
            "agent_type": self.name,
            "source": "attack_chain",
            "vehicle_id": "UAV-001",
            "severity": "HIGH",
            "status": "EW_LINK_DEGRADED",
            "message": "UAV-001 전술 링크 저하 시뮬레이션",
            "detail": f"{step.action_type} | channel={event.get('channel')} duration={event.get('duration')}s",
            "simulated_only": True,
            "scope": "LOCAL_DOCKER_TESTBED_ONLY",
            "evidence": {
                "step_id": step.step_id,
                "module": step.action_type,
                "router_target": f"{cmd['host']}:{cmd['port']}",
                "sent_events": sent,
                "channel": event.get("channel"),
                "duration_sec": event.get("duration"),
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{config.dashboard_url}/api/agent-event",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2) as response:
                body = json.loads(response.read().decode("utf-8"))
            return {"forwarded_alert": True, "dashboard_response": body, "alert": payload}
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            return {"forwarded_alert": False, "dashboard_error": str(exc), "alert": payload}
