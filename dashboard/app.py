import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from flask import Flask, jsonify, render_template, request
from pymavlink import mavutil

app = Flask(__name__)

MISSION_CONTROL_URL   = os.getenv("MISSION_CONTROL_URL", "http://mission-control:8080")
GCS_URL               = os.getenv("GCS_URL",            "http://dah-gcs:8080")
GCS_TELEMETRY_PORT    = int(os.getenv("ROUTER_TELEMETRY_PORT", "14571"))
UAV_HOST              = os.getenv("UAV_HOST",     "172.31.50.10")
UAV_CMD_PORT          = int(os.getenv("UAV_CMD_PORT", "14551"))
GCS_SYS_ID            = 255  # 정상 GCS SYS_ID

# UDP로 직접 수신한 플랫폼 상태 + 로컬 이벤트
router_platforms: dict = {}
local_events: deque  = deque(maxlen=100)
agent_events: deque  = deque(maxlen=300)
failsafe_sim: dict = {
    "active": False,
    "vehicle_id": None,
    "action": None,
    "lat": None,
    "lon": None,
    "start_alt": None,
    "started_at": None,
    "descent_rate_mps": 25.0,
    "source": None,
}

# 임무 상태 (C2 명령 기반)
mission_state: dict = {
    "phase":   "EN_ROUTE",
    "desc":    "목표 지역으로 이동 중",
    "advice":  "경로 유지 및 주변 감시 지속",
    "cmd":     None,
    "cmd_at":  None,
    "cmd_by":  "SYSTEM",
}


def update_mission_state(update: dict):
    mission_state.update(update)


def is_failsafe_sim_active():
    return bool(failsafe_sim.get("active"))


def _as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _latest_vehicle_state(vehicle_id: str) -> dict:
    return router_platforms.get(vehicle_id, {})


def activate_failsafe_sim(vehicle_id: str, action: str = "LAND", source: str = "attack_chain"):
    """Dashboard-local safe simulation: freeze position and descend without sending flight commands."""
    current = _latest_vehicle_state(vehicle_id)
    current_alt = _as_float(current.get("alt", current.get("relative_alt")), 0.0)
    visible_start_alt = max(current_alt, 120.0)

    failsafe_sim.update({
        "active": True,
        "vehicle_id": vehicle_id,
        "action": action,
        "lat": current.get("lat"),
        "lon": current.get("lon"),
        "start_alt": visible_start_alt,
        "started_at": time.time(),
        "descent_rate_mps": 25.0,
        "source": source,
    })


def _failsafe_altitude():
    if not is_failsafe_sim_active():
        return None
    started_at = _as_float(failsafe_sim.get("started_at"), time.time())
    elapsed = max(0.0, time.time() - started_at)
    start_alt = _as_float(failsafe_sim.get("start_alt"), 0.0)
    descent_rate = _as_float(failsafe_sim.get("descent_rate_mps"), 25.0)
    return max(0.0, start_alt - (elapsed * descent_rate))


def current_failsafe_snapshot():
    altitude = _failsafe_altitude()
    if altitude is None:
        return {"active": False}

    landed = altitude <= 0.0
    return {
        **failsafe_sim,
        "alt": round(altitude, 2),
        "mode": "FAILSAFE_LANDED" if landed else "FAILSAFE_LAND",
        "status": "LANDED" if landed else "FAILSAFE_TRIGGERED",
        "mission_state": "FAILSAFE_LANDED" if landed else "FAILSAFE_LAND",
        "simulated_only": True,
        "scope": "LOCAL_DOCKER_TESTBED_ONLY",
    }


def _sync_failsafe_mission_state(altitude: float):
    if altitude <= 0.0 and mission_state.get("phase") != "FAILSAFE_LANDED":
        update_mission_state({
            "phase": "FAILSAFE_LANDED",
            "desc": "Fail-safe 시뮬레이션 완료: UAV가 현재 위치에서 지면에 도달",
            "advice": "임무 중단 상태 유지, 원인 분석 및 복구 절차 전까지 재출격 금지",
            "cmd": "FAILSAFE_LANDED",
            "cmd_at": time.strftime("%H:%M:%S"),
            "cmd_by": "Safe Follow-up Simulation Module",
        })


