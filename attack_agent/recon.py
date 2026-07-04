from __future__ import annotations

import argparse
import json
import math
import os
import socket
import time
import urllib.request
from typing import Any

from mavlink_parser import ParsedMavlinkFrame, parse_datagram


LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 14572

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))
DASHBOARD_URL = f"http://{DASHBOARD_HOST}:8080"

UAV_PLATFORM_ID = "UAV-001"
UAV_SYS_ID = 1
CONF_HIGH = 0.80
CONF_MEDIUM = 0.50

OA_LAT_MIN = 37.850
OA_LAT_MAX = 37.960
OA_LON_MIN = 126.790
OA_LON_MAX = 126.920

MAV_TYPE = {
    0: "GENERIC",
    1: "FIXED_WING",
    2: "QUADROTOR",
    10: "GROUND_ROVER",
    14: "ONBOARD_CONTROLLER",
}
MAV_STATE = {
    0: "UNINIT",
    1: "BOOT",
    2: "CALIBRATING",
    3: "STANDBY",
    4: "ACTIVE",
    5: "CRITICAL",
    6: "EMERGENCY",
    7: "POWEROFF",
}

_evt_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _send_event(message: str, level: str = "info", detail: str = "", status: str = "") -> None:
    event = {
        "platform_type": "AGENT",
        "agent_type": "ATK",
        "platform_id": "ATK-RECON",
        "source": "PIPELINE-RECON",
        "message": message,
        "detail": detail,
        "level": level,
        "status": status,
        "time": time.strftime("%H:%M:%S"),
    }
    try:
        _evt_sock.sendto(json.dumps(event).encode("utf-8"), (DASHBOARD_HOST, DASHBOARD_PORT))
    except Exception:
        pass


def _add_unique(values: list[Any], value: Any, limit: int = 20) -> None:
    if value is None or value in values:
        return
    values.append(value)
    if len(values) > limit:
        del values[0]


def _meters_per_lon_degree(lat_deg: float) -> float:
    return max(1.0, 111_320.0 * math.cos(math.radians(lat_deg)))


def _is_in_operational_area(lat: float | None, lon: float | None) -> bool | None:
    if lat is None or lon is None:
        return None
    return OA_LAT_MIN <= lat <= OA_LAT_MAX and OA_LON_MIN <= lon <= OA_LON_MAX


def phase0_api_recon() -> dict[str, Any]:
    baseline: dict[str, Any] = {"http_requests": 0, "api_available": False}
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/live", timeout=3) as response:
            live = json.loads(response.read())
        baseline["http_requests"] += 1
        baseline["api_available"] = True
    except Exception as exc:
        live = {}
        baseline["live_error"] = str(exc)
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/failsafe", timeout=3) as response:
            policy = json.loads(response.read())
        baseline["http_requests"] += 1
    except Exception as exc:
        policy = {}
        baseline["failsafe_error"] = str(exc)

    platforms = {p.get("platform_id"): p for p in live.get("platforms", [])}
    uav = platforms.get(UAV_PLATFORM_ID, {})
    ticn = uav.get("ticn", {}) if isinstance(uav.get("ticn"), dict) else {}
    mission_state = live.get("mission_state", {}) if isinstance(live.get("mission_state"), dict) else {}

    baseline.update({
        "uav_lat": uav.get("lat"),
        "uav_lon": uav.get("lon"),
        "uav_alt": uav.get("alt"),
        "uav_mode": uav.get("mode"),
        "uav_fuel": uav.get("fuel", uav.get("battery")),
        "uav_speed": uav.get("speed"),
        "uav_status": uav.get("status"),
        "mission_phase": mission_state.get("phase"),
        "mission_desc": mission_state.get("desc"),
        "ticn_loss_pct": ticn.get("loss_pct", 0),
        "ticn_link_quality": ticn.get("link_quality", 100),
        "hb_timeout_sec": policy.get("heartbeat", {}).get("timeout_sec", 5),
        "loss_critical_pct": policy.get("packet_loss", {}).get("critical_pct", 15),
        "latency_critical_ms": policy.get("latency", {}).get("critical_ms", 1500),
        "failsafe_action": policy.get("failsafe_action", "LOITER"),
    })
    return baseline


