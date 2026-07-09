import json
import math
import os
import threading
import socket
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pymavlink import mavutil

DASHBOARD_URL = "http://dah-dashboard:8080"
DEFENSE_STATUS_PORT = int(os.getenv("DEFENSE_STATUS_PORT", "8080"))
DASHBOARD_EVENT_HOST = os.getenv("DASHBOARD_EVENT_HOST", "172.31.50.70")
DASHBOARD_EVENT_PORT = int(os.getenv("DASHBOARD_EVENT_PORT", "14571"))
LINK_LOST_THRESHOLD = 75   # loss_pct 이상이면 Link Lost (VHF 사거리 초과 시 30~40% 정상)

# ─────────────────────────────────────────
# 송골매 UAV 기본 설정값
# ─────────────────────────────────────────
UAV_HOST    = '172.31.50.255'
UAV_PORT    = 14550
CMD_PORT    = 14551
SYS_ID      = 1
PLATFORM_ID = 'UAV-001'
MISSION     = 'RECON'
ALTITUDE    = 2000        # 순항 고도 (m)
SPEED_KMH   = 1200        # 순항 속도 (km/h)
SPEED_MS    = SPEED_KMH / 3.6  # m/s
FUEL        = 78
EO_STATUS   = 'ACTIVE'
IR_STATUS   = 'ACTIVE'

# 평양 출발 → 대한민국 GP 라인 순찰 → 평양 귀환 웨이포인트 (위도, 경도)
WAYPOINTS = [
    (39.019, 125.738),   # WP1 — 평양 기지 (출발·귀환점)
    (38.700, 126.100),   # WP2 — 황주 남쪽 (남동 진출)
    (38.350, 126.400),   # WP3 — 군사분계선 북방
    (37.920, 126.700),   # WP4 — 파주 GP 근처 (MDL 이남 서측)
    (37.870, 127.050),   # WP5 — 연천 GP 근처 (MDL 이남 동측)
    (38.200, 127.100),   # WP6 — 귀환 진입 (DMZ 북쪽 동측)
    (38.750, 126.300),   # WP7 — 황해북도 북상
]

# 위경도 거리 계산 상수 (임무 중심 위도 ≈ 38.5° 기준)
LAT_PER_M = 1 / 111_000
LON_PER_M = 1 / (111_000 * math.cos(math.radians(38.5)))

status = {'mode': 'MISSION', 'alt': ALTITUDE}  # MISSION | LANDING | RTL | LOITER | PAUSED
_resume_until = 0.0    # RESUME 명령 이후 link_monitor 억제 종료 시각

# GCS heartbeat 감시용
_gcs_hb_last  = time.time()
GCS_HB_TIMEOUT = 5.0   # 이 시간 동안 GCS heartbeat 없으면 fail-safe
_defense_state = {
    "enabled": False,
    "block_unsafe_commands": False,
    "block_spoofed_heartbeat": False,
    "allowed_sys_ids": [255],
    "restricted_commands": ["MAV_CMD_NAV_LAND", "MAV_CMD_DO_SET_MODE"],
    "expires_at": 0.0,
    "source": None,
    "reason": None,
    "blocked_events": 0,
}
_defense_lock = threading.Lock()


def defense_active() -> bool:
    with _defense_lock:
        if not _defense_state["enabled"]:
            return False
        if _defense_state["expires_at"] and time.time() > _defense_state["expires_at"]:
            _defense_state["enabled"] = False
            return False
        return True


def defense_snapshot() -> dict:
    with _defense_lock:
        snapshot = dict(_defense_state)
    snapshot["active"] = defense_active()
    if snapshot.get("expires_at"):
        snapshot["remaining_sec"] = max(0.0, round(snapshot["expires_at"] - time.time(), 1))
    return snapshot


def apply_defense_rules(payload: dict) -> dict:
    ttl = float(payload.get("ttl_sec", payload.get("duration_sec", 3600)) or 0)
    with _defense_lock:
        _defense_state["enabled"] = bool(payload.get("enabled", True))
        _defense_state["block_unsafe_commands"] = bool(payload.get("block_unsafe_commands", _defense_state["block_unsafe_commands"]))
        _defense_state["block_spoofed_heartbeat"] = bool(payload.get("block_spoofed_heartbeat", _defense_state["block_spoofed_heartbeat"]))
        _defense_state["allowed_sys_ids"] = list(payload.get("allowed_sys_ids", _defense_state["allowed_sys_ids"]))
        _defense_state["restricted_commands"] = list(payload.get("restricted_commands", _defense_state["restricted_commands"]))
        _defense_state["expires_at"] = time.time() + ttl if ttl > 0 and _defense_state["enabled"] else 0.0
        _defense_state["source"] = payload.get("source", "defense_agent")
        _defense_state["reason"] = payload.get("reason", "defense policy")
    return defense_snapshot()


