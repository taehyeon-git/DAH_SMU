from __future__ import annotations

import os
import threading
import time
from typing import Any

try:
    from pymavlink import mavutil
except Exception:  # pragma: no cover - container dependency may be absent in doc-only environments.
    mavutil = None

from .shared.event_bus import DefenseContext
from .shared.models import IdFactory, Threat
from .shared.utils import clamp_confidence, http_get_json


class DefenseDetectionAgent:
    """Detection agent: classify telemetry, command, link, and agent-event anomalies."""

    source = "DETECTION-AGENT"

    def __init__(self, context: DefenseContext, ids: IdFactory) -> None:
        self.context = context
        self.ids = ids
        self.listen_host = os.getenv("DEFENSE_LISTEN_HOST", "0.0.0.0")
        self.listen_port = int(os.getenv("DEFENSE_LISTEN_PORT", "14551"))
        self.dashboard_url = f"http://{os.getenv('DASHBOARD_HOST', 'dah-dashboard')}:8080"
        self.router_url = f"http://{os.getenv('ROUTER_HOST', 'dah-tactical-router')}:{os.getenv('ROUTER_PORT', '8080')}"
        self.last_seq: dict[int, int] = {}
        self.last_heartbeat_seen = time.time()
        self._recent_threat_keys: dict[str, float] = {}

    def start(self) -> list[threading.Thread]:
        threads = [
            threading.Thread(target=self._mavlink_monitor, name="def-detect-mavlink", daemon=True),
            threading.Thread(target=self._dashboard_monitor, name="def-detect-dashboard", daemon=True),
            threading.Thread(target=self._router_monitor, name="def-detect-router", daemon=True),
        ]
        for thread in threads:
            thread.start()
        self.context.event_bus.send(
            self.source,
            "탐지 에이전트 시작",
            detail=f"MAVLink UDP {self.listen_port}, dashboard/router polling active",
            status="OK",
        )
        return threads

    def _mavlink_monitor(self) -> None:
        if mavutil is None:
            self.context.event_bus.send(
                self.source,
                "MAVLink 감시 비활성화",
                level="warn",
                detail="pymavlink import 실패 — Dashboard/Router 기반 탐지만 수행",
                status="ALERT",
            )
            return

        try:
            mav = mavutil.mavlink_connection(f"udpin:{self.listen_host}:{self.listen_port}")
        except Exception as exc:
            self.context.event_bus.send(
                self.source,
                "MAVLink 감시 소켓 시작 실패",
                level="error",
                detail=str(exc),
                status="ALERT",
            )
            return

        while self.context.running:
            msg = mav.recv_match(blocking=True, timeout=1)
            if msg is None:
                continue
            src_id = int(msg.get_srcSystem())
            seq = int(msg._header.seq)
            msg_type = msg.get_type()
            if msg_type == "HEARTBEAT":
                self.last_heartbeat_seen = time.time()
            if msg_type == "COMMAND_LONG":
                self._analyze_command(src_id, seq, int(msg.command))
            self._analyze_sequence(src_id, seq)

    def _dashboard_monitor(self) -> None:
        while self.context.running:
            time.sleep(3)
            live = http_get_json(f"{self.dashboard_url}/api/live")
            if not live:
                continue
            platforms = {item.get("platform_id"): item for item in live.get("platforms", [])}
            uav = platforms.get("UAV-001", {})
            self._analyze_gps(uav)
            self._analyze_link(uav)
            self._analyze_failsafe(uav, live.get("mission_state", {}))
            self._analyze_agent_events(live.get("agent_events", []))

    def _router_monitor(self) -> None:
        while self.context.running:
            time.sleep(5)
            state = http_get_json(f"{self.router_url}/api/ticn/status")
            if not state:
                continue
            links = state.get("links", {})
            uav_link = links.get("UAV-001", {}) if isinstance(links, dict) else {}
            if uav_link:
                self._analyze_loss_pct(float(uav_link.get("loss_pct", 0) or 0), {"source": "router", "link": uav_link})

    def _analyze_command(self, src_id: int, seq: int, cmd: int) -> None:
        policy = self.context.policy
        allowed_ids = set(int(item) for item in policy.get("allowed_sys_ids", [255]))
        allowed_commands = set(policy.get("allowed_commands", []))
        restricted_commands = set(policy.get("restricted_commands", []))
        cmd_name = self._command_name(cmd)

        if src_id not in allowed_ids:
            scenario = "FORCED_LAND_ATTEMPT" if cmd_name == "MAV_CMD_NAV_LAND" else "COMMAND_INJECTION"
            self._emit_threat(
                scenario=scenario,
                severity="HIGH",
                confidence=0.92,
                reason=f"허용되지 않은 SYS_ID에서 {cmd_name} 명령 수신",
                evidence={"src_id": src_id, "cmd": cmd_name, "cmd_id": cmd, "seq": seq},
                playbook="FORCE_RTL" if scenario == "FORCED_LAND_ATTEMPT" else "BLOCK_COMMAND",
            )

        if cmd_name not in allowed_commands:
            scenario = "FORCED_LAND_ATTEMPT" if cmd_name == "MAV_CMD_NAV_LAND" else "UNKNOWN_COMMAND"
            severity = "HIGH" if cmd_name in restricted_commands else "MEDIUM"
            self._emit_threat(
                scenario=scenario,
                severity=severity,
                confidence=0.84 if severity == "HIGH" else 0.68,
                reason=f"허용 목록 밖 COMMAND_LONG 감지: {cmd_name}",
                evidence={"src_id": src_id, "cmd": cmd_name, "cmd_id": cmd, "seq": seq},
                playbook="FORCE_RTL" if scenario == "FORCED_LAND_ATTEMPT" else "BLOCK_COMMAND",
            )

    def _analyze_sequence(self, src_id: int, seq: int) -> None:
        previous = self.last_seq.get(src_id)
        self.last_seq[src_id] = seq
        if previous is None:
            return
        max_backtrack = int(self.context.policy.get("thresholds", {}).get("replay_max_seq_backtrack", 0))
        if seq <= previous - max_backtrack:
            self._emit_threat(
                scenario="REPLAY_ATTACK",
                severity="HIGH",
                confidence=0.88,
                reason="MAVLink sequence number rollback/replay 의심",
                evidence={"src_id": src_id, "seq": seq, "previous_seq": previous},
                playbook="SAFE_MODE",
            )

    def _analyze_gps(self, uav: dict[str, Any]) -> None:
        thresholds = self.context.policy.get("thresholds", {})
        speed_limit = float(thresholds.get("gps_implied_speed_kmh", 300))
        implied = float(uav.get("implied_speed_kmh", 0) or 0)
        if uav.get("gps_spoofed") or implied >= speed_limit:
            self._emit_threat(
                scenario="GPS_SPOOFING",
                severity="HIGH",
                confidence=0.9 if uav.get("gps_spoofed") else 0.76,
                reason="GPS spoofing flag 또는 물리적으로 비정상적인 implied speed 감지",
                evidence={"gps_spoofed": bool(uav.get("gps_spoofed")), "implied_speed_kmh": implied, "lat": uav.get("lat"), "lon": uav.get("lon")},
                playbook="INS_FALLBACK",
            )

    def _analyze_link(self, uav: dict[str, Any]) -> None:
        ticn = uav.get("ticn", {}) if isinstance(uav.get("ticn"), dict) else {}
        self._analyze_loss_pct(float(ticn.get("loss_pct", 0) or 0), {"source": "dashboard", "ticn": ticn})

    def _analyze_loss_pct(self, loss_pct: float, evidence: dict[str, Any]) -> None:
        thresholds = self.context.policy.get("thresholds", {})
        warn = float(thresholds.get("jamming_loss_warn", 30))
        critical = float(thresholds.get("jamming_loss_critical", 50))
        if loss_pct >= critical:
            self._emit_threat(
                scenario="JAMMING_CRITICAL",
                severity="HIGH",
                confidence=clamp_confidence(0.75 + loss_pct / 200.0),
                reason=f"TICN loss_pct critical threshold 초과: {loss_pct}%",
                evidence={**evidence, "loss_pct": loss_pct},
                playbook="FREQ_HOP",
            )
        elif loss_pct >= warn:
            self._emit_threat(
                scenario="EW_LINK_DEGRADATION",
                severity="MEDIUM",
                confidence=clamp_confidence(0.55 + loss_pct / 200.0),
                reason=f"TICN/EW link degradation warning threshold 초과: {loss_pct}%",
                evidence={**evidence, "loss_pct": loss_pct},
                playbook="IGNORE_AND_MONITOR",
            )

    def _analyze_failsafe(self, uav: dict[str, Any], mission_state: dict[str, Any]) -> None:
        thresholds = self.context.policy.get("thresholds", {})
        heartbeat_gap = time.time() - self.last_heartbeat_seen
        loss_pct = float((uav.get("ticn") or {}).get("loss_pct", 0) or 0) if isinstance(uav.get("ticn"), dict) else 0.0
        phase = str(mission_state.get("phase", ""))
        critical_gap = float(thresholds.get("heartbeat_gap_critical_s", 5))
        if heartbeat_gap >= critical_gap and (loss_pct >= float(thresholds.get("jamming_loss_warn", 30)) or "FAILSAFE" in phase):
            self._emit_threat(
                scenario="FAILSAFE_INDUCTION",
                severity="HIGH",
                confidence=0.82,
                reason="heartbeat gap과 link degradation/fail-safe state가 동시에 관측됨",
                evidence={"heartbeat_gap_s": round(heartbeat_gap, 2), "loss_pct": loss_pct, "mission_phase": phase},
                playbook="HOLD_POSITION",
            )

    def _analyze_agent_events(self, events: list[dict[str, Any]]) -> None:
        for event in events[:20]:
            message = str(event.get("message", ""))
            source = str(event.get("source", ""))
            status = str(event.get("status", ""))
            agent_type = str(event.get("agent_type", ""))
            detail = str(event.get("detail", ""))
            if (
                status in {"EW_LINK_DEGRADED", "FAILSAFE_TRIGGERED", "FAILSAFE_LAND"}
                or agent_type == "dah-jammer"
                or "링크 저하" in message
                or "EW_LINK_DEGRADATION_SIM" in detail
            ):
                self._emit_threat(
                    scenario="EW_LINK_DEGRADATION",
                    severity="HIGH" if status in {"EW_LINK_DEGRADED", "FAILSAFE_TRIGGERED", "FAILSAFE_LAND"} else "MEDIUM",
                    confidence=0.86,
                    reason="Dashboard agent event에서 전술 링크 저하/fail-safe 유도 이벤트 관측",
                    evidence={"event": event},
                    playbook="FREQ_HOP",
                )
                continue
            if "무결성" in message or "CRC_FAIL" in status or source in {"tamper", "Synthetic Protocol Integrity Monitor"}:
                self._emit_threat(
                    scenario="PROTOCOL_FRAME_INTEGRITY",
                    severity="HIGH",
                    confidence=0.8,
                    reason="Dashboard agent event에서 protocol frame integrity alert 관측",
                    evidence={"event": event},
                    playbook="BLOCK_COMMAND",
                )
                break

    def _emit_threat(self, scenario: str, severity: str, confidence: float, reason: str, evidence: dict[str, Any], playbook: str) -> None:
        dedupe_key = f"{scenario}:{evidence.get('src_id')}:{evidence.get('cmd')}:{evidence.get('loss_pct')}:{evidence.get('mission_phase')}"
        now = time.time()
        if now - self._recent_threat_keys.get(dedupe_key, 0.0) < 8.0:
            return
        self._recent_threat_keys[dedupe_key] = now
        threat = Threat(
            threat_id=self.ids.threat_id(),
            scenario=scenario,
            severity=severity,
            confidence=confidence,
            source=self.source,
            reason=reason,
            evidence=evidence,
            recommended_playbook=playbook,
        )
        self.context.threat_queue.put(threat)
        self.context.event_bus.send(
            self.source,
            f"{scenario} 탐지",
            level="warn" if severity in {"HIGH", "MEDIUM"} else "info",
            detail=f"{reason} | confidence={confidence:.2f}",
            status="THREAT",
        )

    @staticmethod
    def _command_name(cmd: int) -> str:
        if mavutil is None:
            return str(cmd)
        try:
            return mavutil.mavlink.enums["MAV_CMD"][int(cmd)].name
        except Exception:
            return f"MAV_CMD_{cmd}"