def _speed_from_positions(prev: dict[str, Any], cur: dict[str, Any]) -> float | None:
    dt = float(cur.get("received_s", 0.0)) - float(prev.get("received_s", 0.0))
    if dt <= 0.05:
        return None
    lat = float(prev["lat_deg"])
    north_m = (float(cur["lat_deg"]) - lat) * 111_320.0
    east_m = (float(cur["lon_deg"]) - float(prev["lon_deg"])) * _meters_per_lon_degree(lat)
    return math.sqrt(north_m * north_m + east_m * east_m) / dt


def _physical_consistency_check(rec: dict[str, Any]) -> bool:
    samples = rec.get("position_history", [])
    if len(samples) < 2:
        return False
    calculated = _speed_from_positions(samples[-2], samples[-1])
    reported = rec.get("ground_speed_mps")
    if calculated is None or reported is None:
        return False
    if calculated < 1.0 and float(reported) < 2.0:
        return True
    if calculated <= 0 or float(reported) <= 0:
        return False
    ratio = max(calculated, float(reported)) / max(0.01, min(calculated, float(reported)))
    return ratio < 4.5


def _cross_message_validation(rec: dict[str, Any]) -> bool:
    if rec.get("is_armed") and rec.get("alt_m", 0) < -10:
        return False
    if rec.get("system_status") == "ACTIVE" and rec.get("position_samples", 0) == 0:
        return False
    if rec.get("battery_pct") is not None and float(rec.get("battery_pct")) < -1:
        return False
    return bool(rec.get("last_heartbeat") or rec.get("position_samples", 0) > 0)


def confidence_label(score: float) -> str:
    if score >= CONF_HIGH:
        return "HIGH - usable for controlled follow-on validation"
    if score >= CONF_MEDIUM:
        return "MEDIUM - short-window revalidation recommended"
    return "LOW - incomplete or stale observation"


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

    integrity_ok = rec.get("crc_valid_frames", 0) > 0 or rec.get("json_frames", 0) > 0
    integrity_note = "json_pipeline" if rec.get("json_frames", 0) > 0 else f"crc_valid={rec.get('crc_valid_frames', 0)}"
    factors["frame_or_pipeline_integrity"] = {
        "ok": integrity_ok,
        "weight": 0.15 if integrity_ok else 0.0,
        "note": integrity_note,
    }

    last_seen = rec.get("last_seen")
    fresh = bool(last_seen and now - float(last_seen) <= 90.0)
    factors["freshness"] = {"ok": fresh, "weight": 0.10 if fresh else 0.0}

    score = round(sum(item["weight"] for item in factors.values()), 2)
    return {"score": min(1.0, score), "label": confidence_label(score), "factors": factors}


def confidence_score(rec: dict[str, Any]) -> float:
    return float(confidence_details(rec)["score"])


