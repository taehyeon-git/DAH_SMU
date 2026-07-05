from __future__ import annotations

import argparse
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any

from attack_agent.core.config import running_inside_docker
from attack_agent.core.io import read_json, write_json
from attack_agent.core.logging_utils import log, utc_now
from attack_agent.core.schemas import IntelDocument, load_intel, save_intel


class ReconAgent:
    """Stage 1 agent: convert recon artifacts into machine-readable intel."""

    name = "ReconAgent"

    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir

    def run(
        self,
        source: str = "output/intel_handoff.json",
        passive_source: str = "output/passive_mavlink_intel.json",
        output: str | None = None,
        collect: bool = False,
        collection_mode: str = "auto",
        listen_host: str = "0.0.0.0",
        listen_port: int = 14550,
        duration_s: int = 30,
        revalidate_s: int = 20,
        prediction_horizon_s: int = 60,
    ) -> dict[str, Any]:
        collection_report: dict[str, Any] | None = None
        if collect:
            collection_report = self.collect(
                source=source,
                passive_source=passive_source,
                mode=collection_mode,
                listen_host=listen_host,
                listen_port=listen_port,
                duration_s=duration_s,
                revalidate_s=revalidate_s,
                prediction_horizon_s=prediction_horizon_s,
            )

        output_path = output or os.path.join(self.output_dir, "stage_1_recon.json")
        doc = load_intel(source) if os.path.exists(source) else IntelDocument(source="recon_agent_empty_input")
        doc.source = source if os.path.exists(source) else doc.source
        doc.environment.update({
            "stage": "RECON",
            "agent": self.name,
            "collection_executed": bool(collection_report),
            "collection_report": collection_report,
            "source_recon_file": source if os.path.exists(source) else None,
            "passive_recon_file": passive_source if os.path.exists(passive_source) else None,
        })
        doc.observations.append({
            "type": "stage_transition",
            "stage": "RECON",
            "agent": self.name,
            "created_at": utc_now(),
            "summary": "Recon artifacts normalized for InitialAccessAgent.",
        })
        if os.path.exists(passive_source):
            passive = read_json(passive_source, {})
            doc.observations.append({
                "type": "passive_recon_summary",
                "source": passive_source,
                "collection_summary": passive.get("collection_summary", {}),
                "target": passive.get("target", {}),
            })
        save_intel(output_path, doc)
        report = {
            "stage": "RECON",
            "agent": self.name,
            "timestamp": utc_now(),
            "source": source,
            "passive_source": passive_source,
            "output": output_path,
            "collection": collection_report,
            "asset_count": len(doc.assets),
            "candidate_action_count": len(doc.candidate_actions),
            "simulated_only": True,
            "scope": doc.safety.get("scope"),
        }
        write_json(os.path.join(self.output_dir, "stage_1_recon_report.json"), report)
        log(self.name, f"saved {output_path}")
        return {"intel": asdict(doc), "report": report}

    def collect(
        self,
        source: str,
        passive_source: str,
        mode: str = "auto",
        listen_host: str = "0.0.0.0",
        listen_port: int = 14550,
        duration_s: int = 30,
        revalidate_s: int = 20,
        prediction_horizon_s: int = 60,
    ) -> dict[str, Any]:
        """Run every recon collection event before normalizing stage output."""
        selected_mode = self._select_collection_mode(mode)
        os.makedirs(self.output_dir, exist_ok=True)
        started_at = utc_now()

        if selected_mode == "docker":
            return self._collect_with_docker(
                source=source,
                passive_source=passive_source,
                duration_s=duration_s,
                revalidate_s=revalidate_s,
                prediction_horizon_s=prediction_horizon_s,
                started_at=started_at,
            )

        return self._collect_locally(
            source=source,
            passive_source=passive_source,
            listen_host=listen_host,
            listen_port=listen_port,
            duration_s=duration_s,
            revalidate_s=revalidate_s,
            prediction_horizon_s=prediction_horizon_s,
            started_at=started_at,
        )

    def _select_collection_mode(self, mode: str) -> str:
        if mode not in {"auto", "docker", "local"}:
            raise ValueError(f"unknown recon collection mode: {mode}")
        if mode != "auto":
            return mode
        if running_inside_docker():
            return "local"
        if Path("docker-compose.yml").exists() and os.path.normpath(self.output_dir) == "output":
            return "docker"
        return "local"

    def _collect_with_docker(
        self,
        source: str,
        passive_source: str,
        duration_s: int,
        revalidate_s: int,
        prediction_horizon_s: int,
        started_at: str,
    ) -> dict[str, Any]:
        env = {
            **os.environ,
            "RECON_DURATION_S": str(duration_s),
            "RECON_REVALIDATE_S": str(revalidate_s),
            "RECON_PREDICTION_HORIZON_S": str(prediction_horizon_s),
        }
        commands = [
            ["docker", "compose", "rm", "-f", "dah-recon"],
            ["docker", "compose", "--profile", "recon-lab", "up", "--build", "--no-deps", "dah-recon"],
        ]
        outputs: list[dict[str, Any]] = []
        for command in commands:
            proc = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
            outputs.append({
                "command": " ".join(command),
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-4000:],
                "stderr_tail": proc.stderr[-4000:],
            })
            if proc.returncode != 0:
                return {
                    "mode": "docker",
                    "status": "failed",
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "source": source,
                    "passive_source": passive_source,
                    "commands": outputs,
                }
        return {
            "mode": "docker",
            "status": "ok" if os.path.exists(source) and os.path.exists(passive_source) else "missing_output",
            "started_at": started_at,
            "finished_at": utc_now(),
            "source": source,
            "passive_source": passive_source,
            "commands": outputs,
        }

    def _collect_locally(
        self,
        source: str,
        passive_source: str,
        listen_host: str,
        listen_port: int,
        duration_s: int,
        revalidate_s: int,
        prediction_horizon_s: int,
        started_at: str,
    ) -> dict[str, Any]:
        from attack_agent import recon as recon_collector

        recon_collector.run(
            listen_host=listen_host,
            listen_port=listen_port,
            duration_s=duration_s,
            revalidate_s=revalidate_s,
            prediction_horizon_s=prediction_horizon_s,
            output_path=passive_source,
            chain_handoff_path=source,
        )
        return {
            "mode": "local",
            "status": "ok" if os.path.exists(source) and os.path.exists(passive_source) else "missing_output",
            "started_at": started_at,
            "finished_at": utc_now(),
            "source": source,
            "passive_source": passive_source,
            "listen": f"{listen_host}:{listen_port}",
            "duration_s": duration_s,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 1 ReconAgent")
    parser.add_argument("--source", default="output/intel_handoff.json")
    parser.add_argument("--passive-source", default="output/passive_mavlink_intel.json")
    parser.add_argument("--output", default="output/stage_1_recon.json")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--skip-collection", action="store_true", help="기존 정찰 JSON만 정규화")
    parser.add_argument("--collection-mode", choices=["auto", "docker", "local"], default="auto")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=14550)
    parser.add_argument("--duration-s", type=int, default=30)
    parser.add_argument("--revalidate-s", type=int, default=20)
    parser.add_argument("--prediction-horizon-s", type=int, default=60)
    args = parser.parse_args(argv)
    ReconAgent(output_dir=args.output_dir).run(
        source=args.source,
        passive_source=args.passive_source,
        output=args.output,
        collect=not args.skip_collection,
        collection_mode=args.collection_mode,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        duration_s=args.duration_s,
        revalidate_s=args.revalidate_s,
        prediction_horizon_s=args.prediction_horizon_s,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
