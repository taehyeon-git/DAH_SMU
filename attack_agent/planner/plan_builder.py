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


# fail-safe를 실제로 유발하는 후속공격 액션 타입 (gcs_reconstructor가 도출)
FAILSAFE_CHAIN_ORDER = [
    "MAVLINK_STATUS_SPOOF",        # A-1 heartbeat 상태 위조
    "HB_TIMEOUT_INDUCTION",        # A-2 heartbeat 두절
    "MAVLINK_COMMAND_INJECTION",   # A-3 위조 명령 주입 (LANDING 등)
    "EW_LINK_DEGRADATION_SIM",     # B-1 EW 재밍 (3채널)
    "EW_STEALTH_DEGRADATION_SIM",  # B-2 임계격차 은신 재밍
]
FAILSAFE_ACTION_TYPES = set(FAILSAFE_CHAIN_ORDER)

# 심각도 가산 — 강제착륙(추락)이 가장 강함
_SEVERITY_BONUS = {
    "MAVLINK_COMMAND_INJECTION": 0.10,
    "MAVLINK_STATUS_SPOOF": 0.04,
}


def _score(action: CandidateAction, objective: str | None = None) -> float:
    score = float(action.confidence)
    if action.risk == "LOW":
        score += 0.12
    elif action.risk == "HIGH":
        score -= 0.05
    if not _missing_params(action):
        score += 0.2
    if objective == "FAILSAFE_INDUCTION" and action.action_type in FAILSAFE_ACTION_TYPES:
        score += 0.25
        score += _SEVERITY_BONUS.get(action.action_type, 0.0)
    if objective == "PROTOCOL_INTEGRITY_TEST" and action.action_type == "PROTOCOL_FRAME_INTEGRITY_SIM":
        score += 0.35
    if action.dry_run_supported:
        score += 0.08
    return score


def _failsafe_chain_rank(action: CandidateAction) -> int:
    try:
        return FAILSAFE_CHAIN_ORDER.index(action.action_type)
    except ValueError:
        return len(FAILSAFE_CHAIN_ORDER)


def build_attack_plan(doc: IntelDocument, objective: str | None = None, max_steps: int = 4) -> AttackPlan:
    candidates = [
        action for action in doc.candidate_actions
        if not _missing_params(action) or action.action_type == "PROTOCOL_FRAME_INTEGRITY_SIM"
    ]
    if objective == "FAILSAFE_INDUCTION":
        candidates = [action for action in candidates if action.action_type in FAILSAFE_ACTION_TYPES]
        candidates.sort(key=lambda action: (_failsafe_chain_rank(action), -_score(action, objective)))
    else:
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
