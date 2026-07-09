from __future__ import annotations

import os
from typing import Any

from .shared.event_bus import DefenseContext
from .shared.policy_loader import load_policy, summarize_policy
from .shared.utils import http_post_json


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
        self._apply_prevention_controls(policy)
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

    def _apply_prevention_controls(self, policy: dict[str, Any]) -> None:
        if os.getenv("DEFENSE_PREVENTION_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
            self.context.event_bus.send(
                self.source,
                "방어 예방 게이트 비활성화",
                level="warn",
                detail="DEFENSE_PREVENTION_ENABLED=false",
                status="ALERT",
            )
            return

        dashboard_url = f"http://{os.getenv('DASHBOARD_HOST', 'dah-dashboard')}:8080"
        router_url = f"http://{os.getenv('ROUTER_HOST', 'dah-tactical-router')}:{os.getenv('ROUTER_PORT', '8080')}"
        uav_host = policy.get("assets", {}).get("uav", {}).get("host") or os.getenv("UAV_HOST", "172.31.50.10")
        uav_url = f"http://{uav_host}:{os.getenv('UAV_DEFENSE_PORT', '8080')}"
        ttl_sec = int(os.getenv("DEFENSE_RULE_TTL_SEC", "3600"))

        common = {
            "enabled": True,
            "ttl_sec": ttl_sec,
            "source": self.source,
            "reason": "Defense Policy Agent baseline prevention gate",
        }
        restricted = policy.get("restricted_commands", [])
        allowed_ids = policy.get("allowed_sys_ids", [255])

        results = {
            "dashboard": http_post_json(
                f"{dashboard_url}/api/defense/rules",
                {
                    **common,
                    "block_attack_events": True,
                    "block_failsafe_overlay": True,
                    "block_protocol_alerts": True,
                },
            ),
            "router": http_post_json(
                f"{router_url}/api/defense/rules",
                {
                    **common,
                    "block_jam_events": True,
                    "block_delay_events": True,
                },
            ),
            "uav": http_post_json(
                f"{uav_url}/api/defense/rules",
                {
                    **common,
                    "block_unsafe_commands": True,
                    "block_spoofed_heartbeat": True,
                    "allowed_sys_ids": allowed_ids,
                    "restricted_commands": restricted,
                },
            ),
        }
        reached = [name for name, result in results.items() if result]
        self.context.event_bus.send(
            self.source,
            "방어 예방 게이트 적용",
            level="info" if reached else "warn",
            detail=f"reached={reached or 'none'} ttl_sec={ttl_sec}",
            status="OK" if reached else "ALERT",
        )

