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
UAV_HOST              = os.getenv("UAV_HOST",     "172.20.0.10")
UAV_CMD_PORT          = int(os.getenv("UAV_CMD_PORT", "14551"))
GCS_SYS_ID            = 255  # 정상 GCS SYS_ID

# UDP로 직접 수신한 플랫폼 상태 + 로컬 이벤트
router_platforms: dict = {}
local_events: deque  = deque(maxlen=100)
agent_events: deque  = deque(maxlen=300)

# 임무 상태 (C2 명령 기반)
mission_state: dict = {
    "phase":   "EN_ROUTE",
    "desc":    "목표 지역으로 이동 중",
    "advice":  "경로 유지 및 주변 감시 지속",
    "cmd":     None,
    "cmd_at":  None,
    "cmd_by":  "SYSTEM",
}

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

                local_events.appendleft({
                    "type":    "telemetry",
                    "time":    payload.get("time", time.strftime("%H:%M:%S")),
                    "level":   "warn",
                    "source":  target or "TICN-LINK",
                    "message": payload.get("message", "전술 데이터링크 통신 두절"),
                    "status":  "COMMS_LOST",
                })

                if target:
                    prev = router_platforms.get(target, {})
                    ptype_hint = prev.get("platform_type") or ("UAV" if target.startswith("UAV") else "UGV")
                    router_platforms[target] = {
                        **prev,
                        "platform_id": target,
                        "platform_type": ptype_hint,
                        "mode": "LOITER" if target.startswith("UAV") else prev.get("mode", "AUTO"),
                        "status": "COMMS_LOST",
                        "comms_lost": True,
                        "gcs_received_at": time.time(),
                        "tmmr": {**prev.get("tmmr", {}), **tmmr},
                        "ticn": {
                            **prev.get("ticn", {}),
                            **ticn,
                            "loss_pct": loss_pct,
                            "link_quality": link_quality,
                        },
                    }

                mission_state.update({
                    "phase":  "LOITER",
                    "desc":   "통신 두절로 제자리 배회",
                    "advice": "현재 좌표 유지, 전술 링크 복구 대기",
                    "cmd":    "COMMS_LOST",
                    "cmd_at": payload.get("time", time.strftime("%H:%M:%S")),
                    "cmd_by": "TICN/TMMR",
                })
                continue

            router_platforms[pid] = {**payload, "gcs_received_at": time.time()}

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
            "ip": "172.20.0.10",
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
            "ip": "172.20.0.40",
            "group": "attack",
        },
        {
            "id": "executor",
            "name": "Executor",
            "label": "dah-executor",
            "role": "COMMAND_LONG LAND 주입",
            "status": "implemented",
            "ip": "172.20.0.50",
            "group": "attack",
        },
        {
            "id": "defense",
            "name": "Defense",
            "label": "dah-defense",
            "role": "비정상 명령 탐지/대응",
            "status": "implemented",
            "ip": "172.20.0.60",
            "group": "defense",
        },
        {
            "id": "dashboard",
            "name": "Dashboard",
            "label": "dah-dashboard",
            "role": "통신 구조 시각화",
            "status": "implemented",
            "ip": "172.20.0.70",
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
        "Recon/Executor/Defense는 별도의 직접 공격/방어 실습 레이어로 유지됩니다.",
        "Upper C2/BMS API: http://localhost:8082  |  GCS API: http://localhost:8083",
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
    import json

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
        mission_state.update(PHASE_MAP[cmd])
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
            "platforms": list(router_platforms.values()),
            "events": [],
        })
    # 플랫폼: GCS + UDP 직수신 병합 (UDP 직수신 우선)
    mc_platforms = {p["platform_id"]: p for p in dashboard.get("platforms", [])}
    merged = {**mc_platforms, **router_platforms}

    # 이벤트: GCS 이벤트 + UGV 로컬 이벤트 병합 후 시간순 정렬
    gcs_events = dashboard.get("events", [])
    merged_events = list(gcs_events) + list(local_events)
    merged_events.sort(key=lambda e: e.get("time", ""), reverse=True)

    return jsonify({
        "status": "ok",
        **dashboard,
        "platforms":     list(merged.values()),
        "events":        merged_events[:50],
        "agent_events":  list(agent_events)[:100],
        "gcs_direct":    len(router_platforms),
        "mission_state": mission_state,
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


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
