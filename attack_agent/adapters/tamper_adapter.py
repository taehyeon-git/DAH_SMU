from __future__ import annotations

from typing import Any

from attack_agent.adapters.base import AgentAdapter
from attack_agent.core.schemas import AttackStep, IntelDocument
from attack_agent.tamper.lab_injector import inject_or_dry_run
from attack_agent.tamper.mutations import apply_mutation
from attack_agent.tamper.packet_model import synthetic_mavlink_like_packet


class TamperAdapter(AgentAdapter):
    name = "tamper"

    def supports(self, step: AttackStep) -> bool:
        return step.agent == self.name or step.action_type == "PROTOCOL_FRAME_INTEGRITY_SIM"

    def build_command(self, step: AttackStep, intel: IntelDocument) -> dict[str, Any]:
        return {
            "transport": "synthetic-lab-packet",
            "mutation": step.params.get("mutation", "FRAME_CRC_BREAK"),
            "dst_host": step.params.get("dst_host", "localhost"),
            "dst_port": int(step.params.get("dst_port", 14550)),
            "protocol": step.params.get("protocol", "MAVLink-like"),
        }

    def dry_run(self, step: AttackStep, intel: IntelDocument) -> dict[str, Any]:
        cmd = self.build_command(step, intel)
        packet = synthetic_mavlink_like_packet(dst_host=cmd["dst_host"], dst_port=cmd["dst_port"])
        mutated = apply_mutation(packet, cmd["mutation"])
        return inject_or_dry_run(mutated, dry_run=True)

    def execute(self, step: AttackStep, intel: IntelDocument) -> dict[str, Any]:
        cmd = self.build_command(step, intel)
        packet = synthetic_mavlink_like_packet(dst_host=cmd["dst_host"], dst_port=cmd["dst_port"])
        mutated = apply_mutation(packet, cmd["mutation"])
        return inject_or_dry_run(mutated, dry_run=False)

