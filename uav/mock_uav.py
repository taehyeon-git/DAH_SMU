import json
import math
import threading
import socket
import time
import urllib.request
from pymavlink import mavutil

DASHBOARD_URL = "http://dah-dashboard:8080"
LINK_LOST_THRESHOLD = 15   # loss_pct 이상이면 Link Lost

# ─────────────────────────────────────────
# 송골매 UAV 기본 설정값
# ─────────────────────────────────────────
UAV_HOST    = '172.31.50.255'
UAV_PORT    = 14550
CMD_PORT    = 14551
SYS_ID      = 1
PLATFORM_ID = 'UAV-001'
MISSION     = 'RECON'
ALTITUDE    = 3500        # 순항 고도 (m)
SPEED_KMH   = 600         # 순항 속도 (km/h)
SPEED_MS    = SPEED_KMH / 3.6  # m/s
FUEL        = 78
EO_STATUS   = 'ACTIVE'
IR_STATUS   = 'ACTIVE'

# 경기 북부 정찰 웨이포인트 (위도, 경도) — 각 꺾임이 명확한 다각형 경로
WAYPOINTS = [
    (37.895, 126.800),   # WP1 — 서쪽 출발점
    (37.945, 126.820),   # WP2 — 북서 (위로 크게 이동)
    (37.955, 126.870),   # WP3 — 북동 (오른쪽으로 이동)
    (37.930, 126.910),   # WP4 — 동쪽 끝 (아래로 꺾임)
    (37.885, 126.905),   # WP5 — 남동 (아래로 크게 이동)
    (37.860, 126.855),   # WP6 — 남쪽 (왼쪽 아래로 꺾임)
    (37.870, 126.810),   # WP7 — 남서 (왼쪽으로 꺾임)
]

# 위경도 거리 계산 상수
LAT_PER_M = 1 / 111_000
LON_PER_M = 1 / (111_000 * math.cos(math.radians(37.9)))

status = {'mode': 'MISSION', 'alt': ALTITUDE}  # MISSION | LANDING | RTL | LOITER | PAUSED

# GCS heartbeat 감시용
_gcs_hb_last  = time.time()
GCS_HB_TIMEOUT = 5.0   # 이 시간 동안 GCS heartbeat 없으면 fail-safe


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
            if loss >= LINK_LOST_THRESHOLD and status['mode'] == 'MISSION':
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
    global _gcs_hb_last
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
            _gcs_hb_last = time.time()
            gcs_status = msg.system_status
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
        elif cmd == mavutil.mavlink.MAV_CMD_DO_PAUSE_CONTINUE:
            if msg.param1 == 0:
                print(f"[송골매] PAUSE 명령 수신 SYS_ID={src} → 임무 정지")
                status['mode'] = 'PAUSED'
            else:
                print(f"[송골매] RESUME 명령 수신 SYS_ID={src} → 임무 재개")
                status['mode'] = 'MISSION'
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
                print("[송골매] 착륙 완료. 임무 중단.")
                break
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
                print("[송골매] RTL 완료. 귀환 착륙.")
                break
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
            vx=int(SPEED_MS * 100),
            vy=0, vz=0,
            hdg=int(hdg * 100)
        )

        seq += 3
        time.sleep(0.5)


if __name__ == '__main__':
    main()