def apply_failsafe_simulation(platforms: list[dict]) -> list[dict]:
    """Overlay a safe fail-safe result on live telemetry without mutating the real UAV link."""
    if not is_failsafe_sim_active():
        return platforms

    vehicle_id = failsafe_sim.get("vehicle_id") or "UAV-001"
    altitude = _failsafe_altitude()
    if altitude is None:
        return platforms

    _sync_failsafe_mission_state(altitude)
    landed = altitude <= 0.0
    mode = "FAILSAFE_LANDED" if landed else "FAILSAFE_LAND"
    status = "LANDED" if landed else "FAILSAFE_TRIGGERED"
    updated = []
    found = False

    for platform in platforms:
        if platform.get("platform_id") != vehicle_id:
            updated.append(platform)
            continue

        found = True
        if failsafe_sim.get("lat") is None:
            failsafe_sim["lat"] = platform.get("lat")
        if failsafe_sim.get("lon") is None:
            failsafe_sim["lon"] = platform.get("lon")

        updated.append({
            **platform,
            "lat": failsafe_sim.get("lat"),
            "lon": failsafe_sim.get("lon"),
            "alt": round(altitude, 2),
            "speed": 0,
            "mode": mode,
            "status": status,
            "mission": "FAILSAFE_STOPPED",
            "failsafe_active": True,
            "failsafe_action": "LAND",
            "simulated_only": True,
            "source": f"{platform.get('source', 'telemetry')}/failsafe_sim",
        })

    if not found:
        updated.append({
            "platform_id": vehicle_id,
            "platform_type": "UAV",
            "lat": failsafe_sim.get("lat"),
            "lon": failsafe_sim.get("lon"),
            "alt": round(altitude, 2),
            "speed": 0,
            "mode": mode,
            "status": status,
            "mission": "FAILSAFE_STOPPED",
            "failsafe_active": True,
            "failsafe_action": "LAND",
            "simulated_only": True,
            "source": "failsafe_sim",
        })

    return updated


def record_agent_event(payload: dict):
    message_type = payload.get("message_type", "")
    severity = payload.get("severity", payload.get("level", "info"))
    status = payload.get("integrity_status", payload.get("status", "INFO"))
    mutation = payload.get("frame_mutation_mode", "")
    vehicle_id = payload.get("vehicle_id", payload.get("target", ""))

    if message_type == "protocol_integrity_alert":
        message = f"{vehicle_id} 프로토콜 무결성 경고: {status}"
        detail = f"{mutation} | evidence={payload.get('evidence', {})}"
        level = "warn" if severity in {"HIGH", "MEDIUM"} else "info"
        update_mission_state({
            "phase": "INTEGRITY_ALERT",
            "desc": "프로토콜 프레임 무결성 이상 탐지",
            "advice": "해당 링크 격리 검토, 프레임 검증 로그 확인, 제어 명령 신뢰성 재검증",
            "cmd": status,
            "cmd_at": time.strftime("%H:%M:%S"),
            "cmd_by": "Synthetic Protocol Integrity Monitor",
        })
    elif message_type == "link_degradation_alert":
        vehicle_id = vehicle_id or "UAV-001"
        activate_failsafe_sim(vehicle_id, action="LAND", source=payload.get("source", "attack_chain"))
        message = payload.get("message", f"{vehicle_id or '전술 링크'} 링크 저하 시뮬레이션")
        raw_detail = payload.get("detail", f"evidence={payload.get('evidence', {})}")
        detail = f"{raw_detail} | fail-safe LAND overlay activated"
        level = "warn" if severity in {"HIGH", "MEDIUM"} else "info"
        update_mission_state({
            "phase": "FAILSAFE_LAND",
            "desc": "Fail-safe 유도 성공: 현재 위치 정지 후 비상 하강 시뮬레이션",
            "advice": "임무 진행 중단, 위치 고정 및 고도 하강 상태 확인",
            "cmd": "FAILSAFE_TRIGGERED",
            "cmd_at": time.strftime("%H:%M:%S"),
            "cmd_by": "Safe Follow-up Simulation Module",
        })
    else:
        message = payload.get("message", "agent event")
        detail = payload.get("detail", "")
        level = payload.get("level", "info")

    agent_events.appendleft({
        "time": payload.get("time", time.strftime("%H:%M:%S")),
        "level": level,
        "agent_type": payload.get("agent_type", message_type or "agent"),
        "source": payload.get("source", "attack_chain"),
        "message": message,
        "detail": detail,
        "status": status,
    })

    local_events.appendleft({
        "type": "attack",
        "time": time.strftime("%H:%M:%S"),
        "level": level,
        "source": payload.get("source", "attack_chain"),
        "message": message,
        "status": status,
    })

