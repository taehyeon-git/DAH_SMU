from __future__ import annotations

import json
import os
import time
from queue import Empty
from typing import Any

from .shared.event_bus import DefenseContext
from .shared.models import DefenseAction, IdFactory, RecoveryObservation, utc_now
from .shared.utils import http_get_json


class DefenseRecoveryAgent:
    """Recovery agent: verify normalization and write incident/policy reports."""

    source = "RECOVERY-AGENT"

    def __init__(self, context: DefenseContext, ids: IdFactory) -> None:
        self.context = context
        self.ids = ids
        self.dashboard_url = f"http://{os.getenv('DASHBOARD_HOST', 'dah-dashboard')}:8080"
        self.output_dir = os.getenv("DEFENSE_OUTPUT_DIR", "output")
        self.incident_id = self.ids.incident_id()
        self.started_at = utc_now()
        self.actions: list[DefenseAction] = []
        self.observations: list[RecoveryObservation] = []
        os.makedirs(self.output_dir, exist_ok=True)

    def run(self) -> None:
        self.context.event_bus.send(self.source, "복구 에이전트 시작", detail="incident reporting and recovery verification active", status="OK")
        last_periodic = 0.0
        while self.context.running:
            try:
                action: DefenseAction = self.context.recovery_queue.get(timeout=1)
                self.actions.append(action)
                observation = self._verify_recovery(action)
                self.observations.append(observation)
                self._send_recovery_event(observation)
                self.write_reports()
            except Empty:
                pass

            if time.time() - last_periodic >= 15:
                last_periodic = time.time()
                self.write_reports()

    def _verify_recovery(self, action: DefenseAction) -> RecoveryObservation:
        live = http_get_json(f"{self.dashboard_url}/api/live")
        platforms = {item.get("platform_id"): item for item in live.get("platforms", [])} if live else {}
        uav = platforms.get("UAV-001", {})
        ticn = uav.get("ticn", {}) if isinstance(uav.get("ticn"), dict) else {}
        loss_pct = float(ticn.get("loss_pct", 0) or 0)
        gps_spoofed = bool(uav.get("gps_spoofed"))
        phase = str((live.get("mission_state", {}) if live else {}).get("phase", "UNKNOWN"))

        recovered = True
        detail_parts = []
        if action.playbook == "FREQ_HOP":
            critical = float(self.context.policy.get("thresholds", {}).get("jamming_loss_critical", 50))
            recovered = loss_pct < critical
            detail_parts.append(f"loss_pct={loss_pct}, critical={critical}")
        elif action.playbook == "INS_FALLBACK":
            recovered = not gps_spoofed
            detail_parts.append(f"gps_spoofed={gps_spoofed}")
        elif action.playbook in {"FORCE_RTL", "SAFE_MODE", "HOLD_POSITION"}:
            detail_parts.append(f"mission_phase={phase}")
        else:
            detail_parts.append("monitoring state recorded")

        status = "RECOVERED" if recovered else "MONITORING"
        return RecoveryObservation(
            incident_id=self.incident_id,
            status=status,
            detail="; ".join(detail_parts),
            evidence={"action": action.to_dict(), "uav": uav, "mission_phase": phase},
        )

    def _send_recovery_event(self, observation: RecoveryObservation) -> None:
        message = "상태 복구 확인" if observation.status == "RECOVERED" else "복구 확인 중 — 모니터링 지속"
        self.context.event_bus.send(
            self.source,
            message,
            level="info" if observation.status == "RECOVERED" else "warn",
            detail=observation.detail,
            status=observation.status,
        )

    def write_reports(self) -> None:
        scenarios = sorted({action.scenario for action in self.actions})
        actions = [action.playbook for action in self.actions]
        report = {
            "incident_id": self.incident_id,
            "started_at": self.started_at,
            "ended_at": utc_now(),
            "simulated_only": True,
            "scope": "LOCAL_DOCKER_TESTBED_ONLY",
            "scenarios": scenarios,
            "timeline": self.context.event_bus.timeline_dicts(),
            "actions": actions,
            "action_details": [action.to_dict() for action in self.actions],
            "recovery_observations": [item.to_dict() for item in self.observations],
            "recommendations": self._recommendation_strings(),
        }
        with open(os.path.join(self.output_dir, "defense_incident_report.json"), "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        with open(os.path.join(self.output_dir, "defense_policy_recommendations.json"), "w", encoding="utf-8") as handle:
            json.dump({"recommendations": self._policy_recommendations()}, handle, ensure_ascii=False, indent=2)

    def _recommendation_strings(self) -> list[str]:
        return [item["suggestion"] for item in self._policy_recommendations()]

    def _policy_recommendations(self) -> list[dict[str, str]]:
        scenarios = [action.scenario for action in self.actions]
        recs: list[dict[str, str]] = []
        if any(item in {"COMMAND_INJECTION", "FORCED_LAND_ATTEMPT", "UNKNOWN_COMMAND"} for item in scenarios):
            recs.append({
                "category": "COMMAND_POLICY",
                "reason": "COMMAND_LONG trust violation observed",
                "suggestion": "Restrict COMMAND_LONG source to GCS SYS_ID 255 and validate mission state before LAND/SET_MODE.",
            })
        if any(item in {"EW_LINK_DEGRADATION", "JAMMING_CRITICAL", "FAILSAFE_INDUCTION"} for item in scenarios):
            recs.append({
                "category": "LINK_PROTECTION",
                "reason": "loss_pct or heartbeat gap exceeded defense thresholds",
                "suggestion": "Prepare FREQ_HOP before critical loss and separate telemetry gap from command gap before fail-safe action.",
            })
        if "GPS_SPOOFING" in scenarios:
            recs.append({
                "category": "NAVIGATION_TRUST",
                "reason": "GPS spoofing or impossible implied speed observed",
                "suggestion": "Keep INS fallback and physical plausibility validation active for UAV position updates.",
            })
        if "PROTOCOL_FRAME_INTEGRITY" in scenarios:
            recs.append({
                "category": "PROTOCOL_INTEGRITY",
                "reason": "Synthetic protocol integrity alert observed",
                "suggestion": "Preserve MAVLink parser integrity checks and quarantine untrusted frame events from command paths.",
            })
        if not recs:
            recs.append({
                "category": "BASELINE",
                "reason": "No active incidents recorded",
                "suggestion": "Maintain lab-only attack event ports and keep recon mirror disabled outside recon-lab profile.",
            })
        return recs

