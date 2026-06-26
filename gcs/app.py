"""
GCS — Ground Control Station
역할: Tactical Router로부터 텔레메트리 수신 (UDP)
      운용자 Command → Router 경유 → CC → UAV FC (MAVLink)
"""
import json
import os
import socket
import threading
import time
from flask import Flask, jsonify, request

LISTEN_PORT     = int(os.getenv("LISTEN_PORT",     "14570"))
ROUTER_HOST     = os.getenv("ROUTER_HOST",     "tactical-router")
ROUTER_CMD_PORT = int(os.getenv("ROUTER_CMD_PORT", "14580"))

app = Flask(__name__)
platforms  = {}
event_log  = []
_cmd_sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    print(f"[GCS] 텔레메트리 수신 대기 → UDP {LISTEN_PORT}  (Tactical Router)")

    while True:
        data, _ = sock.recvfrom(8192)
        try:
            payload = json.loads(data.decode())
        except Exception:
            continue

        pid = payload.get("platform_id", "UNKNOWN")
        platforms[pid] = payload
        event_log.insert(0, {
            "time":    time.strftime("%H:%M:%S"),
            "source":  pid,
            "message": (
                f"telemetry seq={payload.get('seq')} "
                f"alt={payload.get('alt')}m "
                f"fuel={payload.get('fuel')}%"
            ),
        })
        if len(event_log) > 200:
            event_log.pop()
        print(f"[GCS] {pid} seq={payload.get('seq')} "
              f"alt={payload.get('alt')}m fuel={payload.get('fuel')}%")


threading.Thread(target=udp_listener, daemon=True).start()


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/status")
def status():
    return jsonify({
        "platforms": list(platforms.values()),
        "events":    event_log[:30],
        "count":     len(platforms),
    })


@app.post("/api/command")
def command():
    """
    GCS → Tactical Router → CC → UAV FC (MAVLink)
    body: {"target": "UAV-001", "command": "LAND" | "RTB"}
    """
    cmd = request.get_json(silent=True)
    if not cmd or "command" not in cmd:
        return jsonify({"status": "error", "message": "command 필드 필요"}), 400

    cmd["source"]    = "GCS"
    cmd["timestamp"] = time.time()

    try:
        _cmd_sock.sendto(
            json.dumps(cmd).encode(),
            (ROUTER_HOST, ROUTER_CMD_PORT)
        )
        print(f"[GCS] Command 전송 → Router {ROUTER_HOST}:{ROUTER_CMD_PORT}: {cmd['command']}")
        return jsonify({"status": "sent", "command": cmd["command"], "via": "Tactical Router"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
