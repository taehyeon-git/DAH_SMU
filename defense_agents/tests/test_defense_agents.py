from __future__ import annotations

import os
import unittest

from defense_agents.detection_agent import DefenseDetectionAgent
from defense_agents.policy_agent import DefensePolicyAgent
from defense_agents.recovery_agent import DefenseRecoveryAgent
from defense_agents.response_agent import DefenseResponseAgent
from defense_agents.shared.event_bus import DefenseContext
from defense_agents.shared.models import DefenseAction, IdFactory, Threat


class DefenseAgentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = DefenseContext()
        self.ids = IdFactory()
        self.policy = DefensePolicyAgent(self.context).load()

    def tearDown(self) -> None:
        self.context.close()

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
