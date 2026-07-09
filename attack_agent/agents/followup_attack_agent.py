from __future__ import annotations

import argparse
import json
import math
import os
import time
import urllib.error
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


def _post_dashboard_event(payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps({
        "message_type": "attack_chain_step",
        "agent_type": "ATK",
        "source": "FollowUpAttackAgent",
        "simulated_only": True,
        "scope": "LOCAL_DOCKER_TESTBED_ONLY",
        **payload,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{load_config().dashboard_url.rstrip('/')}/api/agent-event",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def _reset_dashboard_failsafe() -> dict[str, Any]:
    req = urllib.request.Request(
        f"{load_config().dashboard_url.rstrip('/')}/api/reset-failsafe",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


def _uav(snapshot: dict[str, Any]) -> dict[str, Any]:
    for platform in snapshot.get("platforms", []) or []:
        if platform.get("platform_id") == "UAV-001":
            return platform
    return {}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlmb / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))


def _safe_gcs_dashboard() -> dict[str, Any]:
    """GCS 원본 텔레메트리 — dashboard failsafe_sim 오버레이가 적용되지 않은 실제 UAV 상태."""
    try:
        with urllib.request.urlopen(f"{load_config().gcs_url.rstrip('/')}/api/dashboard", timeout=3.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return {}


def _gcs_uav(snapshot: dict[str, Any]) -> dict[str, Any]:
    for platform in snapshot.get("platforms", []) or []:
        if platform.get("platform_id") == "UAV-001":
            return platform
    return {}


def _evaluate_failsafe_success(
    step: Any,
    result: dict[str, Any],
    live_after: dict[str, Any],
    gcs_before: dict[str, Any],
    gcs_after: dict[str, Any],
) -> dict[str, Any]:
    """공격이 유발한 dashboard failsafe_sim이 아니라, 실제 텔레메트리로 유발 성공을 판정한다.
    - loss_pct : /api/live platform.ticn (failsafe_sim 오버레이가 안 덮음 = 실제 라우터 값)
    - alt/pos  : GCS /api/dashboard (오버레이 미적용 = mock_uav 원본)
    """
    if result.get("status") in {"failed", "unsupported_adapter"}:
        return {"success": False, "reason": result.get("error", result.get("status", "step failed")), "real_signal": "none"}

    forwarded = result.get("forwarded_alert") is True
    loss_after = _num((_uav(live_after).get("ticn") or {}).get("loss_pct")) or 0.0

    alt_before = _num(gcs_before.get("alt"))
    alt_after = _num(gcs_after.get("alt"))
    alt_drop = (alt_before - alt_after) if (alt_before is not None and alt_after is not None) else None
    airborne = alt_after is not None and alt_after > 50.0

    disp_m: float | None = None
    b_lat, b_lon = _num(gcs_before.get("lat")), _num(gcs_before.get("lon"))
    a_lat, a_lon = _num(gcs_after.get("lat")), _num(gcs_after.get("lon"))
    if None not in (b_lat, b_lon, a_lat, a_lon):
        disp_m = _haversine_m(b_lat, b_lon, a_lat, a_lon)

    action = step.action_type
    if action == "MAVLINK_COMMAND_INJECTION":  # A-3: 실제 고도 하강 = 강제 착륙 유발
        success = alt_drop is not None and alt_drop >= 50.0
        reason = (f"실제 고도 {alt_before}→{alt_after}m 하강 = LAND 유발 확인" if success
                  else "실제 고도 하강 없음 — 주입 미도달/무효")
        evidence = {"alt_before": alt_before, "alt_after": alt_after, "alt_drop_m": alt_drop}
    elif action in {"EW_LINK_DEGRADATION_SIM", "EW_STEALTH_DEGRADATION_SIM"}:  # B-1/B-2: 실제 손실률
        target = _num(step.params.get("target_loss_pct")) or 15.0
        success = loss_after >= target
        reason = (f"실제 ticn.loss_pct={loss_after}% ≥ {target}% = 링크 저하 유발" if success
                  else f"실제 손실률 {loss_after}% < {target}% — TMMR 홉 회피됨")
        evidence = {"loss_pct_after": loss_after, "target_pct": target}
    elif action in {"MAVLINK_STATUS_SPOOF", "HB_TIMEOUT_INDUCTION"}:  # A-1/A-2: LOITER = 위치 유지(휴리스틱)
        success = bool(airborne and disp_m is not None and disp_m < 80.0)
        reason = (f"UAV 위치 유지(이동 {round(disp_m, 1)}m) = LOITER 관측" if success
                  else "LOITER 미관측 — mock_uav가 모드를 텔레메트리로 노출 안 함(약한 신호)")
        evidence = {"displacement_m": None if disp_m is None else round(disp_m, 1), "alt_after": alt_after}
    else:
        success = False
        reason = "unknown action_type"
        evidence = {}

    return {
        "success": bool(success),
        "reason": reason,
        "forwarded_alert": forwarded,
        "real_signal": evidence,
        "basis": "GCS 원본 텔레메트리 + 실제 loss_pct (공격이 켠 failsafe_sim 미사용)",
    }


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
        step_delay_sec: float = 0.0,
        verification_delay_sec: float = 3.0,
        plan_output: str | None = None,
        report_output: str | None = None,
    ) -> dict[str, Any]:
        plan_path = plan_output or os.path.join(self.output_dir, "stage_3_attack_plan.json")
        report_path = report_output or os.path.join(self.output_dir, "stage_3_execution_report.json")
        doc = load_intel(initial_access_intel)
        plan = build_attack_plan(doc, objective=objective, max_steps=max_steps)
        plan.safety_mode = "DRY_RUN" if dry_run else "EXPLICIT_LAB_EXECUTION"
        write_json(plan_path, plan)

        reset_result = None
        if not dry_run and objective == "FAILSAFE_INDUCTION":
            reset_result = _reset_dashboard_failsafe()

        before = _safe_dashboard_live()
        results: list[dict[str, Any]] = []
        adapters = default_adapters()
        selected_steps = plan.steps[:max_steps]
        successful_step_id: str | None = None
        for index, step in enumerate(selected_steps):
            step_before = _safe_dashboard_live()
            gcs_before = _gcs_uav(_safe_gcs_dashboard())
            if not dry_run:
                _post_dashboard_event({
                    "status": "STEP_STARTED",
                    "message": f"{step.step_id} fallback attack attempt started",
                    "detail": f"{step.action_type} | {step.reason}",
                    "evidence": {"step_id": step.step_id, "action_id": step.action_id, "action_type": step.action_type},
                })
            adapter = next((item for item in adapters if item.supports(step)), None)
            if adapter is None:
                result = {"step_id": step.step_id, "status": "unsupported_adapter", "agent": step.agent}
                results.append(result)
                if not dry_run:
                    _post_dashboard_event({
                        "status": "STEP_FAILED",
                        "message": f"{step.step_id} failed, trying next fallback",
                        "detail": f"{step.action_type} unsupported adapter",
                        "evidence": result,
                    })
                continue
            try:
                result = adapter.dry_run(step, doc) if dry_run else adapter.execute(step, doc)
            except Exception as exc:
                result = {"step_id": step.step_id, "adapter": adapter.name, "status": "failed", "error": str(exc)}
                results.append(result)
                if not dry_run:
                    _post_dashboard_event({
                        "status": "STEP_FAILED",
                        "message": f"{step.step_id} failed, trying next fallback",
                        "detail": f"{step.action_type} error={exc}",
                        "evidence": result,
                    })
                if step_delay_sec > 0 and index < len(selected_steps) - 1:
                    time.sleep(step_delay_sec)
                continue
            if not dry_run and verification_delay_sec > 0:
                time.sleep(verification_delay_sec)
            step_after = _safe_dashboard_live()
            gcs_after = _gcs_uav(_safe_gcs_dashboard())
            verification = (
                _evaluate_failsafe_success(step, result, step_after, gcs_before, gcs_after)
                if objective == "FAILSAFE_INDUCTION"
                else {"success": result.get("status") != "failed", "reason": "step executed"}
            )
            result["verification"] = verification
            results.append(result)
            if not dry_run and verification.get("success"):
                successful_step_id = step.step_id
                _post_dashboard_event({
                    "status": "STEP_SUCCEEDED",
                    "message": f"{step.step_id} succeeded, fallback chain stopped",
                    "detail": f"{step.action_type} | {verification.get('reason')}",
                    "evidence": {"step_id": step.step_id, "action_id": step.action_id, "verification": verification},
                })
                break
            if not dry_run:
                _post_dashboard_event({
                    "status": "STEP_FAILED",
                    "message": f"{step.step_id} did not meet success condition, trying next fallback",
                    "detail": f"{step.action_type} | {verification.get('reason')}",
                    "evidence": {"step_id": step.step_id, "action_id": step.action_id, "verification": verification},
                })
            if step_delay_sec > 0 and index < len(selected_steps) - 1:
                time.sleep(step_delay_sec)
        after = _safe_dashboard_live()
        if not dry_run:
            _post_dashboard_event({
                "status": "CHAIN_COMPLETE" if successful_step_id else "CHAIN_EXHAUSTED",
                "message": "fallback attack chain complete" if successful_step_id else "fallback attack chain exhausted",
                "detail": f"successful_step={successful_step_id or 'none'} results={len(results)}/{len(selected_steps)}",
                "evidence": {"successful_step_id": successful_step_id, "result_count": len(results)},
            })

        report = {
            "stage": "FOLLOW_UP_ATTACK",
            "agent": self.name,
            "timestamp": utc_now(),
            "input_initial_access_intel": initial_access_intel,
            "objective": objective,
            "dry_run": dry_run,
            "step_delay_sec": step_delay_sec,
            "verification_delay_sec": verification_delay_sec,
            "chain_strategy": "fallback_until_success" if objective == "FAILSAFE_INDUCTION" else "sequential",
            "successful_step_id": successful_step_id,
            "reset_failsafe_before_run": reset_result,
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
    parser.add_argument("--step-delay-sec", type=float, default=0.0)
    parser.add_argument("--verification-delay-sec", type=float, default=3.0)
    parser.add_argument("--plan-output", default="output/stage_3_attack_plan.json")
    parser.add_argument("--report-output", default="output/stage_3_execution_report.json")
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args(argv)
    FollowUpAttackAgent(output_dir=args.output_dir).run(
        initial_access_intel=args.input,
        objective=args.objective,
        dry_run=not args.execute,
        max_steps=args.max_steps,
        step_delay_sec=args.step_delay_sec,
        verification_delay_sec=args.verification_delay_sec,
        plan_output=args.plan_output,
        report_output=args.report_output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
