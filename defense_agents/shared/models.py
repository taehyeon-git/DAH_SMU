from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


LAB_SCOPE = "LOCAL_DOCKER_TESTBED_ONLY"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_time() -> str:
    return datetime.now().strftime("%H:%M:%S")


@dataclass
class Threat:
    threat_id: str
    scenario: str
    severity: str
    confidence: float
    source: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    recommended_playbook: str = "IGNORE_AND_MONITOR"
    created_at: str = field(default_factory=utc_now)
    simulated_only: bool = True
    scope: str = LAB_SCOPE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DefenseAction:
    action_id: str
    threat_id: str
    scenario: str
    playbook: str
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    simulated_only: bool = True
    scope: str = LAB_SCOPE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RecoveryObservation:
    incident_id: str
    status: str
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TimelineEvent:
    time: str
    agent: str
    event: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class IdFactory:
    def __init__(self) -> None:
        self._threat = 0
        self._action = 0
        self._incident = 0

    def threat_id(self) -> str:
        self._threat += 1
        return f"THREAT-{datetime.now():%Y%m%d}-{self._threat:04d}"

    def action_id(self) -> str:
        self._action += 1
        return f"ACTION-{datetime.now():%Y%m%d}-{self._action:04d}"

    def incident_id(self) -> str:
        self._incident += 1
        return f"INC-{datetime.now():%Y%m%d}-{self._incident:04d}"

