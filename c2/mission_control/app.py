import json
import socket
import threading
import time
from collections import deque
from flask import Flask, jsonify, request

LISTEN_PORT = 14540

app = Flask(__name__)

platforms = {}
events = deque(maxlen=80)


def add_event(level, source, message):
    events.appendleft({
        "time": time.strftime("%H:%M:%S"),
        "level": level,
        "source": source,
        "message": message,
    })


def udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    print(f"[MISSION] C2 Mission Control UDP {LISTEN_PORT} 수신 시작")

    while True:
        data, _ = sock.recvfrom(8192)
        try:
            payload = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            add_event("warn", "MISSION", "invalid payload dropped")
            continue

        platform_id = payload.get("platform_id", "UNKNOWN")
        platforms[platform_id] = payload
        add_event(
            "info",
            platform_id,
            f"{payload.get('platform_type')} telemetry seq={payload.get('seq')} link={payload.get('link')}",
        )


@app.post("/api/companion")
def companion():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"status": "error"}), 400
    platform_id = payload.get("platform_id", "UNKNOWN")
    platforms[platform_id] = payload
    add_event("info", platform_id,
              f"CC telemetry seq={payload.get('seq')} alt={payload.get('alt')}m fuel={payload.get('fuel')}%")
    return jsonify({"status": "ok"})


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/platforms")
def get_platforms():
    return jsonify(list(platforms.values()))


@app.get("/api/events")
def get_events():
    return jsonify(list(events))


@app.get("/api/dashboard")
def dashboard():
    return jsonify({
        "platforms": list(platforms.values()),
        "events": list(events)[:20],
        "links": {
            "ticn": "NORMAL",
            "satcom": "DEGRADED",
            "tdl": "ACTIVE",
        },
    })


if __name__ == "__main__":
    threading.Thread(target=udp_listener, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
