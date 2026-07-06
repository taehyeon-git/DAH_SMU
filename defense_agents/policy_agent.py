from __future__ import annotations

import os
from typing import Any

from .shared.event_bus import DefenseContext
from .shared.policy_loader import load_policy, summarize_policy


class DefensePolicyAgent:
    """Prevention agent: load and publish the blue-team baseline policy."""

    source = "POLICY-AGENT"

    def __init__(self, context: DefenseContext, policy_path: str | None = None) -> None:
        self.context = context
        self.policy_path = policy_path

    def load(self) -> dict[str, Any]:
        policy = load_policy(self.policy_path)
        self.context.policy = policy
        self.context.event_bus.send(
            self.source,
            "방어 정책 기준선 로드 완료",
            level="info",
            detail=summarize_policy(policy),
            status="OK",
        )
        self._check_surface_policy(policy)
        return policy

    def _check_surface_policy(self, policy: dict[str, Any]) -> None:
        surfaces = policy.get("surfaces", {})
        warnings: list[str] = []

        if not surfaces.get("recon_mirror_default_allowed", False) and os.getenv("RECON_MIRROR_DEFAULT", "false").lower() == "true":
            warnings.append("Recon mirror가 기본 profile에서 활성화됨")
        if not surfaces.get("router_api_external_allowed", False) and os.getenv("ROUTER_API_EXTERNAL", "false").lower() == "true":
            warnings.append("Router API가 외부 노출로 설정됨")
        if surfaces.get("attack_event_port_lab_only", True) and os.getenv("ATTACK_EVENT_PORT_PROFILE", "lab").lower() != "lab":
            warnings.append("Attack event port가 lab-only profile 밖에서 열림")

        assets = policy.get("assets", {})
        if not assets.get("defense", {}).get("host") or not assets.get("uav", {}).get("host"):
            warnings.append("필수 방어/UAV 기준선 자산 정보 누락")

        for warning in warnings:
            self.context.event_bus.send(
                self.source,
                "방어 정책 표면 점검 경고",
                level="warn",
                detail=warning,
                status="ALERT",
            )

