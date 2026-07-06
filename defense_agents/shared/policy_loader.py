from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "default_policy.json"


def load_policy(path: str | None = None) -> dict[str, Any]:
    policy_path = Path(path or os.getenv("DEFENSE_POLICY_PATH", str(DEFAULT_POLICY_PATH)))
    with policy_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def summarize_policy(policy: dict[str, Any]) -> str:
    allowed_ids = policy.get("allowed_sys_ids", [])
    allowed_cmds = policy.get("allowed_commands", [])
    thresholds = policy.get("thresholds", {})
    return (
        f"allowed_sys_ids={allowed_ids}; "
        f"allowed_commands={len(allowed_cmds)}; "
        f"jamming_warn={thresholds.get('jamming_loss_warn')}%; "
        f"jamming_critical={thresholds.get('jamming_loss_critical')}%; "
        f"gps_speed={thresholds.get('gps_implied_speed_kmh')}km/h"
    )

