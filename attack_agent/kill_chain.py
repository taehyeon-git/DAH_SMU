from __future__ import annotations

import argparse

from attack_agent.agents import FollowUpAttackAgent, InitialAccessAgent, ReconAgent
from attack_agent.core.logging_utils import log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DAH_SMU three-agent kill-chain controller")
    parser.add_argument("--stage", choices=["recon", "initial-access", "follow-up", "all"], default="all")
    parser.add_argument("--source", default="output/intel_handoff.json")
    parser.add_argument("--passive-source", default="output/passive_mavlink_intel.json")
    parser.add_argument("--recon-output", default="output/stage_1_recon.json")
    parser.add_argument("--initial-access-output", default="output/stage_2_initial_access.json")
    parser.add_argument("--attack-graph-output", default="output/stage_2_attack_graph.json")
    parser.add_argument("--objective", default=None, choices=[None, "FAILSAFE_INDUCTION", "PROTOCOL_INTEGRITY_TEST"], nargs="?")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-steps", type=int, default=1)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--skip-recon-collection", action="store_true", help="ReconAgent가 수집을 실행하지 않고 기존 JSON만 정규화")
    parser.add_argument("--recon-collection-mode", choices=["auto", "docker", "local"], default="auto")
    parser.add_argument("--recon-listen-host", default="0.0.0.0")
    parser.add_argument("--recon-listen-port", type=int, default=14550)
    parser.add_argument("--recon-duration-s", type=int, default=30)
    parser.add_argument("--recon-revalidate-s", type=int, default=20)
    parser.add_argument("--recon-prediction-horizon-s", type=int, default=60)
    args = parser.parse_args(argv)

    if args.stage in {"recon", "all"}:
        ReconAgent(output_dir=args.output_dir).run(
            source=args.source,
            passive_source=args.passive_source,
            output=args.recon_output,
            collect=not args.skip_recon_collection,
            collection_mode=args.recon_collection_mode,
            listen_host=args.recon_listen_host,
            listen_port=args.recon_listen_port,
            duration_s=args.recon_duration_s,
            revalidate_s=args.recon_revalidate_s,
            prediction_horizon_s=args.recon_prediction_horizon_s,
        )

    if args.stage in {"initial-access", "all"}:
        InitialAccessAgent(output_dir=args.output_dir).run(
            recon_intel=args.recon_output,
            output=args.initial_access_output,
            graph_output=args.attack_graph_output,
        )

    if args.stage in {"follow-up", "all"}:
        FollowUpAttackAgent(output_dir=args.output_dir).run(
            initial_access_intel=args.initial_access_output,
            objective=args.objective,
            dry_run=not args.execute,
            max_steps=args.max_steps,
        )

    log("KILL-CHAIN", f"stage={args.stage} mode={'EXECUTE' if args.execute else 'DRY-RUN'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
