from __future__ import annotations

import os
import unittest

from attack_agent.recon import ReconAgent
from attack_agent.core.io import write_json
from attack_agent.core.config import map_lab_host
from attack_agent.core.safety import SafetyError, validate_target
from attack_agent.core.schemas import load_intel, normalize_legacy_recon_json
from attack_agent.initial_access.asset_mapper import map_assets
from attack_agent.initial_access.edge_mapper import map_edges
from attack_agent.initial_access.gcs_reconstructor import reconstruct_gcs
from attack_agent.planner.plan_builder import build_attack_plan
from attack_agent.tamper.mutations import apply_mutation
from attack_agent.tamper.packet_model import synthetic_mavlink_like_packet
from attack_agent.tamper.lab_injector import inject_or_dry_run


class ChainCoreTests(unittest.TestCase):
    def test_lab_host_mapping_for_host_and_docker_runtime(self):
        original = os.environ.get("DAH_RUNTIME")
        try:
            os.environ["DAH_RUNTIME"] = "host"
            self.assertEqual(map_lab_host("dah-tactical-router"), "localhost")
            os.environ["DAH_RUNTIME"] = "docker"
            self.assertEqual(map_lab_host("dah-tactical-router"), "dah-tactical-router")
        finally:
            if original is None:
                os.environ.pop("DAH_RUNTIME", None)
            else:
                os.environ["DAH_RUNTIME"] = original

    def test_legacy_recon_normalizes(self):
        doc = normalize_legacy_recon_json({
            "target": {"platform_id": "UAV-001", "host": "172.31.50.10", "cmd_port": 14551},
            "follow_on_agents": [{"agent": "dah-jammer", "action": "TMMR-JAM", "params": {"router_host": "dah-tactical-router", "jam_port": 14590}}],
            "recon_tags": {"tags": ["CONFIDENCE_HIGH", "LINK_METRICS_AVAILABLE"], "selection_owner": "InitialAccessAgent"},
        })
        self.assertTrue(doc.safety["simulated_only"])
        self.assertEqual(doc.assets[0].asset_id, "UAV-001")
        self.assertEqual(doc.candidate_actions, [])
        self.assertTrue(any(obs["type"] == "recon_tags" for obs in doc.observations))
        self.assertTrue(any(obs["type"] == "legacy_follow_on_agents_ignored" for obs in doc.observations))

    def test_recon_agent_creates_stage_output(self):
        tmp = os.path.join("output", "_test_stage_agent")
        os.makedirs(tmp, exist_ok=True)
        source = os.path.join(tmp, "intel_handoff.json")
        output = os.path.join(tmp, "stage_1_recon.json")
        write_json(source, {
            "target": {"platform_id": "UAV-001", "host": "172.31.50.10", "cmd_port": 14551},
        })
        result = ReconAgent(output_dir=tmp).run(source=source, passive_source=os.path.join(tmp, "missing.json"), output=output)
        doc = load_intel(output)
        self.assertEqual(doc.environment["stage"], "RECON")
        self.assertEqual(doc.assets[0].asset_id, "UAV-001")
        self.assertEqual(result["report"]["output"], output)

    def test_asset_edge_gcs_plan_flow(self):
        doc = normalize_legacy_recon_json({})
        doc.observations.append({
            "type": "api_response",
            "service": "dashboard",
            "path": "/api/failsafe",
            "body": {"heartbeat": {"timeout_sec": 5}, "packet_loss": {"critical_pct": 15}, "failsafe_action": "LOITER"},
        })
        doc.observations.append({
            "type": "api_response",
            "service": "dashboard",
            "path": "/api/live",
            "body": {"platforms": [{"platform_id": "UAV-001", "platform_type": "UAV", "lat": 37.9, "lon": 126.8, "alt": 3500}]},
        })
        doc = map_assets(doc)
        doc = map_edges(doc)
        doc = reconstruct_gcs(doc)
        plan = build_attack_plan(doc, objective="FAILSAFE_INDUCTION")
        self.assertGreaterEqual(len(doc.assets), 4)
        self.assertTrue(any(edge.src == "dah-gcs" and edge.dst == "dah-tactical-router" for edge in doc.edges))
        self.assertTrue(doc.gcs_model.command_egress)
        self.assertTrue(any(action.agent == "dah-jammer" for action in doc.candidate_actions))
        self.assertTrue(plan.steps)

    def test_safety_blocks_external_host(self):
        with self.assertRaises(SafetyError):
            validate_target("8.8.8.8", 53, "udp")

    def test_tamper_dry_run_no_send(self):
        packet = synthetic_mavlink_like_packet()
        mutated = apply_mutation(packet, "FRAME_CRC_BREAK")
        result = inject_or_dry_run(mutated, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["alert"]["integrity_status"], "CRC_FAIL")


if __name__ == "__main__":
    unittest.main()
