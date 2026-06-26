import json
import os
import socket
import threading
import time

ROUTER_HOST = os.getenv("ROUTER_HOST", "dah-tactical-router")
ROUTER_PORT = int(os.getenv("ROUTER_PORT", "14660"))
CMD_PORT    = int(os.getenv("CMD_PORT", "14661"))

PLATFORM_ID = "UGV-001"
MISSION     = "GROUND_RECON"
BASE_LAT    = 37.901
BASE_LON    = 126.792

state = {"stopped": False, "speed_override": None}


def listen_commands():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", CMD_PORT))
    print(f"[UGV] 명령 수신 대기 → 포트 {CMD_PORT}")
    while True:
        data, _ = sock.recvfrom(4096)
        try:
            cmd     = json.loads(data.decode())
            command = cmd.get("command", "")
            src     = cmd.get("source", "?")
            if command == "STOP":
                state["stopped"]        = True
                state["speed_override"] = 0
                print(f"[UGV] ⛔ STOP 수신 ← {src}")
            elif command == "FORWARD":
                state["stopped"]        = False
                state["speed_override"] = cmd.get("speed", 2.0)
                print(f"[UGV] ▶ FORWARD 수신 ← {src} speed={state['speed_override']}")
            elif command == "SPEED_UP":
                cur = state.get("speed_override") or 2.0
                state["speed_override"] = min(cur + 1, 5)
                state["stopped"]        = False
                print(f"[UGV] ↑ SPEED_UP → {state['speed_override']}")
            elif command == "SPEED_DOWN":
                cur = state.get("speed_override") or 2.0
                state["speed_override"] = max(cur - 1, 0)
                print(f"[UGV] ↓ SPEED_DOWN → {state['speed_override']}")
        except Exception as e:
            print(f"[UGV] 명령 파싱 오류: {e}")


def main():
    threading.Thread(target=listen_commands, daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq  = 1
    print(f"[UGV] 전술 지상 플랫폼 시작 → {ROUTER_HOST}:{ROUTER_PORT}")

    while True:
        if state["stopped"]:
            speed = 0
        elif state["speed_override"] is not None:
            speed = state["speed_override"]
        else:
            speed = 18 + (seq % 4)

        offset  = (seq % 30) * 0.00008
        payload = {
            "platform_id":   PLATFORM_ID,
            "platform_type": "UGV",
            "message_type":  "telemetry",
            "mission":       MISSION,
            "seq":           seq,
            "lat":           BASE_LAT + offset,
            "lon":           BASE_LON + offset / 2,
            "speed":         speed,
            "battery":       91 - (seq % 10),
            "sensor_status": "ACTIVE",
            "link":          "TICN/CNRS",
            "status":        "STOPPED" if state["stopped"] else "ACTIVE",
            "timestamp":     time.time(),
        }
        sock.sendto(json.dumps(payload).encode("utf-8"), (ROUTER_HOST, ROUTER_PORT))
        print(f"[UGV] SEQ={seq} | speed={speed} | status={payload['status']}")
        seq += 1
        time.sleep(1.5)


if __name__ == "__main__":
    main()