class IntelligenceReport:
    def __init__(self) -> None:
        self.assets: dict[int, dict[str, Any]] = {}
        self.packet_count = 0
        self.parse_errors = 0
        self.unknown_msg_count = 0
        self.msg_type_counts: dict[str, int] = {}
        self.pipeline_sources: dict[str, int] = {}
        self.signed_frames = 0
        self.unsigned_frames = 0
        self.crc_valid_frames = 0
        self.crc_invalid_frames = 0
        self.crc_unknown_frames = 0
        self.json_packet_count = 0
        self.start_time = time.time()

    def _get(self, sys_id: int) -> dict[str, Any]:
        if sys_id not in self.assets:
            self.assets[sys_id] = {
                "sys_id": sys_id,
                "first_seen": time.time(),
                "last_seen": None,
                "packet_count": 0,
                "source_ips": [],
                "message_counts": {},
                "position_history": [],
                "command_acks": [],
                "mission_items": [],
            }
        return self.assets[sys_id]

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

    def record_frame(self, frame: ParsedMavlinkFrame, source_ip: str) -> None:
        rec = self._get(frame.system_id)
        rec["packet_count"] += 1
        rec["last_seen"] = time.time()
        rec["component_id"] = frame.component_id
        rec["last_sequence"] = frame.sequence
        rec["last_message"] = frame.message_name
        rec["signed_frames"] = rec.get("signed_frames", 0) + (1 if frame.signed else 0)
        rec["unsigned_frames"] = rec.get("unsigned_frames", 0) + (0 if frame.signed else 1)
        if frame.crc_valid is True:
            rec["crc_valid_frames"] = rec.get("crc_valid_frames", 0) + 1
        elif frame.crc_valid is False:
            rec["crc_invalid_frames"] = rec.get("crc_invalid_frames", 0) + 1
        else:
            rec["crc_unknown_frames"] = rec.get("crc_unknown_frames", 0) + 1
        _add_unique(rec["source_ips"], source_ip)
        counts = rec["message_counts"]
        counts[frame.message_name] = counts.get(frame.message_name, 0) + 1

        fields = frame.fields
        if frame.message_name == "HEARTBEAT":
            rec["mav_type"] = MAV_TYPE.get(fields.get("type", -1), f"TYPE_{fields.get('type')}")
            rec["system_status"] = MAV_STATE.get(fields.get("system_status", -1), "UNKNOWN")
            rec["base_mode"] = fields.get("base_mode", 0)
            rec["is_armed"] = bool(fields.get("base_mode", 0) & 0x80)
            rec["is_guided"] = bool(fields.get("base_mode", 0) & 0x08)
            rec["last_heartbeat"] = time.time()
        elif frame.message_name in {"GLOBAL_POSITION_INT", "UTM_GLOBAL_POSITION"}:
            self._record_position(rec, {
                "lat": fields.get("lat", 0) / 1e7,
                "lon": fields.get("lon", 0) / 1e7,
                "alt": fields.get("alt", 0) / 1000.0,
                "speed_mps": math.sqrt((fields.get("vx", 0) / 100.0) ** 2 + (fields.get("vy", 0) / 100.0) ** 2),
                "heading_deg": fields.get("hdg", 0) / 100.0,
                "message": frame.message_name,
            })
        elif frame.message_name == "SYS_STATUS":
            rec["battery_pct"] = fields.get("battery_remaining", -1)
            rec["drop_rate_comm"] = fields.get("drop_rate_comm", 0)
            rec["errors_comm"] = fields.get("errors_comm", 0)

    def record_json_telemetry(self, payload: dict[str, Any], source_ip: str) -> None:
        platform_id = str(payload.get("platform_id") or payload.get("target_platform_id") or "UNKNOWN")
        if platform_id == UAV_PLATFORM_ID:
            sys_id = UAV_SYS_ID
        elif platform_id.startswith("UGV"):
            sys_id = int(payload.get("sys_id") or 20)
        else:
            sys_id = int(payload.get("sys_id") or 900)

        rec = self._get(sys_id)
        rec["packet_count"] += 1
        rec["last_seen"] = time.time()
        rec["platform_id"] = platform_id
        rec["source_pipeline"] = "gcs_json_fanout"
        rec["last_message"] = payload.get("message_type", "JSON_TELEMETRY")
        rec["json_frames"] = rec.get("json_frames", 0) + 1
        rec["component_id"] = rec.get("component_id", 0)
        _add_unique(rec["source_ips"], source_ip)

        source = str(payload.get("source") or "unknown")
        self.pipeline_sources[source] = self.pipeline_sources.get(source, 0) + 1
        self.record_msg_type("JSON_TELEMETRY")
        counts = rec["message_counts"]
        counts["JSON_TELEMETRY"] = counts.get("JSON_TELEMETRY", 0) + 1

        lat = payload.get("lat")
        lon = payload.get("lon")
        alt = payload.get("alt")
        if lat is not None and lon is not None:
            speed_kmh = float(payload.get("speed") or 0.0)
            self._record_position(rec, {
                "lat": float(lat),
                "lon": float(lon),
                "alt": float(alt or 0.0),
                "speed_mps": speed_kmh / 3.6,
                "heading_deg": float(payload.get("heading") or payload.get("hdg") or 0.0),
                "message": "JSON_TELEMETRY",
            })

        rec["mav_type"] = "FIXED_WING" if platform_id == UAV_PLATFORM_ID else payload.get("platform_type", "UNKNOWN")
        rec["system_status"] = payload.get("status") or "ACTIVE"
        rec["is_armed"] = True if platform_id == UAV_PLATFORM_ID else rec.get("is_armed", False)
        rec["is_guided"] = True if platform_id == UAV_PLATFORM_ID else rec.get("is_guided", False)
        rec["last_heartbeat"] = time.time()
        if payload.get("fuel") is not None:
            rec["battery_pct"] = payload.get("fuel")
        elif payload.get("battery") is not None:
            rec["battery_pct"] = payload.get("battery")
        ticn = payload.get("ticn") if isinstance(payload.get("ticn"), dict) else {}
        rec["drop_rate_comm"] = ticn.get("loss_pct", rec.get("drop_rate_comm", 0))
        rec["pipeline_seq"] = payload.get("seq")

    def _record_position(self, rec: dict[str, Any], item: dict[str, Any]) -> None:
        sample = {
            "received_s": time.time(),
            "message": item["message"],
            "lat_deg": round(float(item["lat"]), 7),
            "lon_deg": round(float(item["lon"]), 7),
            "alt_m": round(float(item["alt"]), 1),
            "rel_alt_m": round(float(item["alt"]), 1),
            "vx_mps": round(float(item["speed_mps"]), 2),
            "vy_mps": 0.0,
            "vz_mps": 0.0,
            "heading_deg": round(float(item["heading_deg"]), 1),
        }
        rec.update({
            "lat_deg": sample["lat_deg"],
            "lon_deg": sample["lon_deg"],
            "alt_m": sample["alt_m"],
            "rel_alt_m": sample["rel_alt_m"],
            "velocity_mps": [sample["vx_mps"], 0.0, 0.0],
            "ground_speed_mps": sample["vx_mps"],
            "heading_deg": sample["heading_deg"],
            "position_samples": rec.get("position_samples", 0) + 1,
            "in_operational_area": _is_in_operational_area(sample["lat_deg"], sample["lon_deg"]),
        })
        history = rec.setdefault("position_history", [])
        history.append(sample)
        if len(history) > 30:
            del history[0]
        rec["trail"] = [[p["lat_deg"], p["lon_deg"], p["alt_m"]] for p in history[-20:]]

    def sanitized_assets(self) -> dict[str, Any]:
        return {str(sid): dict(rec) for sid, rec in self.assets.items()}

    def summary(self) -> dict[str, Any]:
        return {
            "packet_count": self.packet_count,
            "parse_errors": self.parse_errors,
            "unknown_msgs": self.unknown_msg_count,
            "json_packets": self.json_packet_count,
            "pipeline_sources": dict(sorted(self.pipeline_sources.items())),
            "asset_count": len(self.assets),
            "uav001_identified": UAV_SYS_ID in self.assets,
            "msg_type_counts": dict(sorted(self.msg_type_counts.items())),
            "signed_frames": self.signed_frames,
            "unsigned_frames": self.unsigned_frames,
            "crc_valid_frames": self.crc_valid_frames,
            "crc_invalid_frames": self.crc_invalid_frames,
            "crc_unknown_frames": self.crc_unknown_frames,
        }

    def print_summary(self) -> None:
        elapsed = time.time() - self.start_time
        print("\n" + "=" * 68, flush=True)
        print("[PIPELINE-RECON] collection complete", flush=True)
        print(f"  elapsed_s:       {elapsed:.1f}", flush=True)
        print(f"  packets:         {self.packet_count}", flush=True)
        print(f"  json_packets:    {self.json_packet_count}", flush=True)
        print(f"  parse_errors:    {self.parse_errors}", flush=True)
        print(f"  assets:          {len(self.assets)}", flush=True)
        print(f"  UAV-001:         {'Y' if UAV_SYS_ID in self.assets else 'N'}", flush=True)
        print(f"  msg_types:       {dict(sorted(self.msg_type_counts.items()))}", flush=True)
        print(f"  sources:         {dict(sorted(self.pipeline_sources.items()))}", flush=True)
        print("=" * 68, flush=True)
        for sid, rec in sorted(self.assets.items()):
            cd = confidence_details(rec)
            print(
                f"  [SYS_ID={sid}] platform={rec.get('platform_id', rec.get('mav_type', 'UNKNOWN'))} "
                f"state={rec.get('system_status', 'UNKNOWN')} confidence={cd['score']:.2f}",
                flush=True,
            )
            if "lat_deg" in rec:
                print(
                    f"    pos={rec['lat_deg']},{rec['lon_deg']} alt={rec.get('alt_m')}m "
                    f"speed={rec.get('ground_speed_mps')}m/s heading={rec.get('heading_deg')}",
                    flush=True,
                )