PHASE_MAP = {
    "HOLD":    {"phase": "LOITER",     "desc": "지정 구역 선회 대기",       "advice": "현재 좌표 유지, 추가 명령 대기"},
    "MONITOR": {"phase": "ON_STATION", "desc": "감시 구역 집중 스캔",        "advice": "센서 전방위 스캔, 이상 징후 보고"},
    "PAUSE":   {"phase": "PAUSED",     "desc": "임무 일시정지",              "advice": "현재 상태 유지, RESUME 명령 대기"},
    "RESUME":  {"phase": "EN_ROUTE",   "desc": "목표 지역으로 이동 중",      "advice": "경로 유지 및 주변 감시 지속"},
    "RTB":     {"phase": "RTB",        "desc": "귀환 비행 중 (Return to Base)", "advice": "랜딩 준비 체크리스트 수행"},
}

# MAVLink 커넥션 (dashboard → UAV 직접)
_mav_conn = None
_mav_lock = threading.Lock()

def get_mav():
    global _mav_conn
    if _mav_conn is None:
        _mav_conn = mavutil.mavlink_connection(
            f"udpout:{UAV_HOST}:{UAV_CMD_PORT}",
            source_system=GCS_SYS_ID,
        )
    return _mav_conn


def _router_udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", GCS_TELEMETRY_PORT))
    print(f"[DASH] UDP 직수신 대기 → {GCS_TELEMETRY_PORT}  (GCS fan-out + Router UGV)")
    while True:
        try:
            data, _ = sock.recvfrom(8192)
            payload = json.loads(data.decode())
            pid  = payload.get("platform_id", "UNKNOWN")
            ptype = payload.get("platform_type", "")
            # 에이전트 판단 이벤트는 별도 큐에 저장
            if ptype == "AGENT":
                agent_events.appendleft({
                    "time":       payload.get("time", time.strftime("%H:%M:%S")),
                    "level":      payload.get("level", "info"),
                    "agent_type": payload.get("agent_type", ""),
                    "source":     payload.get("source", ""),
                    "message":    payload.get("message", ""),
                    "detail":     payload.get("detail", ""),
                    "status":     payload.get("status", ""),
                })
                continue

            if ptype == "NETWORK":
                target = payload.get("target_platform_id")
                ticn = payload.get("ticn", {})
                tmmr = payload.get("tmmr", {})
                loss_pct = ticn.get("loss_pct", 100.0)
                link_quality = ticn.get("link_quality", 0)
                hard_lost = bool(tmmr.get("blackout")) or float(loss_pct or 0) >= 75 or float(link_quality or 0) <= 15

                local_events.appendleft({
                    "type":    "telemetry",
                    "time":    payload.get("time", time.strftime("%H:%M:%S")),
                    "level":   "warn" if hard_lost else "info",
                    "source":  target or "TICN-LINK",
                    "message": payload.get("message", "전술 데이터링크 패킷 드롭"),
                    "status":  "COMMS_LOST" if hard_lost else "PACKET_DROP",
                })

                if target:
                    prev = router_platforms.get(target, {})
                    ptype_hint = prev.get("platform_type") or ("UAV" if target.startswith("UAV") else "UGV")
                    updated = {
                        **prev,
                        "platform_id": target,
                        "platform_type": ptype_hint,
                        "mode": "LOITER" if hard_lost and target.startswith("UAV") else prev.get("mode", "AUTO"),
                        "status": "COMMS_LOST" if hard_lost else prev.get("status", "ACTIVE"),
                        "comms_lost": hard_lost,
                        "gcs_received_at": time.time(),
                        "tmmr": {**prev.get("tmmr", {}), **tmmr},
                        "ticn": {
                            **prev.get("ticn", {}),
                            **ticn,
                            "loss_pct": loss_pct,
                            "link_quality": link_quality,
                        },
                    }
                    if not hard_lost:
                        updated.pop("comms_lost", None)
                        if updated.get("status") == "COMMS_LOST":
                            updated["status"] = "ACTIVE"
                    router_platforms[target] = updated

                # 전술 링크 손실은 링크 상태 패널에만 반영 — 임무 단계는 C2 명령으로만 변경
                continue

            clean_payload = {**payload, "gcs_received_at": time.time()}
            ticn = clean_payload.get("ticn", {}) if isinstance(clean_payload.get("ticn"), dict) else {}
            tmmr = clean_payload.get("tmmr", {}) if isinstance(clean_payload.get("tmmr"), dict) else {}
            link_ok = not tmmr.get("blackout") and float(ticn.get("loss_pct", 0) or 0) < 75 and float(ticn.get("link_quality", 100) or 100) > 15
            if link_ok:
                clean_payload.pop("comms_lost", None)
                if clean_payload.get("status") == "COMMS_LOST":
                    clean_payload["status"] = "ACTIVE"
            router_platforms[pid] = clean_payload

            # GCS를 거치지 않는 플랫폼(UGV 등)은 여기서 로컬 이벤트 생성
            if ptype != "UAV":
                local_events.appendleft({
                    "time":    time.strftime("%H:%M:%S"),
                    "level":   "info",
                    "source":  pid,
                    "message": (
                        f"telemetry seq={payload.get('seq')} "
                        f"spd={payload.get('speed')}m/s "
                        f"batt={payload.get('battery')}%"
                    ),
                })
        except Exception:
            pass


