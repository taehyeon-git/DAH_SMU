from __future__ import annotations

from attack_agent.core.logging_utils import utc_now
from attack_agent.core.schemas import AttackPlan, AttackStep, CandidateAction, IntelDocument


def _missing_params(action: CandidateAction) -> list[str]:
    missing: list[str] = []
    for name in action.required_params:
        value = action.params.get(name)
        if value is None or value == "" or value == []:
            missing.append(name)
    return missing


def _score(action: CandidateAction, objective: str | None = None) -> float:
    score = float(action.confidence)
    if action.risk == "LOW":
        score += 0.12
    elif action.risk == "HIGH":
        score -= 0.05
    if not _missing_params(action):
        score += 0.2
    if objective == "FAILSAFE_INDUCTION" and action.action_type == "EW_LINK_DEGRADATION_SIM":
        score += 0.25
    if objective == "PROTOCOL_INTEGRITY_TEST" and action.action_type == "PROTOCOL_FRAME_INTEGRITY_SIM":
        score += 0.35
    if action.dry_run_supported:
        score += 0.08
    return score


def build_attack_plan(doc: IntelDocument, objective: str | None = None, max_steps: int = 4) -> AttackPlan:
    candidates = [
        action for action in doc.candidate_actions
        if not _missing_params(action) or action.action_type == "PROTOCOL_FRAME_INTEGRITY_SIM"
    ]
    candidates.sort(key=lambda action: _score(action, objective), reverse=True)
    steps: list[AttackStep] = []
    for idx, action in enumerate(candidates[:max_steps], start=1):
        steps.append(AttackStep(
            step_id=f"STEP-{idx:03d}",
            action_id=action.action_id,
            agent=action.agent,
            action_type=action.action_type,
            reason=action.reason,
            params=action.params,
            expected_effect=action.expected_effect,
            dry_run=True,
        ))
    return AttackPlan(
        plan_id=f"PLAN-{utc_now()}",
        steps=steps,
        rollback_or_stop_conditions=[
            "target unavailable",
            "safety allowlist validation failed",
            "required parameter missing",
            "execution flag not enabled",
            "defense-agent reports critical response",
        ],
        verification=[
            "check dashboard /api/live",
            "check agent_events for planned simulated action",
            "compare before/after mission_state and link metrics",
        ],
        safety_mode="DRY_RUN",
    )