def classify_pattern(rec: dict[str, Any]) -> str:
    samples = rec.get("position_history", [])
    speed = float(rec.get("ground_speed_mps") or 0.0)
    if len(samples) >= 3:
        alt_delta = samples[-1]["alt_m"] - samples[0]["alt_m"]
        heading_values = [float(s.get("heading_deg") or 0.0) for s in samples[-5:]]
        heading_span = max(heading_values) - min(heading_values) if heading_values else 0.0
        if alt_delta < -50 and speed < 100:
            return "DESCENT_OR_RTL"
        if heading_span > 30:
            return "PATROL_TURNING"
    if speed < 10 and rec.get("position_samples", 0) >= 2:
        return "LOITER_HOLDING"
    if speed > 80:
        in_oa = _is_in_operational_area(rec.get("lat_deg"), rec.get("lon_deg"))
        return "PATROL_TRANSIT" if in_oa else "OUT_OF_AREA"
    if rec.get("position_samples", 0):
        return "TRANSIT"
    return "INSUFFICIENT_DATA"


def predict_position(rec: dict[str, Any], horizon_s: int) -> dict[str, Any] | None:
    if rec.get("lat_deg") is None or rec.get("lon_deg") is None or not rec.get("velocity_mps"):
        return None
    lat = float(rec["lat_deg"])
    lon = float(rec["lon_deg"])
    alt = float(rec.get("alt_m") or 0.0)
    vx_north, vy_east, vz_down = [float(v) for v in rec.get("velocity_mps", [0.0, 0.0, 0.0])]
    pred_lat = lat + (vx_north * horizon_s) / 111_320.0
    pred_lon = lon + (vy_east * horizon_s) / _meters_per_lon_degree(lat)
    pred_alt = alt - vz_down * horizon_s
    pattern = classify_pattern(rec)
    penalty = 50.0 if pattern in {"PATROL_TURNING", "DESCENT_OR_RTL"} else 15.0
    return {
        "model": "constant_velocity_short_horizon",
        "horizon_s": horizon_s,
        "lat": round(pred_lat, 7),
        "lon": round(pred_lon, 7),
        "alt_m": round(pred_alt, 1),
        "expected_error_m": round(penalty + 0.5 * horizon_s, 1),
        "in_operational_area": _is_in_operational_area(round(pred_lat, 7), round(pred_lon, 7)),
        "limits": "Use for controlled scenario selection only; revalidate before follow-on validation.",
    }


