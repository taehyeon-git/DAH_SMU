from __future__ import annotations

import json
import os
import socket
import time
from queue import Queue
from threading import Lock
from typing import Any

from .models import TimelineEvent, local_time


class DashboardEventBus:
    """Dashboard-compatible event sender plus local in-memory timeline."""

    def __init__(self) -> None:
        self.dashboard_host = os.getenv("DASHBOARD_HOST", "dah-dashboard")
        self.dashboard_port = int(os.getenv("DASHBOARD_PORT", "14571"))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.timeline: list[TimelineEvent] = []
        self._lock = Lock()

    def send(
        self,
        source: str,
        message: str,
        level: str = "info",
        detail: str = "",
        status: str = "OK",
    ) -> None:
        event = {
            "platform_type": "AGENT",
            "agent_type": "DEF",
            "platform_id": "DEF-001",
            "source": source,
            "message": message,
            "detail": detail,
            "level": level,
            "status": status,
            "time": time.strftime("%H:%M:%S"),
        }
        with self._lock:
            self.timeline.append(TimelineEvent(local_time(), source, message, detail))
            self.timeline = self.timeline[-500:]
        try:
            self._sock.sendto(json.dumps(event, ensure_ascii=False).encode("utf-8"), (self.dashboard_host, self.dashboard_port))
        except Exception:
            pass

    def timeline_dicts(self) -> list[dict[str, Any]]:
        with self._lock:
            return [item.to_dict() for item in self.timeline]

    def close(self) -> None:
        try:
            self._sock.close()
        except Exception:
            pass


class DefenseContext:
    def __init__(self) -> None:
        self.threat_queue: Queue = Queue()
        self.action_queue: Queue = Queue()
        self.recovery_queue: Queue = Queue()
        self.event_bus = DashboardEventBus()
        self.policy: dict[str, Any] = {}
        self.running = True

    def close(self) -> None:
        self.running = False
        self.event_bus.close()
