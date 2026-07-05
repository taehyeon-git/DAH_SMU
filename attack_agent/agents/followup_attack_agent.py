from __future__ import annotations

import argparse
import json
import os
import urllib.request
from dataclasses import asdict
from typing import Any

from attack_agent.adapters import default_adapters
from attack_agent.core.config import load_config
from attack_agent.core.io import write_json
from attack_agent.core.logging_utils import log, utc_now
from attack_agent.core.schemas import load_intel
from attack_agent.planner.plan_builder import build_attack_plan


def _safe_dashboard_live() -> dict[str, Any]:
    try:
        with urllib.request.urlopen(f"{load_config().dashboard_url.rstrip('/')}/api/live", timeout=3.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


class FollowUpAttackAgent:
    """Stage 3 agent: build and execute safe follow-up simulation plans."""

    name = "FollowUpAttackAgent"

    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir

    def run(
        self,
        initial_access_intel: str = "output/stage_2_initial_access.json",
        objective: str | None = None,
        dry_run: bool = True,
        max_steps: int = 1,
        plan_output: str | None = None,
        report_output: str | None = None,
    ) -> dict[str, Any]:
        plan_path = plan_output or os.path.join(self.output_dir, "stage_3_attack_plan.json")
        report_path = report_output or os.path.join(self.output_dir, "stage_3_execution_report.json")
        doc = load_intel(initial_access_intel)
        plan = build_attack_plan(doc, objective=objective, max_steps=max_steps)
        plan.safety_mode = "DRY_RUN" if dry_run else "EXPLICIT_LAB_EXECUTION"
        write_json(plan_path, plan)

        before = _safe_dashboard_live()
        results: list[dict[str, Any]] = []
        adapters = default_adapters()
        for step in plan.steps[:max_steps]:
            adapter = next((item for item in adapters if item.supports(step)), None)
            if adapter is None:
                results.append({"step_id": step.step_id, "status": "unsupported_adapter", "agent": step.agent})
                break
            try:
                result = adapter.dry_run(step, doc) if dry_run else adapter.execute(step, doc)
            except Exception as exc:
                result = {"step_id": step.step_id, "adapter": adapter.name, "status": "failed", "error": str(exc)}
                results.append(result)
                break
            results.append(result)
            if not dry_run and result.get("status") == "failed":
                break
        after = _safe_dashboard_live()

        report = {
            "stage": "FOLLOW_UP_ATTACK",
            "agent": self.name,
            "timestamp": utc_now(),
            "input_initial_access_intel": initial_access_intel,
            "objective": objective,
            "dry_run": dry_run,
            "simulated_only": True,
            "scope": doc.safety.get("scope"),
            "attack_plan": plan_path,
            "plan_summary": {
                "plan_id": plan.plan_id,
                "step_count": len(plan.steps),
                "steps": [asdict(step) for step in plan.steps],
            },
            "before_summary": before,
            "after_summary": after,
            "execution_results": results,
            "verification": {
                "dashboard_status_before": before.get("status"),
                "dashboard_status_after": after.get("status"),
                "agent_event_count_before": len(before.get("agent_events", [])),
                "agent_event_count_after": len(after.get("agent_events", [])),
                "recommendation_change": {
                    "before": (before.get("mission_state") or {}).get("phase"),
                    "after": (after.get("mission_state") or {}).get("phase"),
                },
            },
        }
        write_json(report_path, report)
        log(self.name, f"saved {plan_path}, {report_path}")
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 3 FollowUpAttackAgent")
    parser.add_argument("--input", default="output/stage_2_initial_access.json")
    parser.add_argument("--objective", default=None, choices=[None, "FAILSAFE_INDUCTION", "PROTOCOL_INTEGRITY_TEST"], nargs="?")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-steps", type=int, default=1)
    parser.add_argument("--plan-output", default="output/stage_3_attack_plan.json")
    parser.add_argument("--report-output", default="output/stage_3_execution_report.json")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args(argv)
    FollowUpAttackAgent(output_dir=args.output_dir).run(
        initial_access_intel=args.input,
        objective=args.objective,
        dry_run=not args.execute,
        max_steps=args.max_steps,
        plan_output=args.plan_output,
        report_output=args.report_output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