def follow_on_candidates(rec: dict[str, Any], score: float, baseline: dict[str, Any] | None) -> list[dict[str, Any]]:
    if score < CONF_MEDIUM:
        return []
    candidates: list[dict[str, Any]] = []
    pattern = classify_pattern(rec)
    if rec.get("lat_deg") is not None:
        candidates.append({
            "validation": "position-integrity-review",
            "reason": "JSON telemetry exposes current WGS84 position and short-horizon prediction inputs.",
            "pattern": pattern,
        })
    if rec.get("battery_pct") is not None:
        candidates.append({
            "validation": "health-state-review",
            "reason": "Telemetry pipeline exposes fuel/battery and link-state fields.",
            "battery_pct": rec.get("battery_pct"),
        })
    if baseline and baseline.get("api_available"):
        candidates.append({
            "validation": "failsafe-policy-review",
            "reason": "Dashboard API exposes heartbeat, packet-loss, latency, and failsafe policy values.",
            "hb_timeout_sec": baseline.get("hb_timeout_sec"),
            "loss_critical_pct": baseline.get("loss_critical_pct"),
        })
    return candidates


def timing_recommendations(rec: dict[str, Any], score: float) -> list[dict[str, str]]:
    if score < CONF_MEDIUM:
        return [{"status": "hold", "reason": "confidence below medium threshold; revalidation required"}]
    pattern = classify_pattern(rec)
    if pattern == "LOITER_HOLDING":
        return [{"candidate": "link/failsafe validation", "reason": "asset appears stable enough for controlled comparison"}]
    if pattern == "PATROL_TURNING":
        return [{"candidate": "position-integrity validation", "reason": "turning phase is sensitive to trajectory interpretation"}]
    if pattern == "PATROL_TRANSIT":
        return [{"candidate": "pipeline exposure validation", "reason": "high-speed transit provides rich kinematic telemetry"}]
    return [{"candidate": "short-window revalidation", "reason": f"pattern={pattern}"}]