threading.Thread(target=_router_udp_listener, daemon=True).start()


def _gcs_heartbeat_sender():
    """GCS → UAV 1Hz heartbeat — 정상 연결 유지 + 대시보드 로그"""
    while True:
        try:
            with _mav_lock:
                mav = get_mav()
                mav.mav.heartbeat_send(
                    type=mavutil.mavlink.MAV_TYPE_GCS,
                    autopilot=mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                    base_mode=0,
                    custom_mode=0,
                    system_status=mavutil.mavlink.MAV_STATE_ACTIVE,
                )
            uav = router_platforms.get("UAV-001", {})
            local_events.appendleft({
                "type":        "telemetry",
                "platform_id": "UAV-001",
                "time":        time.strftime("%H:%M:%S"),
                "level":       "info",
                "source":      "GCS",
                "message":     (
                    f"HEARTBEAT | GCS→UAV-001 | "
                    f"mode={uav.get('mode','?')} "
                    f"alt={uav.get('alt','?')}m "
                    f"fuel={uav.get('fuel', uav.get('battery','?'))}%"
                ),
                "protocol":    "MAVLink",
                "status":      "정상",
            })
        except Exception:
            pass
        time.sleep(1)


threading.Thread(target=_gcs_heartbeat_sender, daemon=True).start()