def _mav_cmd_name(cmd: int) -> str:
    try:
        return mavutil.mavlink.enums["MAV_CMD"][int(cmd)].name
    except Exception:
        return f"MAV_CMD_{cmd}"


def emit_defense_block(reason: str, evidence: dict) -> None:
    with _defense_lock:
        _defense_state["blocked_events"] += 1
    event = {
        "platform_type": "AGENT",
        "platform_id": "DEF-001",
        "agent_type": "DEF",
        "source": "UAV-DEFENSE-GUARD",
        "message": "UAV 명령/상태 위조 시도 차단",
        "detail": json.dumps({"reason": reason, **evidence}, ensure_ascii=False),
        "level": "warn",
        "status": "BLOCKED",
        "time": time.strftime("%H:%M:%S"),
    }
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(json.dumps(event, ensure_ascii=False).encode("utf-8"), (DASHBOARD_EVENT_HOST, DASHBOARD_EVENT_PORT))
        sock.close()
    except Exception:
        pass
    print(f"[송골매] 🛡️ DEFENSE BLOCK: {reason} {evidence}")


def defense_blocks_heartbeat(src: int, system_status: int) -> bool:
    if not defense_active() or not _defense_state.get("block_spoofed_heartbeat"):
        return False
    allowed = set(int(item) for item in _defense_state.get("allowed_sys_ids", [255]))
    unsafe_status = system_status in {
        mavutil.mavlink.MAV_STATE_CRITICAL,
        mavutil.mavlink.MAV_STATE_EMERGENCY,
    }
    return src not in allowed or unsafe_status


def defense_blocks_command(src: int, cmd: int) -> bool:
    if not defense_active() or not _defense_state.get("block_unsafe_commands"):
        return False
    allowed = set(int(item) for item in _defense_state.get("allowed_sys_ids", [255]))
    restricted = set(_defense_state.get("restricted_commands", []))
    cmd_name = _mav_cmd_name(cmd)
    return src not in allowed or cmd_name in restricted


def defense_http_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def _send_json(self, code: int, payload: dict):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/api/defense/status":
                self._send_json(200, defense_snapshot())
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                payload = {}
            if self.path == "/api/defense/rules":
                self._send_json(200, {"ok": True, "defense": apply_defense_rules(payload)})
            else:
                self._send_json(404, {"error": "not found"})

    HTTPServer(("0.0.0.0", DEFENSE_STATUS_PORT), Handler).serve_forever()


def link_monitor():
    """링크 품질 감시 — loss_pct 급등 시 LOITER 전환, 복구 시 임무 복귀"""
    loiter_ticks = 0
    while True:
        time.sleep(2)
        try:
            with urllib.request.urlopen(f"{DASHBOARD_URL}/api/live", timeout=1.5) as r:
                data = json.loads(r.read())
            pmap = {p["platform_id"]: p for p in data.get("platforms", [])}
            uav  = pmap.get("UAV-001", {})
            loss = uav.get("ticn", {}).get("loss_pct", 0) or 0
            # RESUME 쿨다운 또는 보호 모드 중이면 LOITER 전환 억제
            if time.time() < _resume_until:
                continue
            if loss >= LINK_LOST_THRESHOLD and status['mode'] not in ('LOITER', 'PAUSED', 'LANDING', 'RTL'):
                print(f"[송골매] ⚠️  Link Lost (loss={loss}%) → LOITER 전환")
                status['mode'] = 'LOITER'
                loiter_ticks = 0
            elif loss < LINK_LOST_THRESHOLD and status['mode'] == 'LOITER':
                loiter_ticks += 1
                if loiter_ticks >= 3:  # 6초 이상 안정 시 복귀
                    print(f"[송골매] ✅ Link Restored (loss={loss}%) → 임무 복귀")
                    status['mode'] = 'MISSION'
            else:
                loiter_ticks = 0
        except Exception:
            pass


def gcs_heartbeat_watchdog():
    """GCS heartbeat timeout 감시 — 5초 이상 없으면 LOITER fail-safe"""
    while True:
        time.sleep(1)
        elapsed = time.time() - _gcs_hb_last
        if elapsed > GCS_HB_TIMEOUT and status['mode'] == 'MISSION':
            print(f"[송골매] 🚨 GCS Heartbeat 없음 {elapsed:.1f}s → Fail-safe LOITER")
            status['mode'] = 'LOITER'