def blue_team_mapping() -> list[dict[str, Any]]:
    return [
        {
            "layer": "GCS telemetry fan-out",
            "expected_visibility": "high",
            "reason": "GCS explicitly sends JSON telemetry to Dashboard, Collector, Router, and the lab Recon Tap.",
            "recommended_control": "inventory all configured fan-out recipients and alert on unexpected telemetry taps.",
        },
        {
            "layer": "Dashboard/GCS HTTP audit",
            "expected_visibility": "medium",
            "reason": "Phase 0 optionally calls /api/live and /api/failsafe; Phase 1 uses UDP JSON tap only.",
            "recommended_control": "correlate API readers with UDP telemetry recipients.",
        },
        {
            "layer": "Container runtime",
            "expected_visibility": "medium-high",
            "reason": "dah-recon container, command line, network membership, and output volume are visible.",
            "recommended_control": "restrict cyber-lab profile containers and review ops_net membership.",
        },
        {
            "layer": "Protocol monitor",
            "expected_visibility": "high for exposure",
            "reason": "MAVLink is converted to JSON at the companion/GCS boundary and propagated through the pipeline.",
            "recommended_control": "treat telemetry fan-out as a data boundary; minimize fields and apply recipient allowlists.",
        },
    ]


def ghost_sentinel_assessment() -> dict[str, Any]:
    return {
        "implemented": False,
        "purpose": "Threat-model comparison only; this module uses no raw socket and no CAP_NET_RAW.",
        "current_mode": "DAH_SMU JSON telemetry pipeline recon",
        "defensive_question": "Can the environment detect an unexpected recipient in the GCS telemetry fan-out path?",
    }