TOPOLOGY = {
    "title": "UAV/UGV 전술통신 파이프라인",
    "subtitle": "CC(UAV) 텔레메트리가 GCS로 직수신된 후 Dashboard / Collector / Router로 fan-out되고, Router가 TMMR/TICN 시뮬레이션을 통해 Upper C2/BMS로 전달합니다.",
    "network": {
        "name": "uav_net / ops_net / dah-net",
        "subnet": "pipeline separated networks",
        "type": "Docker bridge networks",
    },
    "nodes": [
        {
            "id": "us",
            "name": "US",
            "label": "Unmanned Systems",
            "role": "무인체계 전체 범위",
            "status": "scope",
            "ip": "-",
            "group": "system",
        },
        {
            "id": "uav",
            "name": "UAV",
            "label": "dah-uav",
            "role": "송골매 UAV 시뮬레이터",
            "status": "implemented",
            "ip": "172.31.50.10",
            "group": "vehicle",
        },
        {
            "id": "ugv",
            "name": "UGV",
            "label": "dah-ugv",
            "role": "무인지상차량 시뮬레이터",
            "status": "implemented",
            "ip": "uav_net",
            "group": "vehicle",
        },
        {
            "id": "router",
            "name": "Tactical Router",
            "label": "dah-tactical-router",
            "role": "GCS 전술망 릴레이 → TMMR/TICN 시뮬레이션 → Upper C2/BMS",
            "status": "implemented",
            "ip": "uav_net/ops_net",
            "group": "network",
        },
        {
            "id": "mission",
            "name": "Upper C2/BMS",
            "label": "dah-mission-control",
            "role": "작전 상황 판단 / 명령 하달",
            "status": "implemented",
            "ip": "ops_net:8080",
            "group": "ops",
        },
        {
            "id": "collector",
            "name": "Telemetry Collector",
            "label": "dah-telemetry-collector",
            "role": "전술 텔레메트리 로그 수집",
            "status": "implemented",
            "ip": "ops_net:14541",
            "group": "ops",
        },
        {
            "id": "recon",
            "name": "Recon",
            "label": "dah-recon",
            "role": "텔레메트리 도청/분석",
            "status": "implemented",
            "ip": "172.31.50.40",
            "group": "attack",
        },
        {
            "id": "defense",
            "name": "Defense",
            "label": "dah-defense",
            "role": "비정상 명령 탐지/대응",
            "status": "implemented",
            "ip": "172.31.50.60",
            "group": "defense",
        },
        {
            "id": "dashboard",
            "name": "Dashboard",
            "label": "dah-dashboard",
            "role": "통신 구조 시각화",
            "status": "implemented",
            "ip": "172.31.50.70",
            "group": "ops",
        },
    ],
    "links": [
        {
            "source": "us",
            "target": "uav",
            "protocol": "범위",
            "port": "-",
            "flow": "US 하위 체계",
            "status": "implemented",
        },
        {
            "source": "us",
            "target": "ugv",
            "protocol": "범위",
            "port": "-",
            "flow": "US 하위 체계",
            "status": "planned",
        },
        {
            "source": "uav",
            "target": "dashboard",
            "protocol": "MAVLink/JSON / UDP",
            "port": "14555",
            "flow": "CC → GCS 텔레메트리 직수신",
            "status": "implemented",
        },
        {
            "source": "ugv",
            "target": "router",
            "protocol": "JSON telemetry / UDP",
            "port": "14660",
            "flow": "UGV 위치, 속도, 지상 센서 상태",
            "status": "implemented",
        },
        {
            "source": "dashboard",
            "target": "router",
            "protocol": "JSON telemetry / UDP",
            "port": "14560",
            "flow": "GCS → Router 전술망 릴레이",
            "status": "implemented",
        },
        {
            "source": "dashboard",
            "target": "collector",
            "protocol": "JSON telemetry / UDP",
            "port": "14541",
            "flow": "GCS → Collector fan-out",
            "status": "implemented",
        },
        {
            "source": "router",
            "target": "mission",
            "protocol": "JSON+TMMR/TICN / UDP",
            "port": "14545",
            "flow": "Router → Upper C2/BMS 전술 상황",
            "status": "implemented",
        },
        {
            "source": "mission",
            "target": "router",
            "protocol": "JSON / UDP",
            "port": "14546",
            "flow": "Upper C2/BMS 작전 명령 하달",
            "status": "implemented",
        },
    ],
    "notes": [
        "텔레메트리 파이프라인: CC → GCS → (Dashboard / Collector / Router) → Upper C2/BMS",
        "명령 파이프라인: Upper C2/BMS → Router → GCS → CC → UAV FC (MAVLink)",
        "Recon/Defense는 별도의 직접 공격/방어 실습 레이어로 유지됩니다.",
        "Gateway API: http://localhost:9000  |  GCS API: /gcs/  |  Upper C2/BMS API: /c2/",
    ],
}


def fetch_json(path):
    url = f"{MISSION_CONTROL_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return json_loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def json_loads(raw):
    return json.loads(raw.decode("utf-8"))


