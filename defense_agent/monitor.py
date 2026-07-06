import time
from pymavlink import mavutil

# ─────────────────────────────────────────
# 모니터 설정
# ─────────────────────────────────────────
LISTEN_HOST = '0.0.0.0'    # 모든 IP에서 오는 패킷 수신
LISTEN_PORT = 14550         # UAV 텔레메트리 포트 감시
ALLOWED_SYS_ID = 1          # 정상 UAV SYS_ID (송골매)
ALLOWED_GCS_ID = 255        # 정상 GCS SYS_ID
ALLOWED_COMMANDS = {
    mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
    mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
    mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
}

# 탐지된 이상 패킷 저장소 (detector.py가 읽어감)
alerts = []


def monitor():
    """
    MAVLink 패킷을 실시간으로 감시
    모든 패킷의 SYS_ID, 명령 종류, SEQ 기록
    """
    mav = mavutil.mavlink_connection(f'udpin:{LISTEN_HOST}:{LISTEN_PORT}')
    print(f"[MONITOR] 감시 시작 → 포트 {LISTEN_PORT}")
    print(f"[MONITOR] 허용 SYS_ID: UAV={ALLOWED_SYS_ID} | GCS={ALLOWED_GCS_ID}")

    last_seq = {}   # SYS_ID별 마지막 SEQ 번호 저장 (Replay Attack 탐지용)

    while True:
        msg = mav.recv_match(blocking=True)
        if msg is None:
            continue

        msg_type = msg.get_type()
        src_id   = msg.get_srcSystem()   # 패킷 보낸 SYS_ID
        seq      = msg._header.seq       # 패킷 순서 번호

        # ── COMMAND_LONG 패킷 집중 감시
        # 실제 명령(LAND, RTL 등)이 담긴 패킷
        if msg_type == 'COMMAND_LONG':
            cmd = msg.command
            print(f"[MONITOR] COMMAND_LONG 감지 | 출처 SYS_ID={src_id} | 명령={cmd} | SEQ={seq}")

            # 허용되지 않은 SYS_ID에서 명령 온 경우 → 경보
            if src_id != ALLOWED_GCS_ID:
                alert = {
                    'type'   : 'UNKNOWN_SRC',       # 경보 종류
                    'src_id' : src_id,               # 의심 SYS_ID
                    'cmd'    : cmd,                  # 명령 종류
                    'seq'    : seq                   # 패킷 번호
                }
                alerts.append(alert)
                print(f"[MONITOR] ⚠️  비정상 출처 탐지 → SYS_ID={src_id} (허용={ALLOWED_GCS_ID})")

            if cmd not in ALLOWED_COMMANDS:
                alerts.append({
                    'type'   : 'UNKNOWN_COMMAND',
                    'src_id' : src_id,
                    'cmd'    : cmd,
                    'seq'    : seq
                })
                print(f"[MONITOR] ⚠️  허용되지 않은 명령 탐지 → CMD={cmd}")

        # ── Replay Attack 탐지
        # 이전에 받은 SEQ보다 낮은 번호가 오면 재전송 공격 의심
        if src_id in last_seq:
            if seq <= last_seq[src_id]:
                alert = {
                    'type'   : 'REPLAY',
                    'src_id' : src_id,
                    'seq'    : seq
                }
                alerts.append(alert)
                print(f"[MONITOR] ⚠️  Replay Attack 의심 → SYS_ID={src_id} SEQ={seq} (이전={last_seq[src_id]})")

        last_seq[src_id] = seq   # 현재 SEQ 저장


if __name__ == '__main__':
    monitor()
