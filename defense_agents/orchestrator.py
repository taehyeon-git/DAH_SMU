from __future__ import annotations

import argparse
import signal
import threading
import time

from .detection_agent import DefenseDetectionAgent
from .policy_agent import DefensePolicyAgent
from .recovery_agent import DefenseRecoveryAgent
from .response_agent import DefenseResponseAgent
from .shared.event_bus import DefenseContext
from .shared.models import IdFactory


class DefenseOrchestrator:
    """Run the four DAH_SMU defense agents as one blue-team service."""

    source = "DEFENSE-ORCHESTRATOR"

    def __init__(self, policy_path: str | None = None) -> None:
        self.context = DefenseContext()
        self.ids = IdFactory()
        self.policy_agent = DefensePolicyAgent(self.context, policy_path=policy_path)
        self.detection_agent = DefenseDetectionAgent(self.context, self.ids)
        self.response_agent = DefenseResponseAgent(self.context, self.ids)
        self.recovery_agent = DefenseRecoveryAgent(self.context, self.ids)
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self.policy_agent.load()
        self._threads.extend(self.detection_agent.start())
        response_thread = threading.Thread(target=self.response_agent.run, name="def-response-agent", daemon=True)
        recovery_thread = threading.Thread(target=self.recovery_agent.run, name="def-recovery-agent", daemon=True)
        response_thread.start()
        recovery_thread.start()
        self._threads.extend([response_thread, recovery_thread])
        self.context.event_bus.send(
            self.source,
            "4-Agent 방어 체계 실행 중",
            level="info",
            detail="policy/detection/response/recovery agents active",
            status="OK",
        )

    def run_forever(self, heartbeat_interval_s: int = 10) -> None:
        self.start()
        while self.context.running:
            time.sleep(heartbeat_interval_s)
            self.context.event_bus.send(
                self.source,
                "4-Agent 방어 체계 실행 중",
                level="info",
                detail=(
                    f"policy_loaded={bool(self.context.policy)} "
                    f"threat_queue={self.context.threat_queue.qsize()} "
                    f"action_queue={self.context.action_queue.qsize()}"
                ),
                status="OK",
            )

    def run_once(self) -> None:
        """Non-blocking sanity mode used by tests and manual health checks."""
        self.policy_agent.load()
        self.recovery_agent.write_reports()
        self.context.event_bus.send(
            self.source,
            "4-Agent 방어 체계 점검 완료",
            level="info",
            detail="policy loaded, report writers verified, long-running monitors not started",
            status="OK",
        )

    def stop(self) -> None:
        self.context.running = False
        self.context.event_bus.send(self.source, "방어 체계 종료", level="info", detail="graceful stop requested", status="OK")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DAH_SMU four-agent defense orchestrator")
    parser.add_argument("--policy", default=None, help="Path to defense policy JSON")
    parser.add_argument("--once", action="store_true", help="Load policy and write reports without starting infinite monitors")
    parser.add_argument("--heartbeat-interval-s", type=int, default=10)
    args = parser.parse_args(argv)

    orchestrator = DefenseOrchestrator(policy_path=args.policy)

    def _handle_stop(signum, frame):  # noqa: ANN001
        orchestrator.stop()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    if args.once:
        orchestrator.run_once()
        return 0

    orchestrator.run_forever(heartbeat_interval_s=args.heartbeat_interval_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