def listen_for_commands():
    global _gcs_hb_last, _resume_until
    cmd_conn = mavutil.mavlink_connection(f'udpin:0.0.0.0:{CMD_PORT}')
    print(f"[송골매] 명령 수신 대기 중 → 포트 {CMD_PORT}")
    while True:
        msg = cmd_conn.recv_match(type=['COMMAND_LONG', 'HEARTBEAT'], blocking=True)
        if msg is None:
            continue
        msg_type = msg.get_type()
        src = msg.get_srcSystem()

        # GCS heartbeat 수신 → 타임스탬프 갱신
        if msg_type == 'HEARTBEAT' and src == 255:
            gcs_status = msg.system_status
            if defense_blocks_heartbeat(src, gcs_status):
                emit_defense_block(
                    "unsafe_gcs_heartbeat_status",
                    {"src_sys": src, "system_status": int(gcs_status), "message_type": "HEARTBEAT"},
                )
                continue
            _gcs_hb_last = time.time()
            if gcs_status == mavutil.mavlink.MAV_STATE_CRITICAL:
                print(f"[송골매] ⚠️  GCS CRITICAL 상태 수신 → Fail-safe LOITER")
                status['mode'] = 'LOITER'
            elif gcs_status == mavutil.mavlink.MAV_STATE_EMERGENCY:
                print(f"[송골매] 🚨 GCS EMERGENCY 수신 → Fail-safe RTL")
                status['mode'] = 'RTL'
            continue

        if msg_type != 'COMMAND_LONG':
            continue

        cmd = msg.command
        if defense_blocks_command(src, cmd):
            emit_defense_block(
                "restricted_or_untrusted_command",
                {"src_sys": src, "cmd": _mav_cmd_name(cmd), "cmd_id": int(cmd), "message_type": "COMMAND_LONG"},
            )
            continue

        if cmd == mavutil.mavlink.MAV_CMD_NAV_LAND:
            print(f"[송골매] ⚠️  LAND 명령 수신 SYS_ID={src} → 착륙 시작")
            status['mode'] = 'LANDING'
        elif cmd == mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH:
            if status['mode'] == 'LANDING':
                print(f"[송골매] ✅ RTL 수신 SYS_ID={src} → 착륙 취소, 순항 고도 복귀")
                status['mode'] = 'MISSION'
                status['alt'] = ALTITUDE
            else:
                print(f"[송골매] RTL 명령 수신 SYS_ID={src} → 귀환")
                status['mode'] = 'RTL'
        elif cmd == mavutil.mavlink.MAV_CMD_NAV_LOITER_UNLIM:
            print(f"[송골매] HOLD 명령 수신 SYS_ID={src} → 선회 대기")
            status['mode'] = 'LOITER'
            _resume_until = time.time() + 60.0   # 60초간 link_monitor 자동복귀 억제
        elif cmd == mavutil.mavlink.MAV_CMD_DO_PAUSE_CONTINUE:
            if msg.param1 == 0:
                print(f"[송골매] PAUSE 명령 수신 SYS_ID={src} → 임무 정지")
                status['mode'] = 'PAUSED'
            else:
                print(f"[송골매] RESUME 명령 수신 SYS_ID={src} → 임무 재개")
                status['mode'] = 'MISSION'
                _resume_until = time.time() + 30.0   # 30초간 link_monitor LOITER 재전환 억제
        elif cmd == mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED:
            print(f"[송골매] MONITOR 명령 수신 SYS_ID={src} → 감시 모드")
            status['mode'] = 'MISSION'


def heading_deg(lat1, lon1, lat2, lon2):
    dlat = lat2 - lat1
    dlon = (lon2 - lon1) * math.cos(math.radians(lat1))
    angle = math.degrees(math.atan2(dlon, dlat))
    return angle % 360


