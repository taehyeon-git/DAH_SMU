from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


def http_get_json(url: str, timeout: float = 2.0) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {}


def http_post_json(url: str, payload: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {}


def clamp_confidence(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 2)