@app.post("/api/command")
def send_command():
    body   = request.get_json(force=True) or {}
    cmd    = body.get("cmd", "").upper()
    target = body.get("target", "UAV-001")

    CMD_TO_MAV = {
        "HOLD":    (mavutil.mavlink.MAV_CMD_NAV_LOITER_UNLIM,     [0,0,0,0,0,0,0]),
        "MONITOR": (mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,      [0, 100, -1, 0, 0, 0, 0]),
        "PAUSE":   (mavutil.mavlink.MAV_CMD_DO_PAUSE_CONTINUE,    [0, 0, 0, 0, 0, 0, 0]),
        "RESUME":  (mavutil.mavlink.MAV_CMD_DO_PAUSE_CONTINUE,    [1, 0, 0, 0, 0, 0, 0]),
        "RTB":     (mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH, [0,0,0,0,0,0,0]),
    }

    if cmd not in CMD_TO_MAV:
        return jsonify({"ok": False, "error": f"unknown cmd: {cmd}"}), 400

    mav_cmd, params = CMD_TO_MAV[cmd]
    try:
        with _mav_lock:
            mav = get_mav()
            mav.mav.command_long_send(
                target_system=1, target_component=1,
                command=mav_cmd, confirmation=0,
                param1=params[0], param2=params[1], param3=params[2],
                param4=params[3], param5=params[4], param6=params[5], param7=params[6],
            )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    # 임무 상태 업데이트
    if cmd in PHASE_MAP:
        update_mission_state(PHASE_MAP[cmd])
        mission_state["cmd"]    = cmd
        mission_state["cmd_at"] = time.strftime("%H:%M:%S")
        mission_state["cmd_by"] = "C2/GCS"

    # 로컬 이벤트에도 기록
    local_events.appendleft({
        "type":    "command",
        "time":    time.strftime("%H:%M:%S"),
        "source":  "C2/GCS",
        "target":  target,
        "message": f"C2 명령 전송: {cmd}",
        "status":  "SENT",
    })
    print(f"[C2] {cmd} → {target} ({UAV_HOST}:{UAV_CMD_PORT})")
    return jsonify({"ok": True, "cmd": cmd, "target": target})


@app.get("/")
def index():
    return render_template("index.html", topology=TOPOLOGY)


@app.get("/api/topology")
def topology():
    return jsonify(TOPOLOGY)




def fetch_gcs(path):
    url = f"{GCS_URL}{path}"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as response:
            return json_loads(response.read())
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


@app.get("/api/live")
def live():
    # GCS 우선 fetch (최신 데이터 보유)
    dashboard = fetch_gcs("/api/dashboard")
    if dashboard is None:
        # GCS 불가 시 Upper C2/BMS fallback
        dashboard = fetch_json("/api/dashboard")
    if dashboard is None:
        return jsonify({
            "status": "degraded",
            "message": "GCS / Upper C2 unavailable (direct UDP only)",
            "platforms": apply_failsafe_simulation(list(router_platforms.values())),
            "events": [],
            "mission_state": mission_state,
            "failsafe_simulation": current_failsafe_snapshot(),
        })
    # 플랫폼: GCS + UDP 직수신 병합 (UDP 직수신 우선)
    mc_platforms = {p["platform_id"]: p for p in dashboard.get("platforms", [])}
    merged = {**mc_platforms, **router_platforms}

    # 이벤트: GCS 이벤트 + UGV 로컬 이벤트 병합 후 시간순 정렬
    gcs_events = dashboard.get("events", [])
    merged_events = list(gcs_events) + list(local_events)
    merged_events.sort(key=lambda e: e.get("time", ""), reverse=True)
    platform_list = apply_failsafe_simulation(list(merged.values()))

    return jsonify({
        "status": "ok",
        **dashboard,
        "platforms":     platform_list,
        "events":        merged_events[:50],
        "agent_events":  list(agent_events)[:100],
        "gcs_direct":    len(router_platforms),
        "mission_state": mission_state,
        "failsafe_simulation": current_failsafe_snapshot(),
    })


@app.get("/api/failsafe")
def failsafe_policy():
    """정찰 에이전트가 수집하는 fail-safe 정책값 노출 엔드포인트"""
    return jsonify({
        "heartbeat": {
            "interval_sec":      1,
            "timeout_sec":       5,
            "max_miss_count":    5,
            "last_hb_time":      router_platforms.get("UAV-001", {}).get("time", None),
        },
        "packet_loss": {
            "normal_pct":            0,
            "warning_pct":           10,
            "critical_pct":          15,   # mock_uav.py LINK_LOST_THRESHOLD
            "critical_duration_sec": 2,
        },
        "latency": {
            "normal_ms":   50,
            "warning_ms":  500,
            "critical_ms": 1500,
        },
        "failsafe_action":  "LOITER",
        "rtb_on_prolonged": True,
        "loiter_restore_ticks": 3,
    })


@app.post("/api/agent-event")
def ingest_agent_event():
    payload = request.get_json(force=True, silent=True) or {}
    record_agent_event(payload)
    return jsonify({"ok": True, "agent_events": len(agent_events)})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