def main():
    threading.Thread(target=listen_for_commands,      daemon=True).start()
    threading.Thread(target=link_monitor,             daemon=True).start()
    threading.Thread(target=gcs_heartbeat_watchdog,   daemon=True).start()
    threading.Thread(target=defense_http_server,       daemon=True).start()

    mav = mavutil.mavlink_connection(f'udpout:{UAV_HOST}:{UAV_PORT}', source_system=SYS_ID)
    mav.port.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    seq        = 1
    lat, lon   = WAYPOINTS[0]
    wp_idx     = 1   # 다음 목표 웨이포인트 인덱스
    hdg        = 0
    prev_dist  = None
    cooldown   = 0   # 연속 감지 방지용

    print(f"[송골매] 정찰 비행 시작 | 웨이포인트 {len(WAYPOINTS)}개 순환")

    while True:
        alt = status['alt']  # 방어 성공 시 스레드가 복구한 고도 반영

        if status['mode'] == 'PAUSED':
            print(f"[송골매] ⏸  임무 정지 — 현재 위치 유지 | 고도={alt}m")
        elif status['mode'] == 'LOITER':
            # 제자리 선회 — Link Lost / HOLD 명령 시
            loiter_angle = (seq * 3) % 360
            r_lat = 300 * LAT_PER_M
            r_lon = 300 * LON_PER_M
            lat = lat + r_lat * math.cos(math.radians(loiter_angle)) * 0.05
            lon = lon + r_lon * math.sin(math.radians(loiter_angle)) * 0.05
            hdg = (loiter_angle + 90) % 360
            print(f"[송골매] 🔄 LOITER | 고도={alt}m 선회각={loiter_angle}°")
        elif status['mode'] == 'LANDING':
            alt = max(0, alt - 100)
            status['alt'] = alt
            print(f"[송골매] ⚠️  착륙 중 (공격)... 현재 고도={alt}m")
            if alt == 0:
                print("[송골매] 착륙 완료. LOITER 대기.")
                status['mode'] = 'LOITER'
        elif status['mode'] == 'RTL':
            # 출발지(WP1)로 비행하면서 하강
            home_lat, home_lon = WAYPOINTS[0]
            hdg = heading_deg(lat, lon, home_lat, home_lon)
            dlat = math.cos(math.radians(hdg)) * SPEED_MS * LAT_PER_M
            dlon = math.sin(math.radians(hdg)) * SPEED_MS * LON_PER_M
            lat += dlat
            lon += dlon
            dist_m = math.sqrt(((lat - home_lat) / LAT_PER_M) ** 2 +
                               ((lon - home_lon) / LON_PER_M) ** 2)
            # 출발지 근접 시 하강
            if dist_m < 500:
                alt = max(0, alt - 100)
                status['alt'] = alt
            print(f"[송골매] RTL 귀환 중... 고도={alt}m 거리={dist_m:.0f}m → WP1")
            if alt == 0 and dist_m < 500:
                print("[송골매] RTL 완료. 귀환 착륙 — LOITER 대기")
                status['mode'] = 'LOITER'
                status['alt']  = 0
        else:
            # ── 다음 웨이포인트 방향으로 이동
            wp_lat, wp_lon = WAYPOINTS[wp_idx]
            hdg = heading_deg(lat, lon, wp_lat, wp_lon)

            dlat = math.cos(math.radians(hdg)) * SPEED_MS * LAT_PER_M
            dlon = math.sin(math.radians(hdg)) * SPEED_MS * LON_PER_M
            lat += dlat
            lon += dlon

            dist_m = math.sqrt(((lat - wp_lat) / LAT_PER_M) ** 2 +
                               ((lon - wp_lon) / LON_PER_M) ** 2)

            if cooldown > 0:
                cooldown -= 1
            else:
                # 임계값(300m) 이내 진입 OR 최근접 통과(거리가 다시 벌어지는 순간)
                reached = dist_m < 300
                overshot = (prev_dist is not None and dist_m > prev_dist + 5 and prev_dist < 300)
                if reached or overshot:
                    print(f"[송골매] WP{wp_idx + 1} 도달 (dist={dist_m:.0f}m) → 다음 WP{(wp_idx + 2 - 1) % len(WAYPOINTS) + 1}")
                    mav.mav.mission_item_reached_send(seq=wp_idx)
                    wp_idx = (wp_idx + 1) % len(WAYPOINTS)
                    prev_dist = None
                    cooldown = 8   # 8스텝(4초) 동안 재감지 차단
                else:
                    prev_dist = dist_m

            print(f"[송골매] POSITION | 위도={lat:.5f} 경도={lon:.5f} 고도={alt}m 방향={hdg:.1f}° | SEQ={seq+2}")
            print("-" * 50)

        # MAVLink 패킷 전송
        mav.mav.heartbeat_send(
            type=mavutil.mavlink.MAV_TYPE_FIXED_WING,
            autopilot=mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
            base_mode=mavutil.mavlink.MAV_MODE_AUTO_ARMED,
            custom_mode=0,
            system_status=mavutil.mavlink.MAV_STATE_ACTIVE
        )
        mav.mav.sys_status_send(
            onboard_control_sensors_present=0,
            onboard_control_sensors_enabled=0,
            onboard_control_sensors_health=0,
            load=300,
            voltage_battery=12000,
            current_battery=1500,
            battery_remaining=FUEL,
            drop_rate_comm=0,
            errors_comm=0,
            errors_count1=0, errors_count2=0,
            errors_count3=0, errors_count4=0
        )
        mav.mav.global_position_int_send(
            time_boot_ms=seq * 1000,
            lat=int(lat * 1e7),
            lon=int(lon * 1e7),
            alt=int(alt * 1000),
            relative_alt=int(alt * 1000),
            vx=min(int(SPEED_MS * 100), 32767),  # int16_t 최대값 캡핑
            vy=0, vz=0,
            hdg=int(hdg * 100)
        )

        seq += 3
        time.sleep(0.5)


if __name__ == '__main__':
    main()
