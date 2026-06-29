"""
ArduPilot SITL Runner — mock_uav.py 대체
실제 ArduPlane 펌웨어(SITL)를 구동하고 정찰 임무 업로드 후 AUTO 비행.

브리지 구조:
  SITL (TCP:5760) ←→ sitl_runner ←→ 브로드캐스트 UDP:14550  (companion 수신)
                                  ←→ UDP:14551 수신            (공격/방어 명령)
"""
import os
import socket
import subprocess
import threading
import time

os.environ['MAVLINK20'] = '1'
from pymavlink import mavutil

CC_HOST  = os.getenv('CC_HOST', '172.20.0.30')
CC_PORT  = int(os.getenv('CC_PORT', '14550'))
CMD_PORT = int(os.getenv('CMD_PORT', '14551'))
SPEEDUP  = int(os.getenv('SITL_SPEEDUP', '5'))

HOME_LAT = 37.895
HOME_LON = 126.800

WAYPOINTS = [
    (37.895, 126.800, 3500),
    (37.945, 126.820, 3500),
    (37.955, 126.870, 3500),
    (37.930, 126.910, 3500),
    (37.885, 126.905, 3500),
    (37.860, 126.855, 3500),
    (37.870, 126.810, 3500),
]


def run_sitl() -> subprocess.Popen:
    proc = subprocess.Popen([
        '/usr/local/bin/arduplane',
        f'--home={HOME_LAT},{HOME_LON},0,0',
        '--model=plane',
        f'--speedup={SPEEDUP}',
        '--sysid=1',
        '--sim-address=127.0.0.1',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[SITL] ArduPlane 시작 PID={proc.pid}  speedup={SPEEDUP}x")
    time.sleep(8)
    return proc


def upload_mission(mav):
    mav.mav.mission_clear_all_send(1, 0, 0)
    time.sleep(0.5)
    mav.mav.mission_count_send(1, 0, len(WAYPOINTS), 0)
    time.sleep(0.3)

    for i, (lat, lon, alt) in enumerate(WAYPOINTS):
        mav.mav.mission_item_int_send(
            target_system=1, target_component=0,
            seq=i,
            frame=mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
            command=mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
            current=1 if i == 0 else 0,
            autocontinue=1,
            param1=0, param2=15, param3=0, param4=float('nan'),
            x=int(lat * 1e7), y=int(lon * 1e7), z=float(alt),
            mission_type=0,
        )
        time.sleep(0.1)
    print(f"[SITL] 미션 업로드 완료: {len(WAYPOINTS)}개 웨이포인트")


def bridge_sitl_to_network(mav, out_sock):
    """SITL MAVLink → 네트워크 UDP 브로드캐스트 (companion 수신)"""
    while True:
        try:
            msg = mav.recv_match(blocking=True, timeout=1)
            if msg:
                buf = msg.get_msgbuf()
                out_sock.sendto(bytes(buf), (CC_HOST, CC_PORT))
        except Exception:
            pass


def bridge_network_to_sitl(cmd_sock, sitl_port):
    """네트워크 UDP:14551 (공격/방어 명령) → SITL TCP"""
    while True:
        try:
            data, _ = cmd_sock.recvfrom(4096)
            sitl_port.send(data)
        except Exception:
            pass


def main():
    proc = run_sitl()

    print("[SITL] MAVLink 연결 중 (TCP:5760)...")
    mav = mavutil.mavlink_connection('tcp:127.0.0.1:5760', source_system=255)
    mav.wait_heartbeat(timeout=30)
    print(f"[SITL] 연결 완료  SYS_ID={mav.target_system}")

    upload_mission(mav)

    # ARM + AUTO 모드 전환
    mav.mav.command_long_send(
        1, 0,
        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        0, 1, 0, 0, 0, 0, 0, 0,
    )
    time.sleep(2)
    mav.set_mode('AUTO')
    print("[SITL] ARM + AUTO → 정찰 임무 시작")

    # 브리지 소켓
    out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cmd_sock.bind(('0.0.0.0', CMD_PORT))

    threading.Thread(target=bridge_sitl_to_network,
                     args=(mav, out_sock), daemon=True).start()
    threading.Thread(target=bridge_network_to_sitl,
                     args=(cmd_sock, mav.port), daemon=True).start()

    print(f"[SITL] 브리지 시작  SITL→{CC_HOST}:{CC_PORT}  CMD←:{CMD_PORT}")
    proc.wait()


if __name__ == '__main__':
    main()
