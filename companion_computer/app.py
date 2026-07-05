"""
Companion Computer — UAV 탑재 컴퓨터
역할: UAV FC MAVLink 수신 → JSON 변환 → Tactical Router UDP 전송
      Router Command 수신 → MAVLink → UAV FC 전달
"""
import json
import os
import socket
import threading
import time

os.environ['MAVLINK20'] = '1'
from pymavlink import mavutil

MAVLINK_HOST = "0.0.0.0"
MAVLINK_PORT = int(os.getenv("MAVLINK_PORT",  "14550"))
GCS_HOST     = os.getenv("GCS_HOST",  "dah-gcs")
GCS_PORT     = int(os.getenv("GCS_PORT",  "14555"))       # CC → GCS 텔레메트리
CMD_PORT     = int(os.getenv("CMD_PORT",  "14552"))        # GCS → CC 명령 수신
UAV_HOST     = os.getenv("UAV_HOST",  "172.31.50.10")
UAV_CMD_PORT = int(os.getenv("UAV_CMD_PORT", "14551"))
RECON_MIRROR_ENABLED = os.getenv("RECON_MIRROR_ENABLED", "true").lower() == "true"
RECON_MIRROR_HOST = os.getenv("RECON_MIRROR_HOST", "dah-recon")
RECON_MIRROR_PORT = int(os.getenv("RECON_MIRROR_PORT", "14550"))
PLATFORM_ID  = "UAV-001"
SYS_ID       = 1

state = {}
gcs_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
mirror_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
last_mirror_error_at = 0.0


def send_to_gcs(payload):
    try:
        gcs_sock.sendto(json.dumps(payload).encode("utf-8"), (GCS_HOST, GCS_PORT))
    except Exception as e:
        print(f"[CC] GCS 전송 실패: {e}")


def mirror_to_recon(msg) -> None:
    """Mirror parsed MAVLink bytes to the passive recon listener without changing the C2 path."""
    global last_mirror_error_at
    if not RECON_MIRROR_ENABLED:
        return
    try:
        raw = bytes(msg.get_msgbuf())
        if raw:
            mirror_sock.sendto(raw, (RECON_MIRROR_HOST, RECON_MIRROR_PORT))
    except Exception as e:
        now = time.time()
        if now - last_mirror_error_at > 10:
            last_mirror_error_at = now
            print(f"[CC] Recon mirror 전송 실패: {e}")


def listen_commands():
    """GCS → CC → UAV FC 명령 경로"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", CMD_PORT))
    mav_fc = mavutil.mavlink_connection(
        f"udpout:{UAV_HOST}:{UAV_CMD_PORT}",
        source_system=255  # GCS SYS_ID로 위장
    )
    print(f"[CC] Command 수신 대기 → 포트 {CMD_PORT}")

    while True:
        data, addr = sock.recvfrom(4096)
        try:
            cmd = json.loads(data.decode())
        except Exception:
            continue

        command = cmd.get("command", "")
        src     = cmd.get("source", "?")
        print(f"[CC] Command 수신 ← {src} ({addr[0]}): {command}")

        if command == "LAND":
            mav_fc.mav.command_long_send(
                target_system=SYS_ID, target_component=1,
                command=mavutil.mavlink.MAV_CMD_NAV_LAND,
                confirmation=0,
                param1=0, param2=0, param3=0, param4=0,
                param5=0, param6=0, param7=0
            )
            print(f"[CC] MAVLink LAND → UAV FC {UAV_HOST}:{UAV_CMD_PORT}")

        elif command == "RTB":
            mav_fc.mav.command_long_send(
                target_system=SYS_ID, target_component=1,
                command=mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
                confirmation=0,
                param1=0, param2=0, param3=0, param4=0,
                param5=0, param6=0, param7=0
            )
            print(f"[CC] MAVLink RTB → UAV FC {UAV_HOST}:{UAV_CMD_PORT}")

        else:
            print(f"[CC] 알 수 없는 명령: {command}")


def main():
    threading.Thread(target=listen_commands, daemon=True).start()

    mav = mavutil.mavlink_connection(f"udpin:{MAVLINK_HOST}:{MAVLINK_PORT}")
    print("[CC] Companion Computer 시작")
    print(f"[CC] MAVLink 수신 ← {MAVLINK_HOST}:{MAVLINK_PORT}  (UAV FC 브로드캐스트)")
    print(f"[CC] Telemetry 전송 → {GCS_HOST}:{GCS_PORT}  (GCS)")
    if RECON_MIRROR_ENABLED:
        print(f"[CC] Recon mirror → {RECON_MIRROR_HOST}:{RECON_MIRROR_PORT}  (passive copy)")
    else:
        print("[CC] Recon mirror 비활성화")
    print(f"[CC] Command 수신 → 포트 {CMD_PORT}  (GCS → CC)")
    print("-" * 50)

    while True:
        msg = mav.recv_match(blocking=True, timeout=5)
        if msg is None:
            continue

        msg_type = msg.get_type()
        sys_id   = msg.get_srcSystem()
        seq      = msg._header.seq
        mirror_to_recon(msg)

        if msg_type == "HEARTBEAT":
            state["sys_id"] = sys_id
            state["mode"]   = msg.base_mode
            print(f"[CC] HEARTBEAT   | SYS_ID={sys_id} | SEQ={seq}")

        elif msg_type == "SYS_STATUS":
            state["fuel"] = msg.battery_remaining
            print(f"[CC] SYS_STATUS  | fuel={state['fuel']}% | SEQ={seq}")

        elif msg_type == "GLOBAL_POSITION_INT":
            state["lat"]   = msg.lat / 1e7
            state["lon"]   = msg.lon / 1e7
            state["alt"]   = msg.alt / 1000
            state["speed"] = round(((msg.vx ** 2 + msg.vy ** 2) ** 0.5) / 100 * 3.6, 1)
            print(f"[CC] POSITION    | lat={state['lat']} lon={state['lon']} alt={state['alt']}m | SEQ={seq}")

            if "fuel" in state:
                payload = {
                    "platform_id":   PLATFORM_ID,
                    "platform_type": "UAV",
                    "message_type":  "telemetry",
                    "source":        "companion_computer/MAVLink",
                    "seq":           seq,
                    **state,
                    "timestamp": time.time(),
                }
                send_to_gcs(payload)
                print(f"[CC] → GCS {GCS_HOST}:{GCS_PORT}  SEQ={seq}")

        elif msg_type == "MISSION_ITEM_REACHED":
            wp_seq = msg.seq
            print(f"[CC] MISSION_ITEM_REACHED | WP{wp_seq + 1} (seq={wp_seq})")
            payload_wp = {
                "platform_id":   PLATFORM_ID,
                "platform_type": "UAV",
                "message_type":  "mission_item_reached",
                "event":         "MISSION_ITEM_REACHED",
                "wp_seq":        wp_seq,
                **state,
                "timestamp": time.time(),
            }
            send_to_gcs(payload_wp)


if __name__ == "__main__":
    main()
