from __future__ import annotations

import argparse
import os
from dataclasses import asdict
from typing import Any

from attack_agent.core.config import load_config
from attack_agent.core.io import write_json
from attack_agent.core.logging_utils import log, utc_now
from attack_agent.core.schemas import IntelDocument, load_intel, save_intel
from attack_agent.initial_access.api_discovery import discover_api_surface
from attack_agent.initial_access.asset_mapper import map_assets
from attack_agent.initial_access.edge_mapper import map_edges
from attack_agent.initial_access.gcs_reconstructor import reconstruct_gcs


class InitialAccessAgent:
    """Stage 2 agent: convert recon intel into attack graph and follow-up options."""

    name = "InitialAccessAgent"

    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir
        self.config = load_config()

    def run(
        self,
        recon_intel: str = "output/stage_1_recon.json",
        output: str | None = None,
        graph_output: str | None = None,
    ) -> dict[str, Any]:
        output_path = output or os.path.join(self.output_dir, "stage_2_initial_access.json")
        graph_path = graph_output or os.path.join(self.output_dir, "stage_2_attack_graph.json")
        doc = load_intel(recon_intel)
        doc.environment.update({
            "stage": "INITIAL_ACCESS",
            "agent": self.name,
            "input_recon_intel": recon_intel,
            "dashboard_url": self.config.dashboard_url,
            "gcs_url": self.config.gcs_url,
            "c2_url": self.config.c2_url,
            "router_url": self.config.router_url,
        })
        doc.observations.append({
            "type": "stage_transition",
            "stage": "INITIAL_ACCESS",
            "agent": self.name,
            "created_at": utc_now(),
            "summary": "Recon intel converted into API surface, attack graph, and follow-up candidates.",
        })
        doc = discover_api_surface(doc, self.config)
        doc = map_assets(doc)
        doc = map_edges(doc)
        doc = reconstruct_gcs(doc)
        graph = self._attack_graph(doc)
        save_intel(output_path, doc)
        write_json(graph_path, graph)
        report = {
            "stage": "INITIAL_ACCESS",
            "agent": self.name,
            "timestamp": utc_now(),
            "input": recon_intel,
            "output": output_path,
            "attack_graph": graph_path,
            "asset_count": len(doc.assets),
            "edge_count": len(doc.edges),
            "api_endpoint_count": len(doc.api_surface),
            "candidate_action_count": len(doc.candidate_actions),
            "recommended_followup_modules": [item.action_type for item in doc.candidate_actions],
            "simulated_only": True,
            "scope": doc.safety.get("scope"),
        }
        write_json(os.path.join(self.output_dir, "stage_2_initial_access_report.json"), report)
        log(self.name, f"saved {output_path}, {graph_path}")
        return {"intel": asdict(doc), "attack_graph": graph, "report": report}

    def _attack_graph(self, doc: IntelDocument) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "created_at": utc_now(),
            "simulated_only": True,
            "scope": doc.safety.get("scope"),
            "source_intel": doc.environment.get("input_recon_intel"),
            "nodes": [asdict(asset) for asset in doc.assets],
            "edges": [asdict(edge) for edge in doc.edges],
            "weak_points": list(doc.gcs_model.weak_points),
            "trust_assumptions": list(doc.gcs_model.trust_assumptions),
            "recommended_followup_modules": [
                {
                    "action_id": action.action_id,
                    "agent": action.agent,
                    "action_type": action.action_type,
                    "reason": action.reason,
                    "params": action.params,
                    "expected_effect": action.expected_effect,
                    "risk": action.risk,
                    "confidence": action.confidence,
                }
                for action in doc.candidate_actions
            ],
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 2 InitialAccessAgent")
    parser.add_argument("--input", default="output/stage_1_recon.json")
    parser.add_argument("--output", default="output/stage_2_initial_access.json")
    parser.add_argument("--graph-output", default="output/stage_2_attack_graph.json")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args(argv)
    InitialAccessAgent(output_dir=args.output_dir).run(
        recon_intel=args.input,
        output=args.output,
        graph_output=args.graph_output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
