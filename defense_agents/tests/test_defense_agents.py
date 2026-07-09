from __future__ import annotations

import os
import importlib
import unittest

try:
    from pymavlink import mavutil
except ModuleNotFoundError:
    mavutil = None

from defense_agents.detection_agent import DefenseDetectionAgent
from defense_agents.policy_agent import DefensePolicyAgent
from defense_agents.recovery_agent import DefenseRecoveryAgent
from defense_agents.response_agent import DefenseResponseAgent
from defense_agents.shared.event_bus import DefenseContext
from defense_agents.shared.models import DefenseAction, IdFactory, Threat


class DefenseAgentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_prevention = os.environ.get("DEFENSE_PREVENTION_ENABLED")
        self.old_stdout_events = os.environ.get("DEFENSE_STDOUT_EVENTS")
        self.old_dashboard_host = os.environ.get("DASHBOARD_HOST")
        self.old_router_host = os.environ.get("ROUTER_HOST")
        self.old_uav_host = os.environ.get("UAV_HOST")
        os.environ["DEFENSE_PREVENTION_ENABLED"] = "false"
        os.environ["DEFENSE_STDOUT_EVENTS"] = "false"
        os.environ["DASHBOARD_HOST"] = "127.0.0.1"
        os.environ["ROUTER_HOST"] = "127.0.0.1"
        os.environ["UAV_HOST"] = "127.0.0.1"
        self.context = DefenseContext()
        self.ids = IdFactory()
        self.policy = DefensePolicyAgent(self.context).load()

    def tearDown(self) -> None:
        self.context.close()
        if self.old_prevention is None:
            os.environ.pop("DEFENSE_PREVENTION_ENABLED", None)
        else:
            os.environ["DEFENSE_PREVENTION_ENABLED"] = self.old_prevention
        if self.old_stdout_events is None:
            os.environ.pop("DEFENSE_STDOUT_EVENTS", None)
        else:
            os.environ["DEFENSE_STDOUT_EVENTS"] = self.old_stdout_events
        if self.old_dashboard_host is None:
            os.environ.pop("DASHBOARD_HOST", None)
        else:
            os.environ["DASHBOARD_HOST"] = self.old_dashboard_host
        if self.old_router_host is None:
            os.environ.pop("ROUTER_HOST", None)
        else:
            os.environ["ROUTER_HOST"] = self.old_router_host
        if self.old_uav_host is None:
            os.environ.pop("UAV_HOST", None)
        else:
            os.environ["UAV_HOST"] = self.old_uav_host

    def test_policy_agent_loads_baseline(self):
        self.assertEqual(self.context.policy["assets"]["uav"]["platform_id"], "UAV-001")
        self.assertIn(255, self.context.policy["allowed_sys_ids"])
        self.assertIn("MAV_CMD_NAV_LAND", self.context.policy["restricted_commands"])

    def test_detection_agent_emits_jamming_threat(self):
        detector = DefenseDetectionAgent(self.context, self.ids)
        detector._analyze_loss_pct(55.0, {"source": "unit-test"})
        threat = self.context.threat_queue.get_nowait()
        self.assertEqual(threat.scenario, "JAMMING_CRITICAL")
        self.assertEqual(threat.recommended_playbook, "FREQ_HOP")
        self.assertGreaterEqual(threat.confidence, 0.8)

    def test_detection_agent_emits_link_degradation_from_dashboard_event(self):
        detector = DefenseDetectionAgent(self.context, self.ids)
        detector._analyze_agent_events([{
            "agent_type": "dah-jammer",
            "source": "attack_chain",
            "message": "UAV-001 전술 링크 저하 시뮬레이션",
            "detail": "EW_LINK_DEGRADATION_SIM | channel=VHF",
            "status": "EW_LINK_DEGRADED",
        }])
        threat = self.context.threat_queue.get_nowait()
        self.assertEqual(threat.scenario, "EW_LINK_DEGRADATION")
        self.assertEqual(threat.recommended_playbook, "FREQ_HOP")
        self.assertGreaterEqual(threat.confidence, 0.8)

    def test_detection_agent_emits_threat_from_defense_block_event(self):
        detector = DefenseDetectionAgent(self.context, self.ids)
        detector._analyze_agent_events([{
            "agent_type": "DEF",
            "source": "UAV-DEFENSE-GUARD",
            "message": "UAV 명령/상태 위조 시도 차단",
            "detail": '{"cmd": "MAV_CMD_NAV_LAND", "message_type": "COMMAND_LONG"}',
            "status": "BLOCKED",
        }])
        threat = self.context.threat_queue.get_nowait()
        self.assertEqual(threat.scenario, "FORCED_LAND_ATTEMPT")
        self.assertEqual(threat.recommended_playbook, "BLOCK_COMMAND")
        self.assertGreaterEqual(threat.confidence, 0.8)

    def test_dashboard_guard_blocks_failsafe_overlay_event(self):
        if mavutil is None:
            self.skipTest("pymavlink is not installed in this host Python")
        dashboard_app = importlib.import_module("dashboard.app")
        dashboard_app.failsafe_sim["active"] = False
        dashboard_app.apply_defense_rules({
            "enabled": True,
            "block_attack_events": True,
            "block_failsafe_overlay": True,
            "ttl_sec": 30,
            "source": "unit-test",
        })
        try:
            result = dashboard_app.record_agent_event({
                "message_type": "link_degradation_alert",
                "vehicle_id": "UAV-001",
                "status": "EW_LINK_DEGRADED",
                "evidence": {"loss_pct": 75},
            })
            self.assertTrue(result["blocked"])
            self.assertFalse(dashboard_app.failsafe_sim["active"])
            self.assertEqual(result["event"]["status"], "BLOCKED")
        finally:
            dashboard_app.apply_defense_rules({"enabled": False, "ttl_sec": 0})

    def test_uav_guard_blocks_restricted_land_command(self):
        if mavutil is None:
            self.skipTest("pymavlink is not installed in this host Python")
        uav_app = importlib.import_module("uav.mock_uav")
        uav_app.apply_defense_rules({
            "enabled": True,
            "block_unsafe_commands": True,
            "allowed_sys_ids": [255],
            "restricted_commands": ["MAV_CMD_NAV_LAND", "MAV_CMD_DO_SET_MODE"],
            "ttl_sec": 30,
            "source": "unit-test",
        })
        try:
            self.assertTrue(uav_app.defense_blocks_command(255, mavutil.mavlink.MAV_CMD_NAV_LAND))
            self.assertTrue(uav_app.defense_blocks_command(99, mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH))
            self.assertFalse(uav_app.defense_blocks_command(255, mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH))
        finally:
            uav_app.apply_defense_rules({"enabled": False, "ttl_sec": 0})

    def test_response_agent_selects_policy_playbooks(self):
        responder = DefenseResponseAgent(self.context, self.ids)
        threat = Threat(
            threat_id="THREAT-TEST",
            scenario="FORCED_LAND_ATTEMPT",
            severity="HIGH",
            confidence=0.9,
            source="TEST",
            reason="unit test",
            recommended_playbook="FORCE_RTL",
        )
        self.assertEqual(responder._select_playbooks(threat), ["BLOCK_COMMAND", "FORCE_RTL"])

    def test_recovery_agent_writes_reports(self):
        tmp = os.path.join("output", "_test_defense_agents")
        os.makedirs(tmp, exist_ok=True)
        old_output = os.environ.get("DEFENSE_OUTPUT_DIR")
        os.environ["DEFENSE_OUTPUT_DIR"] = tmp
        try:
            recovery = DefenseRecoveryAgent(self.context, self.ids)
            recovery.actions.append(DefenseAction(
                action_id="ACTION-TEST",
                threat_id="THREAT-TEST",
                scenario="GPS_SPOOFING",
                playbook="INS_FALLBACK",
                status="BLOCKED",
                detail="unit test",
            ))
            recovery.write_reports()
            self.assertTrue(os.path.exists(os.path.join(tmp, "defense_incident_report.json")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "defense_policy_recommendations.json")))
        finally:
            if old_output is None:
                os.environ.pop("DEFENSE_OUTPUT_DIR", None)
            else:
                os.environ["DEFENSE_OUTPUT_DIR"] = old_output


if __name__ == "__main__":
    unittest.main()
