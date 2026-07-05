from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from attack_agent.core.schemas import AttackStep, IntelDocument


class AgentAdapter(ABC):
    name: str

    @abstractmethod
    def supports(self, step: AttackStep) -> bool:
        raise NotImplementedError

    @abstractmethod
    def build_command(self, step: AttackStep, intel: IntelDocument) -> list[str] | dict[str, Any]:
        raise NotImplementedError

    def dry_run(self, step: AttackStep, intel: IntelDocument) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "step_id": step.step_id,
            "dry_run": True,
            "command": self.build_command(step, intel),
            "reason": step.reason,
            "expected_effect": step.expected_effect,
        }

    @abstractmethod
    def execute(self, step: AttackStep, intel: IntelDocument) -> dict[str, Any]:
        raise NotImplementedError

