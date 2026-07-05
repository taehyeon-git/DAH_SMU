from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", flush=True)