def _open_socket(listen_host: str, listen_port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
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
            if isinstance(item, dict):
                report.json_packet_count += 1
                report.record_json_telemetry(item, addr[0])
                print(
                    f"  [{label}] {addr[0]} json platform={item.get('platform_id', item.get('target_platform_id', '?'))} "
                    f"source={item.get('source', 'unknown')}",
                    flush=True,
                )
                continue
            if isinstance(item, ParsedMavlinkFrame):
                report.record_frame_security(item)
                report.record_msg_type(item.message_name)
                report.record_frame(item, addr[0])
                print(
                    f"  [{label}] {addr[0]} mavlink sys={item.system_id} msg={item.message_name} "
                    f"signed={'Y' if item.signed else 'N'} crc={item.crc_valid}",
                    flush=True,
                )


def _merge_better_observations(primary: IntelligenceReport, secondary: IntelligenceReport) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for sid, new_rec in secondary.assets.items():
        old_rec = primary.assets.get(sid)
        if old_rec is None:
            primary.assets[sid] = new_rec
            changes.append({"sys_id": sid, "action": "added", "new_score": confidence_score(new_rec)})
            continue
        old_score = confidence_score(old_rec)
        new_score = confidence_score(new_rec)
        if new_score > old_score:
            old_rec.update(new_rec)
            changes.append({"sys_id": sid, "action": "improved", "old_score": old_score, "new_score": new_score})
        else:
            changes.append({"sys_id": sid, "action": "kept", "old_score": old_score, "new_score": new_score})
    primary.json_packet_count += secondary.json_packet_count
    for key, value in secondary.pipeline_sources.items():
        primary.pipeline_sources[key] = primary.pipeline_sources.get(key, 0) + value
    return changes


def _write_handoff(path: str, uav_rec: dict[str, Any] | None, cd: dict[str, Any], candidates: list[dict[str, Any]], baseline: dict[str, Any] | None) -> None:
    handoff = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "recon_source": "gcs_json_fanout_tap",
        "target": {"platform_id": UAV_PLATFORM_ID, "sys_id": UAV_SYS_ID},
        "confidence": {"score": cd.get("score"), "label": cd.get("label")},
        "uav_state": {
            "armed": uav_rec.get("is_armed") if uav_rec else None,
            "alt_m": uav_rec.get("alt_m") if uav_rec else None,
            "lat": uav_rec.get("lat_deg") if uav_rec else None,
            "lon": uav_rec.get("lon_deg") if uav_rec else None,
            "speed_mps": uav_rec.get("ground_speed_mps") if uav_rec else None,
            "heading_deg": uav_rec.get("heading_deg") if uav_rec else None,
            "battery_pct": uav_rec.get("battery_pct") if uav_rec else None,
            "pattern": classify_pattern(uav_rec) if uav_rec else None,
            "in_oa": uav_rec.get("in_operational_area") if uav_rec else None,
        },
        "api_baseline": baseline,
        "follow_on_validations": candidates,
        "operator_approval_required": True,
    }
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(handoff, handle, ensure_ascii=False, indent=2, default=str)


