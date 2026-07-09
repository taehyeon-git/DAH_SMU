# -*- coding: utf-8 -*-
"""Passive MAVLink Recon — Low-Privilege Sentinel (DAH_SMU edition).

6단계 파이프라인:
  Phase 0: Dashboard API 사전 정찰  (GET /api/live + /api/failsafe)
  Phase 1: UDP 14550 수동 MAVLink 청취  (120s, dah-net 브로드캐스트)
  Phase 2: 6-팩터 신뢰도 채점
  Phase 3: LOW 자산 단기 재검증  (20s)
  Phase 4: InitialAccessAgent 전달용 정찰 태그/분석 힌트 생성
  Phase 5: JSON 저장  (intel.json + intel_handoff.json)

보안 제약: raw socket 없음, 패킷 주입 없음, 실제 군 장비 미연결.
Phase 0에서 HTTP 요청 1회 발생 — 이후는 완전 수동.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import subprocess
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

try:
    from .mavlink_parser import ParsedMavlinkFrame, parse_datagram
except ImportError:  # Direct script entrypoint fallback
    from mavlink_parser import ParsedMavlinkFrame, parse_datagram

from attack_agent.core.config import running_inside_docker
from attack_agent.core.io import read_json, write_json
from attack_agent.core.logging_utils import log, utc_now
from attack_agent.core.schemas import IntelDocument, load_intel, save_intel

# ── 수신 설정 ──────────────────────────────────────────────────────────────
LISTEN_PORT = 14550   # UAV → dah-net 브로드캐스트 포트
LISTEN_HOST = "0.0.0.0"

# ── Dashboard / ops_net 통신 ───────────────────────────────────────────────
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))
DASHBOARD_URL  = f"http://{DASHBOARD_HOST}:8080"

_evt_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _send_event(message: str, level: str = "info", detail: str = "", status: str = "") -> None:
    evt = {
        "platform_type": "AGENT",
        "agent_type":    "ATK",
        "platform_id":   "ATK-RECON",
        "source":        "PASSIVE-MAVLINK-RECON",
        "message":       message,
        "detail":        detail,
        "level":         level,
        "status":        status,
        "time":          time.strftime("%H:%M:%S"),
    }
    try:
        _evt_sock.sendto(json.dumps(evt).encode(), (DASHBOARD_HOST, DASHBOARD_PORT))
    except Exception:
        pass


# ── DAH_SMU 작전 상수 ─────────────────────────────────────────────────────
# UAV-001 (송골매) 운용 제원
UAV_PLATFORM_ID  = "UAV-001"
UAV_SYS_ID       = 1
UAV_HOST         = os.getenv("UAV_HOST", "172.31.50.10")
UAV_CMD_PORT     = int(os.getenv("UAV_CMD_PORT", "14551"))
UAV_CRUISE_ALT_M = 3500.0        # 순항 고도 (m)
UAV_CRUISE_SPD_MS= 166.7         # 600 km/h → m/s

# 경기 북부 정찰 작전구역 경계 (WAYPOINTS 외각)
OA_LAT_MIN = 37.850
OA_LAT_MAX = 37.960
OA_LON_MIN = 126.790
OA_LON_MAX = 126.920

# 신뢰도 임계값
CONF_HIGH   = 0.80
CONF_MEDIUM = 0.50

# ── MAVLink 상수 ──────────────────────────────────────────────────────────
MAV_TYPE = {
    0: "GENERIC", 2: "QUADROTOR", 10: "GROUND_ROVER",
    14: "ONBOARD_CONTROLLER", 27: "ADSB",
}
MAV_STATE = {
    0: "UNINIT", 1: "BOOT", 2: "CALIBRATING", 3: "STANDBY",
    4: "ACTIVE", 5: "CRITICAL", 6: "EMERGENCY", 7: "POWEROFF",
}
COMMAND_ACK_RESULT = {
    0: "ACCEPTED", 1: "TEMP_REJECTED", 2: "DENIED",
    3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS",
}
MISSION_ACK_TYPE = {
    0: "ACCEPTED", 1: "ERROR", 2: "UNSUPPORTED_FRAME",
    3: "UNSUPPORTED", 4: "NO_SPACE", 5: "INVALID",
    6: "INVALID_PARAM1", 13: "OPERATION_CANCELLED",
}
COMMAND_NAMES = {
    16:  "MAV_CMD_NAV_WAYPOINT",
    17:  "MAV_CMD_NAV_LOITER_UNLIM",
    20:  "MAV_CMD_NAV_RETURN_TO_LAUNCH",
    21:  "MAV_CMD_NAV_LAND",
    176: "MAV_CMD_DO_SET_MODE",
    193: "MAV_CMD_DO_PAUSE_CONTINUE",
}


# ── Phase 0: Dashboard API 사전 정찰 ─────────────────────────────────────

def phase0_api_recon() -> dict[str, Any]:
    """Dashboard /api/live + /api/failsafe 를 통해 현재 운용 상태 및 취약 정책값 수집.
    HTTP 요청이 발생하므로 GCS 감사로그에 흔적이 남을 수 있음(대시보드 → GCS 경유).
    """
    _send_event("Phase 0: API 사전 정찰 시작", detail=DASHBOARD_URL)

    live, policy = {}, {}
    http_requests = 0
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/live", timeout=3) as r:
            live = json.loads(r.read())
        http_requests += 1
    except Exception as e:
        print(f"[RECON-P0] /api/live 실패: {e}", flush=True)

    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/failsafe", timeout=3) as r:
            policy = json.loads(r.read())
        http_requests += 1
    except Exception as e:
        print(f"[RECON-P0] /api/failsafe 실패: {e}", flush=True)

    # UAV-001 상태 파싱
    pmap = {p.get("platform_id"): p for p in live.get("platforms", [])}
    uav  = pmap.get(UAV_PLATFORM_ID, {})
    ticn = uav.get("ticn", {})
    mission_state = live.get("mission_state", {})

    baseline: dict[str, Any] = {
        "http_requests":       http_requests,
        "api_available":       bool(live),
        # UAV 현재 상태
        "uav_lat":             uav.get("lat"),
        "uav_lon":             uav.get("lon"),
        "uav_alt":             uav.get("alt"),
        "uav_mode":            uav.get("mode"),
        "uav_fuel":            uav.get("fuel", uav.get("battery")),
        "uav_speed":           uav.get("speed"),
        "uav_status":          uav.get("status"),
        "mission_phase":       mission_state.get("phase"),
        "mission_desc":        mission_state.get("desc"),
        # 링크 상태
        "ticn_loss_pct":       ticn.get("loss_pct", 0),
        "ticn_link_quality":   ticn.get("link_quality", 100),
        # Fail-safe 정책
        "hb_timeout_sec":      policy.get("heartbeat", {}).get("timeout_sec", 5),
        "hb_interval_sec":     policy.get("heartbeat", {}).get("interval_sec", 1),
        "hb_max_miss":         policy.get("heartbeat", {}).get("max_miss_count", 5),
        "loss_warning_pct":    policy.get("packet_loss", {}).get("warning_pct", 10),
        "loss_critical_pct":   policy.get("packet_loss", {}).get("critical_pct", 15),
        "loss_duration_sec":   policy.get("packet_loss", {}).get("critical_duration_sec", 2),
        "latency_warning_ms":  policy.get("latency", {}).get("warning_ms", 500),
        "latency_critical_ms": policy.get("latency", {}).get("critical_ms", 1500),
        "failsafe_action":     policy.get("failsafe_action", "LOITER"),
    }

    print(f"\n[RECON-P0] API 사전 정찰 완료 (요청 {http_requests}회)", flush=True)
    print(f"  UAV 위치: lat={baseline['uav_lat']} lon={baseline['uav_lon']} "
          f"고도={baseline['uav_alt']}m 모드={baseline['uav_mode']}", flush=True)
    print(f"  연료={baseline['uav_fuel']}%  TICN 손실={baseline['ticn_loss_pct']}%", flush=True)
    print(f"  임무 단계={baseline['mission_phase']}  Fail-safe 정책: "
          f"HB timeout={baseline['hb_timeout_sec']}s  "
          f"loss critical={baseline['loss_critical_pct']}%  "
          f"action={baseline['failsafe_action']}", flush=True)

    _send_event(
        "Phase 0 완료 — 운용 상태 및 Fail-safe 정책 수집",
        level="warn",
        detail=(f"UAV alt={baseline['uav_alt']}m mode={baseline['uav_mode']} "
                f"hb_timeout={baseline['hb_timeout_sec']}s "
                f"loss_critical={baseline['loss_critical_pct']}%"),
        status="ALERT",
    )
    return baseline


# ── 유틸 ──────────────────────────────────────────────────────────────────

def _add_unique(values: list[Any], value: Any, limit: int = 20) -> None:
    if value is None or value in values:
        return
    values.append(value)
    if len(values) > limit:
        del values[0]


def _meters_per_lon_degree(lat_deg: float) -> float:
    return max(1.0, 111_320.0 * math.cos(math.radians(lat_deg)))


def _speed_from_positions(prev: dict[str, Any], cur: dict[str, Any]) -> float | None:
    dt = float(cur.get("received_s", 0.0)) - float(prev.get("received_s", 0.0))
    if dt <= 0.05:
        return None
    lat = float(prev["lat_deg"])
    north_m = (float(cur["lat_deg"]) - lat) * 111_320.0
    east_m  = (float(cur["lon_deg"]) - float(prev["lon_deg"])) * _meters_per_lon_degree(lat)
    return math.sqrt(north_m * north_m + east_m * east_m) / dt


def _is_in_operational_area(lat: float | None, lon: float | None) -> bool | None:
    """UAV-001의 정찰 작전구역(경기 북부) 내 위치 여부."""
    if lat is None or lon is None:
        return None
    return (OA_LAT_MIN <= lat <= OA_LAT_MAX) and (OA_LON_MIN <= lon <= OA_LON_MAX)


# ── 신뢰도 팩터 ───────────────────────────────────────────────────────────

def _physical_consistency_check(rec: dict[str, Any]) -> bool:
    samples = rec.get("position_history", [])
    if len(samples) < 2:
        return False
    calculated = _speed_from_positions(samples[-2], samples[-1])
    reported   = rec.get("ground_speed_mps")
    if calculated is None or reported is None:
        return False
    # 송골매는 최대 ~170m/s; 정지 시 양쪽 모두 작은 값이면 일치
    if calculated < 1.0 and float(reported) < 2.0:
        return True
    if calculated <= 0 or float(reported) <= 0:
        return False
    ratio = max(calculated, float(reported)) / max(0.01, min(calculated, float(reported)))
    return ratio < 4.0   # 고속 항공기 허용 비율 (DAH_temp 3.0 → 4.0으로 완화)


def _cross_message_validation(rec: dict[str, Any]) -> bool:
    # 무장 상태에서 고도가 비정상적으로 낮으면 이상
    if rec.get("is_armed") and rec.get("alt_m", 0) < -10:
        return False
    # ACTIVE 상태인데 위치 샘플이 없으면 의심
    if rec.get("system_status") == "ACTIVE" and rec.get("position_samples", 0) == 0:
        return False
    # 배터리 음수 이상
    if rec.get("battery_pct", 50) is not None and rec.get("battery_pct", 50) < -1:
        return False
    return bool(rec.get("last_heartbeat") or rec.get("position_samples", 0) > 0)


def _frame_integrity_factor(rec: dict[str, Any]) -> tuple[float, str]:
    invalid  = int(rec.get("crc_invalid_frames", 0))
    valid    = int(rec.get("crc_valid_frames",   0))
    unknown  = int(rec.get("crc_unknown_frames", 0))
    if invalid > 0:
        return 0.0,  f"crc_invalid={invalid}"
    if valid > 0:
        return 0.15, f"crc_valid={valid}"
    if unknown > 0:
        return 0.08, f"crc_unknown={unknown}"
    return 0.0, "no_crc_metadata"


def confidence_details(rec: dict[str, Any], now_s: float | None = None) -> dict[str, Any]:
    now = time.time() if now_s is None else now_s
    factors: dict[str, dict[str, Any]] = {}

    repeated = rec.get("packet_count", 0) >= 3
    factors["message_repetition"] = {"ok": repeated, "weight": 0.20 if repeated else 0.0}

    position_ok = rec.get("position_samples", 0) >= 2
    factors["position_repetition"] = {"ok": position_ok, "weight": 0.15 if position_ok else 0.0}

    physical_ok = _physical_consistency_check(rec)
    factors["physical_consistency"] = {"ok": physical_ok, "weight": 0.25 if physical_ok else 0.0}

    cross_ok = _cross_message_validation(rec)
    factors["cross_message_validation"] = {"ok": cross_ok, "weight": 0.15 if cross_ok else 0.0}

    integrity_weight, integrity_note = _frame_integrity_factor(rec)
    factors["frame_integrity"] = {"ok": integrity_weight > 0, "weight": integrity_weight, "note": integrity_note}

    last_seen = rec.get("last_seen")
    fresh = bool(last_seen and now - float(last_seen) <= 90.0)
    factors["freshness"] = {"ok": fresh, "weight": 0.10 if fresh else 0.0}

    score = round(sum(item["weight"] for item in factors.values()), 2)
    return {"score": min(1.0, score), "label": _confidence_label(score), "factors": factors}


def _confidence_score(rec: dict[str, Any]) -> float:
    return float(confidence_details(rec)["score"])


def _confidence_label(score: float) -> str:
    if score >= CONF_HIGH:
        return "HIGH — 정찰 신뢰도 높음"
    if score >= CONF_MEDIUM:
        return "MEDIUM — 재검증 권고"
    return "LOW — 지연/스푸핑/불완전 관측"


# ── 행동 패턴 분류 (DAH_SMU UAV-001 상황 맞춤) ───────────────────────────

def classify_pattern(rec: dict[str, Any]) -> str:
    samples = rec.get("position_history", [])
    speed   = float(rec.get("ground_speed_mps") or 0.0)

    if rec.get("mission_upload_in_progress"):
        return "MISSION_UPLOAD_ACTIVITY"
    if rec.get("command_acks") or rec.get("command_long_seen"):
        return "COMMAND_ACTIVITY"

    if len(samples) >= 3:
        alt_delta = samples[-1]["alt_m"] - samples[0]["alt_m"]
        heading_vals = [float(s.get("heading_deg") or 0.0) for s in samples[-5:]]
        heading_span  = max(heading_vals) - min(heading_vals) if heading_vals else 0.0

        if alt_delta < -50 and speed < 100:   # 하강 중 (송골매 기준 하강속도)
            return "DESCENT_OR_RTL"
        if heading_span > 30:                  # 웨이포인트 선회
            return "PATROL_TURNING"

    if speed < 10 and rec.get("position_samples", 0) >= 2:
        return "LOITER_HOLDING"   # Fail-safe 또는 HOLD 명령

    if speed > 80:                             # 송골매 순항 속도 (600km/h)
        in_oa = _is_in_operational_area(rec.get("lat_deg"), rec.get("lon_deg"))
        if in_oa is True:
            return "PATROL_TRANSIT"
        if in_oa is False:
            return "OUT_OF_AREA"   # 작전구역 이탈 — 스푸핑 가능성

    if rec.get("mission_seq") is not None:
        return "MISSION_PROGRESS"
    if rec.get("position_samples", 0):
        return "TRANSIT"
    return "INSUFFICIENT_DATA"


def predict_position(rec: dict[str, Any], horizon_s: int) -> dict[str, Any] | None:
    if rec.get("lat_deg") is None or not rec.get("velocity_mps"):
        return None
    lat, lon  = float(rec["lat_deg"]), float(rec["lon_deg"])
    alt       = float(rec.get("alt_m") or 0.0)
    vx_north, vy_east, vz_down = [float(v) for v in rec.get("velocity_mps", [0.0, 0.0, 0.0])]
    pred_lat  = lat + (vx_north * horizon_s) / 111_320.0
    pred_lon  = lon + (vy_east  * horizon_s) / _meters_per_lon_degree(lat)
    pred_alt  = alt - vz_down * horizon_s
    pattern   = classify_pattern(rec)
    penalty   = 50.0 if pattern in {"PATROL_TURNING", "DESCENT_OR_RTL", "COMMAND_ACTIVITY"} else 15.0
    base_err  = penalty + 0.5 * horizon_s   # 고속 항공기 → 오차 더 큼
    in_oa     = _is_in_operational_area(round(pred_lat, 7), round(pred_lon, 7))
    return {
        "model":              "constant_velocity_short_horizon",
        "horizon_s":          horizon_s,
        "lat":                round(pred_lat, 7),
        "lon":                round(pred_lon, 7),
        "alt_m":              round(pred_alt, 1),
        "expected_error_m":   round(base_err, 1),
        "in_operational_area": in_oa,
        "limits":             "단기 상황 인식 전용; InitialAccessAgent 판단 전 재검증 권고",
    }


# ── Phase 4: InitialAccessAgent 전달용 정찰 태그 생성 ───────────────────

def build_recon_tags(
    rec: dict[str, Any],
    score: float,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """정찰 결과를 공격 후보가 아닌 분석 신호로 정규화한다."""
    pattern = classify_pattern(rec)
    api_available = bool(baseline and baseline.get("api_available"))
    link_metrics_available = bool(
        baseline
        and (
            baseline.get("ticn_loss_pct") is not None
            or baseline.get("ticn_link_quality") is not None
        )
    )
    protocol_metadata_available = bool(
        rec.get("crc_valid_frames")
        or rec.get("crc_invalid_frames")
        or rec.get("crc_unknown_frames")
        or rec.get("signed_frames")
        or rec.get("unsigned_frames")
    )
    tags: list[str] = []
    tags.append("CONFIDENCE_HIGH" if score >= CONF_HIGH else "CONFIDENCE_MEDIUM" if score >= CONF_MEDIUM else "CONFIDENCE_LOW")
    tags.append(f"PATTERN_{pattern}")
    if api_available:
        tags.append("API_BASELINE_AVAILABLE")
    if link_metrics_available:
        tags.append("LINK_METRICS_AVAILABLE")
    if protocol_metadata_available:
        tags.append("PROTOCOL_FRAME_METADATA_AVAILABLE")
    if baseline and baseline.get("failsafe_action"):
        tags.append("FAILSAFE_POLICY_OBSERVED")
    if rec.get("in_operational_area") is False:
        tags.append("OUT_OF_OPERATIONAL_AREA")

    return {
        "tags": tags,
        "pattern": pattern,
        "confidence_score": score,
        "confidence_ready": score >= CONF_MEDIUM,
        "api_baseline_available": api_available,
        "link_metrics_available": link_metrics_available,
        "protocol_metadata_available": protocol_metadata_available,
        "failsafe_policy_observed": bool(baseline and baseline.get("failsafe_action")),
        "selection_owner": "InitialAccessAgent",
        "module_candidates_generated": False,
        "analysis_hints": timing_recommendations(rec, score),
    }


def timing_recommendations(rec: dict[str, Any], score: float) -> list[dict[str, str]]:
    if score < CONF_MEDIUM:
        return [{"status": "hold", "reason": "신뢰도 MEDIUM 미만 — 재검증 필요"}]
    pattern = classify_pattern(rec)
    alt_m   = float(rec.get("alt_m") or 0.0)
    armed   = bool(rec.get("is_armed"))

    if pattern == "LOITER_HOLDING":
        return [{"signal": "LOITER_HOLDING", "reason": "이미 대기 상태에 가까워 실행 전후 위치 변화 검증이 중요"}]
    if pattern == "PATROL_TURNING":
        return [{"signal": "PATROL_TURNING", "reason": "선회 중이라 위치/방위각 기반 관측 신뢰도를 재확인할 필요"}]
    if pattern == "DESCENT_OR_RTL":
        return [{"signal": "DESCENT_OR_RTL", "reason": "이미 귀환/하강 중일 수 있어 후속 분석에서 원인 혼동 주의"}]
    if pattern == "PATROL_TRANSIT" and armed and alt_m > 1000:
        return [{"signal": "PATROL_TRANSIT_HIGH_ALT", "reason": f"순항 중 + 고도 {alt_m:.0f}m — 실행 전후 상태 변화 비교에 유리"}]
    if pattern == "OUT_OF_AREA":
        return [{"signal": "OUT_OF_AREA", "reason": "작전구역 이탈 감지 — 위치 데이터 재검증 필요"}]
    if pattern == "MISSION_UPLOAD_ACTIVITY":
        return [{"signal": "MISSION_UPLOAD_ACTIVITY", "reason": "미션 업로드 활동 감지 — 통신 경로 분석 근거"}]
    return [{"signal": pattern, "reason": f"패턴={pattern} — InitialAccessAgent 분석 입력으로 전달"}]


# ── Blue-team 매핑 (DAH_SMU 환경 기준) ───────────────────────────────────

def blue_team_mapping() -> list[dict[str, Any]]:
    return [
        {
            "layer":                 "GCS 어플리케이션 감사로그",
            "expected_visibility":   "낮음 (Phase 1 기준) / 중간 (Phase 0 포함 시)",
            "reason":                "Phase 0에서 Dashboard HTTP 요청 2회 발생; Phase 1은 UDP 수신만",
            "recommended_control":   "Dashboard → GCS API 호출 로그 수집 + 비정상 접근 출처 검출",
            "dah_smu_component":     "dah-dashboard /api/live, /api/failsafe",
        },
        {
            "layer":                 "네트워크 IDS / dah-net 모니터",
            "expected_visibility":   "중간",
            "reason":                "UDP 14550 브로드캐스트는 다중 수신 가능; dah-recon 컨테이너 식별 어려움",
            "recommended_control":   "dah-net 세그먼트의 비인가 UDP bind 이벤트 경보",
            "dah_smu_component":     "docker network: dah-net (172.31.50.0/24)",
        },
        {
            "layer":                 "컨테이너 런타임 / Docker 이벤트",
            "expected_visibility":   "높음",
            "reason":                "recon-lab 프로파일 컨테이너 시작/커맨드라인/stdout은 Docker 이벤트로 관측",
            "recommended_control":   "docker events 모니터링 + 비인가 프로파일 컨테이너 기동 감사",
            "dah_smu_component":     "dah-recon (recon-lab profile, 172.31.50.40)",
        },
        {
            "layer":                 "호스트 EDR / eBPF",
            "expected_visibility":   "중간-높음",
            "reason":                "SO_REUSEADDR + SO_REUSEPORT로 14550 bind — 프로세스 소켓 이벤트 추적 가능",
            "recommended_control":   "UDP 14550 bind 이벤트 + 172.31.50.40 출처 트래픽 감사",
            "dah_smu_component":     "attack_agent/recon.py 소켓 개방",
        },
        {
            "layer":                 "프로토콜 모니터 / MAVLink 서명",
            "expected_visibility":   "노출=높음 / 수신자 식별=낮음",
            "reason":                "MAVLink 서명 없이 브로드캐스트되면 평문 텔레메트리 전체 노출",
            "recommended_control":   "MAVLink v2 서명 강제 적용 + 브로드캐스트 범위 축소 (단방향 unicast)",
            "dah_smu_component":     "uav/mock_uav.py → dah-companion:14550",
        },
    ]


def ghost_sentinel_assessment() -> dict[str, Any]:
    return {
        "implemented":   False,
        "purpose":       "위협 모델 비교용; 이 모듈은 raw socket/CAP_NET_RAW 없음",
        "would_reduce":  ["UDP 14550 bind 테이블 가시성", "컨테이너 커맨드라인 노출"],
        "would_introduce": [
            "CAP_NET_RAW 정책 이벤트",
            "AF_PACKET 소켓 텔레메트리",
            "dah-net 내 모든 IP 패킷 수신 (unicast 포함)",
        ],
        "defensive_question": (
            "dah-net 내에서 AF_PACKET 소켓을 개방하는 컨테이너를 탐지할 수 있는가? "
            "현재 UDP bind 모니터만으로는 Ghost Sentinel을 탐지하지 못한다."
        ),
    }


# ── IntelligenceReport ────────────────────────────────────────────────────

class IntelligenceReport:
    def __init__(self) -> None:
        self.assets: dict[int, dict[str, Any]] = {}
        self.packet_count    = 0
        self.parse_errors    = 0
        self.unknown_msg_count = 0
        self.msg_type_counts: dict[str, int] = {}
        self.signed_frames    = 0
        self.unsigned_frames  = 0
        self.crc_valid_frames   = 0
        self.crc_invalid_frames = 0
        self.crc_unknown_frames = 0
        self.start_time = time.time()

    def _get(self, sys_id: int) -> dict[str, Any]:
        if sys_id not in self.assets:
            self.assets[sys_id] = {
                "sys_id":          sys_id,
                "first_seen":      time.time(),
                "last_seen":       None,
                "packet_count":    0,
                "source_ips":      [],
                "message_counts":  {},
                "position_history": [],
                "command_acks":    [],
                "mission_items":   [],
            }
        return self.assets[sys_id]

    def record_frame(self, frame: ParsedMavlinkFrame, source_ip: str) -> dict[str, Any]:
        rec = self._get(frame.system_id)
        rec["packet_count"]  += 1
        rec["last_seen"]      = time.time()
        rec["component_id"]   = frame.component_id
        rec["last_sequence"]  = frame.sequence
        rec["last_message"]   = frame.message_name
        rec["signed_frames"]  = rec.get("signed_frames",  0) + (1 if frame.signed else 0)
        rec["unsigned_frames"] = rec.get("unsigned_frames", 0) + (0 if frame.signed else 1)
        if frame.crc_valid is True:
            rec["crc_valid_frames"]   = rec.get("crc_valid_frames",   0) + 1
        elif frame.crc_valid is False:
            rec["crc_invalid_frames"] = rec.get("crc_invalid_frames", 0) + 1
        else:
            rec["crc_unknown_frames"] = rec.get("crc_unknown_frames", 0) + 1
        _add_unique(rec["source_ips"], source_ip)
        counts = rec["message_counts"]
        counts[frame.message_name] = counts.get(frame.message_name, 0) + 1
        return rec

    def record_msg_type(self, name: str) -> None:
        self.msg_type_counts[name] = self.msg_type_counts.get(name, 0) + 1

    def record_frame_security(self, frame: ParsedMavlinkFrame) -> None:
        if frame.signed:
            self.signed_frames += 1
        else:
            self.unsigned_frames += 1
        if frame.crc_valid is True:
            self.crc_valid_frames += 1
        elif frame.crc_valid is False:
            self.crc_invalid_frames += 1
        else:
            self.crc_unknown_frames += 1

    def update_heartbeat(self, sys_id: int, fields: dict[str, Any]) -> None:
        rec = self._get(sys_id)
        rec["mav_type"]       = MAV_TYPE.get(fields.get("type", -1), f"TYPE_{fields.get('type')}")
        rec["system_status"]  = MAV_STATE.get(fields.get("system_status", -1), "UNKNOWN")
        rec["base_mode"]      = fields.get("base_mode", 0)
        rec["is_armed"]       = bool(fields.get("base_mode", 0) & 0x80)
        rec["is_guided"]      = bool(fields.get("base_mode", 0) & 0x08)
        rec["last_heartbeat"] = time.time()

    def update_position(self, sys_id: int, fields: dict[str, Any], *, message_name: str) -> None:
        rec = self._get(sys_id)
        lat     = fields.get("lat", 0) / 1e7
        lon     = fields.get("lon", 0) / 1e7
        alt     = fields.get("alt", 0) / 1000.0
        rel_alt = fields.get("relative_alt", fields.get("alt", 0)) / 1000.0
        vx  = fields.get("vx", 0) / 100.0
        vy  = fields.get("vy", 0) / 100.0
        vz  = fields.get("vz", 0) / 100.0
        hdg = fields.get("hdg", 0) / 100.0
        sample = {
            "received_s":  time.time(),
            "message":     message_name,
            "lat_deg":     round(lat, 7),
            "lon_deg":     round(lon, 7),
            "alt_m":       round(alt, 1),
            "rel_alt_m":   round(rel_alt, 1),
            "vx_mps":      round(vx, 2),
            "vy_mps":      round(vy, 2),
            "vz_mps":      round(vz, 2),
            "heading_deg": round(hdg, 1),
        }
        rec.update({
            "lat_deg":          sample["lat_deg"],
            "lon_deg":          sample["lon_deg"],
            "alt_m":            sample["alt_m"],
            "rel_alt_m":        sample["rel_alt_m"],
            "velocity_mps":     [sample["vx_mps"], sample["vy_mps"], sample["vz_mps"]],
            "ground_speed_mps": round(math.sqrt(vx**2 + vy**2), 2),
            "heading_deg":      sample["heading_deg"],
            "position_samples": rec.get("position_samples", 0) + 1,
            "in_operational_area": _is_in_operational_area(sample["lat_deg"], sample["lon_deg"]),
        })
        history = rec.setdefault("position_history", [])
        history.append(sample)
        if len(history) > 30:
            del history[0]
        rec["trail"] = [[p["lat_deg"], p["lon_deg"], p["alt_m"]] for p in history[-20:]]

    def update_sys_status(self, sys_id: int, fields: dict[str, Any]) -> None:
        rec = self._get(sys_id)
        rec["battery_pct"]    = fields.get("battery_remaining", -1)
        rec["drop_rate_comm"] = fields.get("drop_rate_comm", 0)
        rec["errors_comm"]    = fields.get("errors_comm", 0)

    def note_command_ack(self, sys_id: int, fields: dict[str, Any]) -> None:
        rec  = self._get(sys_id)
        acks = rec.setdefault("command_acks", [])
        cmd  = fields.get("command")
        acks.append({
            "command":      cmd,
            "command_name": COMMAND_NAMES.get(cmd, f"CMD_{cmd}"),
            "result":       COMMAND_ACK_RESULT.get(fields.get("result", -1), f"RESULT_{fields.get('result')}"),
            "seen_s":       time.time(),
        })
        if len(acks) > 10:
            del acks[0]

    def note_command_long(self, sys_id: int, fields: dict[str, Any]) -> None:
        rec      = self._get(sys_id)
        commands = rec.setdefault("command_long_seen", [])
        cmd      = fields.get("command")
        commands.append({
            "command":          cmd,
            "command_name":     COMMAND_NAMES.get(cmd, f"CMD_{cmd}"),
            "target_system":    fields.get("target_system"),
            "target_component": fields.get("target_component"),
            "seen_s":           time.time(),
        })
        if len(commands) > 10:
            del commands[0]

    def note_mission_current(self, sys_id: int, fields: dict[str, Any]) -> None:
        self._get(sys_id)["mission_seq"] = fields.get("seq")

    def note_mission_count(self, sys_id: int, fields: dict[str, Any]) -> None:
        rec = self._get(sys_id)
        rec["mission_count"] = fields.get("count")
        rec["mission_upload_in_progress"] = True

    def note_mission_ack(self, sys_id: int, fields: dict[str, Any]) -> None:
        self._get(sys_id)["last_mission_ack"] = MISSION_ACK_TYPE.get(
            fields.get("type", -1), f"ACK_{fields.get('type')}"
        )

    def note_mission_request(self, sys_id: int, fields: dict[str, Any]) -> None:
        rec = self._get(sys_id)
        rec["mission_upload_in_progress"] = True
        rec["mission_upload_seq"] = fields.get("seq")

    def note_mission_item(self, sys_id: int, fields: dict[str, Any]) -> None:
        rec   = self._get(sys_id)
        items = rec.setdefault("mission_items", [])
        items.append({
            "seq":     fields.get("seq"),
            "command": fields.get("command"),
            "x": fields.get("x"),
            "y": fields.get("y"),
            "z": fields.get("z"),
        })
        if len(items) > 10:
            del items[0]

    def sanitized_assets(self) -> dict[str, Any]:
        return {str(sid): dict(rec) for sid, rec in self.assets.items()}

    def summary(self) -> dict[str, Any]:
        return {
            "packet_count":       self.packet_count,
            "parse_errors":       self.parse_errors,
            "unknown_msgs":       self.unknown_msg_count,
            "asset_count":        len(self.assets),
            "uav001_identified":  UAV_SYS_ID in self.assets,
            "msg_type_counts":    dict(sorted(self.msg_type_counts.items())),
            "signed_frames":      self.signed_frames,
            "unsigned_frames":    self.unsigned_frames,
            "crc_valid_frames":   self.crc_valid_frames,
            "crc_invalid_frames": self.crc_invalid_frames,
            "crc_unknown_frames": self.crc_unknown_frames,
        }

    def print_phase1_summary(self) -> None:
        elapsed = time.time() - self.start_time
        print(f"\n{'=' * 64}", flush=True)
        print(f"[PASSIVE-MAVLINK-RECON] Phase 1 수집 완료", flush=True)
        print(f"  경과 시간:    {elapsed:.1f}s", flush=True)
        print(f"  수신 패킷:    {self.packet_count}개", flush=True)
        print(f"  파싱 오류:    {self.parse_errors}개", flush=True)
        print(f"  식별 자산:    {len(self.assets)}개", flush=True)
        print(f"  UAV-001 식별: {'Y' if UAV_SYS_ID in self.assets else 'N'}", flush=True)
        print(f"  HTTP 요청:    0  (Phase 1은 완전 수동)", flush=True)
        print(f"  메시지 분포:  {dict(sorted(self.msg_type_counts.items()))}", flush=True)
        print(f"  프레임 서명:  signed={self.signed_frames} unsigned={self.unsigned_frames} "
              f"crc_invalid={self.crc_invalid_frames}", flush=True)
        print(f"{'=' * 64}", flush=True)

        for sid, rec in sorted(self.assets.items()):
            cd      = confidence_details(rec)
            armed   = "Y" if rec.get("is_armed") else "N"
            label   = cd["label"].split("—")[0].strip()
            in_oa   = rec.get("in_operational_area")
            oa_str  = {True: "작전구역내", False: "작전구역외", None: "위치미확인"}.get(in_oa, "?")
            print(
                f"  [SYS_ID={sid}] {rec.get('mav_type','?')} "
                f"상태={rec.get('system_status','?')} 무장={armed} "
                f"신뢰도={cd['score']:.2f} [{label}] {oa_str}",
                flush=True,
            )
            if "lat_deg" in rec:
                print(
                    f"    위치: {rec['lat_deg']}, {rec['lon_deg']} "
                    f"고도={rec.get('alt_m')}m 속도={rec.get('ground_speed_mps')}m/s "
                    f"방위={rec.get('heading_deg')}°",
                    flush=True,
                )
            if rec.get("battery_pct", -1) is not None and rec.get("battery_pct", -1) >= 0:
                print(
                    f"    배터리={rec['battery_pct']}% "
                    f"패킷손실={rec.get('drop_rate_comm', 0) / 100:.1f}%",
                    flush=True,
                )
            print(f"    패턴={classify_pattern(rec)}", flush=True)


# ── 수집 루프 ─────────────────────────────────────────────────────────────

def _handle_frame(report: IntelligenceReport, frame: ParsedMavlinkFrame, source_ip: str) -> None:
    report.record_frame_security(frame)
    report.record_msg_type(frame.message_name)
    report.record_frame(frame, source_ip)
    fields = frame.fields
    sid    = frame.system_id
    name   = frame.message_name

    if name == "HEARTBEAT":
        report.update_heartbeat(sid, fields)
    elif name in {"GLOBAL_POSITION_INT", "UTM_GLOBAL_POSITION"}:
        report.update_position(sid, fields, message_name=name)
    elif name == "SYS_STATUS":
        report.update_sys_status(sid, fields)
    elif name == "COMMAND_ACK":
        report.note_command_ack(sid, fields)
    elif name == "COMMAND_LONG":
        report.note_command_long(sid, fields)
    elif name == "MISSION_CURRENT":
        report.note_mission_current(sid, fields)
    elif name == "MISSION_COUNT":
        report.note_mission_count(sid, fields)
    elif name == "MISSION_ACK":
        report.note_mission_ack(sid, fields)
    elif name == "MISSION_REQUEST_INT":
        report.note_mission_request(sid, fields)
    elif name == "MISSION_ITEM_INT":
        report.note_mission_item(sid, fields)
    else:
        report.unknown_msg_count += 1


def _open_socket(listen_host: str, listen_port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # Linux: 브로드캐스트 공유
    except AttributeError:
        pass
    sock.bind((listen_host, listen_port))
    sock.settimeout(1.0)
    return sock


def _collect(sock: socket.socket, report: IntelligenceReport, deadline: float, label: str) -> None:
    while time.time() < deadline:
        try:
            datagram, addr = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError as exc:
            print(f"  [{label}] socket error: {exc}", flush=True)
            break
        report.packet_count += 1
        try:
            frames = parse_datagram(datagram)
        except Exception as exc:
            report.parse_errors += 1
            print(f"  [{label}] parse error from {addr[0]} err={exc}", flush=True)
            continue
        for item in frames:
            if not isinstance(item, ParsedMavlinkFrame):
                continue
            _handle_frame(report, item, addr[0])
            print(
                f"  [{label}] {addr[0]} sys={item.system_id} {item.message_name} "
                f"signed={'Y' if item.signed else 'N'} crc={item.crc_valid}",
                flush=True,
            )


def _merge_better_observations(
    primary: IntelligenceReport, secondary: IntelligenceReport
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for sid, new_rec in secondary.assets.items():
        old_rec   = primary.assets.get(sid)
        if old_rec is None:
            primary.assets[sid] = new_rec
            changes.append({"sys_id": sid, "action": "added", "new_score": _confidence_score(new_rec)})
            continue
        old_score = _confidence_score(old_rec)
        new_score = _confidence_score(new_rec)
        if new_score > old_score:
            old_rec.update(new_rec)
            changes.append({"sys_id": sid, "action": "improved", "old_score": old_score, "new_score": new_score})
        else:
            changes.append({"sys_id": sid, "action": "kept", "old_score": old_score, "new_score": new_score})
    return changes


# ── Phase 5: Intel 저장 ───────────────────────────────────────────────────

def _write_intel_handoff(path: str, uav_rec: dict[str, Any] | None,
                          cd: dict[str, Any], recon_tags: dict[str, Any],
                          baseline: dict[str, Any] | None) -> None:
    """다른 공격 에이전트가 읽을 수 있는 경량 핸드오프 파일 생성."""
    handoff: dict[str, Any] = {
        "generated_at":       time.strftime("%Y-%m-%dT%H:%M:%S"),
        "recon_source":       "passive_mavlink_recon + dashboard_api",
        "target": {
            "platform_id":   UAV_PLATFORM_ID,
            "sys_id":        UAV_SYS_ID,
            "host":          UAV_HOST,
            "cmd_port":      UAV_CMD_PORT,
        },
        "confidence": {
            "score":  cd.get("score"),
            "label":  cd.get("label"),
        },
        "uav_state": {
            "armed":         uav_rec.get("is_armed")       if uav_rec else None,
            "alt_m":         uav_rec.get("alt_m")          if uav_rec else None,
            "lat":           uav_rec.get("lat_deg")        if uav_rec else None,
            "lon":           uav_rec.get("lon_deg")        if uav_rec else None,
            "speed_mps":     uav_rec.get("ground_speed_mps") if uav_rec else None,
            "heading_deg":   uav_rec.get("heading_deg")    if uav_rec else None,
            "battery_pct":   uav_rec.get("battery_pct")   if uav_rec else None,
            "pattern":       classify_pattern(uav_rec)     if uav_rec else None,
            "in_oa":         uav_rec.get("in_operational_area") if uav_rec else None,
        },
        "api_baseline":       baseline,
        "recon_tags":         recon_tags,
        "analysis_hints":     recon_tags.get("analysis_hints", []),
        "next_stage":         "InitialAccessAgent",
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(handoff, fh, ensure_ascii=False, indent=2, default=str)
    print(f"[passive-mavlink-recon] 핸드오프 저장 → {path}", flush=True)


# ── 메인 파이프라인 ────────────────────────────────────────────────────────

def run(
    listen_host: str,
    listen_port: int,
    duration_s: int,
    revalidate_s: int,
    prediction_horizon_s: int,
    output_path: str | None,
    skip_phase0: bool = False,
    chain_handoff_path: str | None = None,
) -> dict[str, Any]:
    print("[passive-mavlink-recon] Low-Privilege Sentinel 시작 (DAH_SMU)", flush=True)
    print(f"  listen={listen_host}:{listen_port}  duration_s={duration_s}", flush=True)
    print("  scope=DAH 2026 제어 적 에뮬레이션 — raw socket 없음, 패킷 주입 없음", flush=True)
    _send_event("정찰 파이프라인 시작", detail=f"listen={listen_host}:{listen_port}")

    # ── Phase 0: API 사전 정찰 ────────────────────────────────────────────
    baseline: dict[str, Any] | None = None
    http_requests_total = 0
    if not skip_phase0:
        print(f"\n[passive-mavlink-recon] Phase 0: API 사전 정찰", flush=True)
        baseline = phase0_api_recon()
        http_requests_total = baseline.get("http_requests", 0)
    else:
        print(f"\n[passive-mavlink-recon] Phase 0: 생략 (--skip-phase0)", flush=True)

    # ── Phase 1: 수동 MAVLink 청취 ────────────────────────────────────────
    print(f"\n[passive-mavlink-recon] Phase 1: UDP {listen_host}:{listen_port} 수동 청취 ({duration_s}s)", flush=True)
    _send_event(f"Phase 1: 수동 청취 시작 UDP:{listen_port}", detail=f"duration={duration_s}s")
    report = IntelligenceReport()
    sock   = _open_socket(listen_host, listen_port)
    _collect(sock, report, time.time() + duration_s, "phase1")
    sock.close()
    report.print_phase1_summary()
    _send_event(
        f"Phase 1 완료 — {len(report.assets)}개 자산 식별",
        level="warn" if report.assets else "info",
        detail=f"packets={report.packet_count} UAV001={'Y' if UAV_SYS_ID in report.assets else 'N'}",
        status="OK",
    )

    # ── Phase 2: 신뢰도 채점 ─────────────────────────────────────────────
    print(f"\n[passive-mavlink-recon] Phase 2: 신뢰도 채점", flush=True)
    for sid, rec in sorted(report.assets.items()):
        cd = confidence_details(rec)
        label_short = cd["label"].split("—")[0].strip()
        print(f"  sys={sid:3d}  score={cd['score']:.2f}  {label_short}", flush=True)
        if sid == UAV_SYS_ID and cd["score"] >= CONF_HIGH:
            _send_event(
                f"UAV-001 HIGH 신뢰도 확보",
                level="warn",
                detail=f"score={cd['score']:.2f} armed={'Y' if rec.get('is_armed') else 'N'} "
                       f"alt={rec.get('alt_m')}m pattern={classify_pattern(rec)}",
                status="ALERT",
            )

    # ── Phase 3: 단기 재검증 (LOW 자산) ──────────────────────────────────
    revalidation_changes: list[dict[str, Any]] = []
    needs_reval = [sid for sid, rec in report.assets.items() if _confidence_score(rec) < CONF_HIGH]
    if revalidate_s > 0 and needs_reval:
        print(f"\n[passive-mavlink-recon] Phase 3: 재검증 sys_ids={needs_reval}", flush=True)
        second = IntelligenceReport()
        sock2  = _open_socket(listen_host, listen_port)
        _collect(sock2, second, time.time() + revalidate_s, "revalidate")
        sock2.close()
        revalidation_changes = _merge_better_observations(report, second)
        for change in revalidation_changes:
            print(f"  재검증 {change}", flush=True)
    elif revalidate_s > 0:
        print(f"\n[passive-mavlink-recon] Phase 3: 전 자산 HIGH — 재검증 생략", flush=True)

    # ── Phase 4: InitialAccessAgent 전달용 정찰 신호 정규화 ─────────────
    print(f"\n[passive-mavlink-recon] Phase 4: 정찰 신호 정규화", flush=True)
    uav_rec    = report.assets.get(UAV_SYS_ID)
    uav_cd     = confidence_details(uav_rec) if uav_rec else {"score": 0.0, "label": "NO_DATA", "factors": {}}
    recon_tags = build_recon_tags(uav_rec or {}, float(uav_cd["score"]), baseline)
    timing_recs = timing_recommendations(uav_rec or {}, float(uav_cd["score"]))
    print(f"  tags={recon_tags.get('tags', [])}", flush=True)
    print(f"  selection_owner={recon_tags.get('selection_owner')}", flush=True)
    print(f"\n[passive-mavlink-recon] 분석 힌트: {timing_recs}", flush=True)
    _send_event(
        "정찰 신호 정규화 완료",
        level="info",
        detail=", ".join(recon_tags.get("tags", [])),
        status="OK",
    )

    # 위치 예측
    prediction = predict_position(uav_rec or {}, prediction_horizon_s) if uav_rec else None
    if prediction:
        print(
            f"\n[passive-mavlink-recon] {prediction_horizon_s}s 위치 예측: "
            f"lat={prediction['lat']} lon={prediction['lon']} alt={prediction['alt_m']}m "
            f"오차={prediction['expected_error_m']}m in_oa={prediction.get('in_operational_area')}",
            flush=True,
        )

    # ── Phase 5: JSON 저장 ────────────────────────────────────────────────
    intel: dict[str, Any] = {
        "meta": {
            "attack":               "passive_mavlink_recon",
            "scenario":             "Low-Privilege Sentinel (DAH_SMU)",
            "threat_model":         "low-privilege observer on dah-net MAVLink broadcast segment",
            "duration_s":           duration_s,
            "revalidate_s":         revalidate_s,
            "prediction_horizon_s": prediction_horizon_s,
            "http_requests":        http_requests_total,
            "gcs_audit_trace":      http_requests_total > 0,
            "network_ids_visible":  True,
            "raw_socket_used":      False,
            "cap_net_raw_required": False,
            "dah_smu_target":       UAV_PLATFORM_ID,
        },
        "phase0_api_baseline":    baseline,
        "collection_summary":     report.summary(),
        "assets":                 report.sanitized_assets(),
        "uav001": {
            "sys_id":    UAV_SYS_ID,
            "confidence": uav_cd,
            "state":     {
                k: uav_rec.get(k) for k in [
                    "mav_type", "system_status", "is_armed", "is_guided",
                    "lat_deg", "lon_deg", "alt_m", "ground_speed_mps",
                    "heading_deg", "battery_pct", "drop_rate_comm",
                    "mission_seq", "in_operational_area",
                ]
            } if uav_rec else None,
            "pattern":         classify_pattern(uav_rec) if uav_rec else None,
            "prediction":      prediction,
            "timing_recs":     timing_recs,
        },
        "recon_tags":         recon_tags,
        "revalidation":       revalidation_changes,
        "blue_team_mapping":  blue_team_mapping(),
        "ghost_sentinel":     ghost_sentinel_assessment(),
    }

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(intel, fh, ensure_ascii=False, indent=2, default=str)
        print(f"\n[passive-mavlink-recon] Phase 5: intel 저장 → {output_path}", flush=True)

        # intel_handoff.json (InitialAccessAgent용 경량 파일)
        handoff_path = chain_handoff_path or os.path.join(os.path.dirname(output_path), "intel_handoff.json")
        _write_intel_handoff(handoff_path, uav_rec, uav_cd, recon_tags, baseline)
        _send_event("인텔 저장 완료", detail=f"{output_path} + {handoff_path}", status="OK")
    else:
        print(json.dumps(intel, ensure_ascii=False, indent=2, default=str))

    return intel


class ReconAgent:
    """Stage 1 agent and recon collector facade.

    This class lives in ``recon.py`` so collection and stage-1 normalization stay
    in one maintainable module. The collector CLI below remains compatible with
    the ``dah-recon`` Docker service.
    """

    name = "ReconAgent"

    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir

    def run(
        self,
        source: str = "output/intel_handoff.json",
        passive_source: str = "output/passive_mavlink_intel.json",
        output: str | None = None,
        collect: bool = False,
        collection_mode: str = "auto",
        listen_host: str = "0.0.0.0",
        listen_port: int = 14550,
        duration_s: int = 30,
        revalidate_s: int = 20,
        prediction_horizon_s: int = 60,
    ) -> dict[str, Any]:
        collection_report: dict[str, Any] | None = None
        if collect:
            collection_report = self.collect(
                source=source,
                passive_source=passive_source,
                mode=collection_mode,
                listen_host=listen_host,
                listen_port=listen_port,
                duration_s=duration_s,
                revalidate_s=revalidate_s,
                prediction_horizon_s=prediction_horizon_s,
            )

        output_path = output or os.path.join(self.output_dir, "stage_1_recon.json")
        doc = load_intel(source) if os.path.exists(source) else IntelDocument(source="recon_agent_empty_input")
        doc.source = source if os.path.exists(source) else doc.source
        doc.environment.update({
            "stage": "RECON",
            "agent": self.name,
            "collection_executed": bool(collection_report),
            "collection_report": collection_report,
            "source_recon_file": source if os.path.exists(source) else None,
            "passive_recon_file": passive_source if os.path.exists(passive_source) else None,
        })
        doc.observations.append({
            "type": "stage_transition",
            "stage": "RECON",
            "agent": self.name,
            "created_at": utc_now(),
            "summary": "Recon artifacts normalized for InitialAccessAgent.",
        })
        if os.path.exists(passive_source):
            passive = read_json(passive_source, {})
            doc.observations.append({
                "type": "passive_recon_summary",
                "source": passive_source,
                "collection_summary": passive.get("collection_summary", {}),
                "target": passive.get("target", {}),
            })
        save_intel(output_path, doc)
        report = {
            "stage": "RECON",
            "agent": self.name,
            "timestamp": utc_now(),
            "source": source,
            "passive_source": passive_source,
            "output": output_path,
            "collection": collection_report,
            "asset_count": len(doc.assets),
            "observation_count": len(doc.observations),
            "simulated_only": True,
            "scope": doc.safety.get("scope"),
        }
        write_json(os.path.join(self.output_dir, "stage_1_recon_report.json"), report)
        log(self.name, f"saved {output_path}")
        return {"intel": asdict(doc), "report": report}

    def collect(
        self,
        source: str,
        passive_source: str,
        mode: str = "auto",
        listen_host: str = "0.0.0.0",
        listen_port: int = 14550,
        duration_s: int = 30,
        revalidate_s: int = 20,
        prediction_horizon_s: int = 60,
    ) -> dict[str, Any]:
        """Run every recon collection event before normalizing stage output."""
        selected_mode = self._select_collection_mode(mode)
        os.makedirs(self.output_dir, exist_ok=True)
        started_at = utc_now()

        if selected_mode == "docker":
            return self._collect_with_docker(
                source=source,
                passive_source=passive_source,
                duration_s=duration_s,
                revalidate_s=revalidate_s,
                prediction_horizon_s=prediction_horizon_s,
                started_at=started_at,
            )

        return self._collect_locally(
            source=source,
            passive_source=passive_source,
            listen_host=listen_host,
            listen_port=listen_port,
            duration_s=duration_s,
            revalidate_s=revalidate_s,
            prediction_horizon_s=prediction_horizon_s,
            started_at=started_at,
        )

    def _select_collection_mode(self, mode: str) -> str:
        if mode not in {"auto", "docker", "local"}:
            raise ValueError(f"unknown recon collection mode: {mode}")
        if mode != "auto":
            return mode
        if running_inside_docker():
            return "local"
        if Path("docker-compose.yml").exists() and os.path.normpath(self.output_dir) == "output":
            return "docker"
        return "local"

    def _collect_with_docker(
        self,
        source: str,
        passive_source: str,
        duration_s: int,
        revalidate_s: int,
        prediction_horizon_s: int,
        started_at: str,
    ) -> dict[str, Any]:
        env = {
            **os.environ,
            "RECON_DURATION_S": str(duration_s),
            "RECON_REVALIDATE_S": str(revalidate_s),
            "RECON_PREDICTION_HORIZON_S": str(prediction_horizon_s),
        }
        commands = [
            ["docker", "compose", "rm", "-f", "dah-recon"],
            ["docker", "compose", "--profile", "recon-lab", "up", "--build", "--no-deps", "dah-recon"],
        ]
        outputs: list[dict[str, Any]] = []
        for command in commands:
            proc = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
            outputs.append({
                "command": " ".join(command),
                "returncode": proc.returncode,
                "stdout_tail": proc.stdout[-4000:],
                "stderr_tail": proc.stderr[-4000:],
            })
            if proc.returncode != 0:
                return {
                    "mode": "docker",
                    "status": "failed",
                    "started_at": started_at,
                    "finished_at": utc_now(),
                    "source": source,
                    "passive_source": passive_source,
                    "commands": outputs,
                }
        return {
            "mode": "docker",
            "status": "ok" if os.path.exists(source) and os.path.exists(passive_source) else "missing_output",
            "started_at": started_at,
            "finished_at": utc_now(),
            "source": source,
            "passive_source": passive_source,
            "commands": outputs,
        }

    def _collect_locally(
        self,
        source: str,
        passive_source: str,
        listen_host: str,
        listen_port: int,
        duration_s: int,
        revalidate_s: int,
        prediction_horizon_s: int,
        started_at: str,
    ) -> dict[str, Any]:
        run(
            listen_host=listen_host,
            listen_port=listen_port,
            duration_s=duration_s,
            revalidate_s=revalidate_s,
            prediction_horizon_s=prediction_horizon_s,
            output_path=passive_source,
            chain_handoff_path=source,
        )
        return {
            "mode": "local",
            "status": "ok" if os.path.exists(source) and os.path.exists(passive_source) else "missing_output",
            "started_at": started_at,
            "finished_at": utc_now(),
            "source": source,
            "passive_source": passive_source,
            "listen": f"{listen_host}:{listen_port}",
            "duration_s": duration_s,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Passive MAVLink Recon — Low-Privilege Sentinel (DAH_SMU)"
    )
    parser.add_argument("--listen-host",           default=LISTEN_HOST)
    parser.add_argument("--listen-port",   type=int, default=LISTEN_PORT)
    parser.add_argument("--duration-s",    type=int, default=120)
    parser.add_argument("--revalidate-s",  type=int, default=20)
    parser.add_argument("--prediction-horizon-s", type=int, default=60)
    parser.add_argument("--output",               default="/app/output/passive_mavlink_intel.json")
    parser.add_argument("--chain-handoff",         default=None)
    parser.add_argument("--skip-phase0",   action="store_true",
                        help="Dashboard API 사전 정찰 생략 (완전 수동 모드)")
    args = parser.parse_args(argv)
    run(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        duration_s=args.duration_s,
        revalidate_s=args.revalidate_s,
        prediction_horizon_s=args.prediction_horizon_s,
        output_path=args.output,
        skip_phase0=args.skip_phase0,
        chain_handoff_path=args.chain_handoff,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