def run(
    listen_host: str,
    listen_port: int,
    duration_s: int,
    revalidate_s: int,
    prediction_horizon_s: int,
    output_path: str | None,
    skip_phase0: bool = False,
) -> dict[str, Any]:
    print("[pipeline-recon] DAH_SMU telemetry pipeline recon starting", flush=True)
    print(f"  listen={listen_host}:{listen_port} duration_s={duration_s}", flush=True)
    print("  scope=controlled DAH emulation; no raw socket and no packet injection", flush=True)
    _send_event("pipeline recon started", detail=f"listen={listen_host}:{listen_port}")

    baseline: dict[str, Any] | None = None
    http_requests_total = 0
    if not skip_phase0:
        baseline = phase0_api_recon()
        http_requests_total = int(baseline.get("http_requests", 0))
        print(f"[pipeline-recon] phase0 API requests={http_requests_total}", flush=True)
    else:
        print("[pipeline-recon] phase0 skipped", flush=True)

    report = IntelligenceReport()
    sock = _open_socket(listen_host, listen_port)
    _collect(sock, report, time.time() + duration_s, "phase1")
    sock.close()
    report.print_summary()

    revalidation_changes: list[dict[str, Any]] = []
    needs_reval = [sid for sid, rec in report.assets.items() if confidence_score(rec) < CONF_HIGH]
    if revalidate_s > 0 and needs_reval:
        print(f"[pipeline-recon] revalidating sys_ids={needs_reval}", flush=True)
        second = IntelligenceReport()
        sock2 = _open_socket(listen_host, listen_port)
        _collect(sock2, second, time.time() + revalidate_s, "revalidate")
        sock2.close()
        revalidation_changes = _merge_better_observations(report, second)
    elif revalidate_s > 0:
        print("[pipeline-recon] revalidation skipped: all assets high confidence", flush=True)

    uav_rec = report.assets.get(UAV_SYS_ID)
    uav_cd = confidence_details(uav_rec) if uav_rec else {"score": 0.0, "label": "NO_DATA", "factors": {}}
    prediction = predict_position(uav_rec or {}, prediction_horizon_s) if uav_rec else None
    candidates = follow_on_candidates(uav_rec or {}, float(uav_cd["score"]), baseline)
    timing_recs = timing_recommendations(uav_rec or {}, float(uav_cd["score"]))

    intel = {
        "meta": {
            "attack": "pipeline_recon",
            "scenario": "Low-Privilege Sentinel (DAH_SMU JSON telemetry pipeline)",
            "threat_model": "low-privilege observer explicitly attached to GCS telemetry fan-out in the DAH_SMU lab",
            "duration_s": duration_s,
            "revalidate_s": revalidate_s,
            "prediction_horizon_s": prediction_horizon_s,
            "http_requests": http_requests_total,
            "gcs_audit_trace": http_requests_total > 0,
            "network_ids_visible": True,
            "raw_socket_used": False,
            "cap_net_raw_required": False,
            "listen_port": listen_port,
            "dah_smu_target": UAV_PLATFORM_ID,
        },
        "phase0_api_baseline": baseline,
        "collection_summary": report.summary(),
        "assets": report.sanitized_assets(),
        "uav001": {
            "sys_id": UAV_SYS_ID,
            "confidence": uav_cd,
            "state": {
                key: uav_rec.get(key) for key in [
                    "platform_id", "mav_type", "system_status", "is_armed", "is_guided",
                    "lat_deg", "lon_deg", "alt_m", "ground_speed_mps", "heading_deg",
                    "battery_pct", "drop_rate_comm", "pipeline_seq", "in_operational_area",
                ]
            } if uav_rec else None,
            "pattern": classify_pattern(uav_rec) if uav_rec else None,
            "prediction": prediction,
            "timing_recs": timing_recs,
        },
        "follow_on_validations": candidates,
        "revalidation": revalidation_changes,
        "blue_team_mapping": blue_team_mapping(),
        "ghost_sentinel": ghost_sentinel_assessment(),
    }

    print("[pipeline-recon] follow-on validation summary", flush=True)
    print(json.dumps({
        "uav001_confidence": uav_cd.get("score"),
        "pattern": intel["uav001"]["pattern"],
        "candidates": candidates,
    }, ensure_ascii=False, indent=2), flush=True)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(intel, handle, ensure_ascii=False, indent=2, default=str)
        handoff_path = os.path.join(os.path.dirname(output_path), "intel_handoff.json")
        _write_handoff(handoff_path, uav_rec, uav_cd, candidates, baseline)
        print(f"[pipeline-recon] result saved: {output_path}", flush=True)
        print(f"[pipeline-recon] handoff saved: {handoff_path}", flush=True)
    else:
        print(json.dumps(intel, ensure_ascii=False, indent=2, default=str), flush=True)
    return intel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DAH_SMU telemetry pipeline recon")
    parser.add_argument("--listen-host", default=LISTEN_HOST)
    parser.add_argument("--listen-port", type=int, default=LISTEN_PORT)
    parser.add_argument("--duration-s", type=int, default=120)
    parser.add_argument("--revalidate-s", type=int, default=20)
    parser.add_argument("--prediction-horizon-s", type=int, default=60)
    parser.add_argument("--output", default="/app/output/passive_mavlink_intel.json")
    parser.add_argument("--skip-phase0", action="store_true")
    args = parser.parse_args(argv)
    run(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        duration_s=args.duration_s,
        revalidate_s=args.revalidate_s,
        prediction_horizon_s=args.prediction_horizon_s,
        output_path=args.output,
        skip_phase0=args.skip_phase0,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
