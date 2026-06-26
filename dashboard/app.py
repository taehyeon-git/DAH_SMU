import json
import os
import socket
import threading
import urllib.error
import urllib.request
from flask import Flask, jsonify, render_template

app = Flask(__name__)

MISSION_CONTROL_URL   = os.getenv("MISSION_CONTROL_URL", "http://mission-control:8080")
ROUTER_TELEMETRY_PORT = int(os.getenv("ROUTER_TELEMETRY_PORT", "14571"))

# Router로부터 직접 수신한 플랫폼 상태
router_platforms: dict = {}


def _router_udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", ROUTER_TELEMETRY_PORT))
    print(f"[DASH] Router 직수신 대기 → UDP {ROUTER_TELEMETRY_PORT}")
    while True:
        try:
            data, _ = sock.recvfrom(8192)
            payload = json.loads(data.decode())
            pid = payload.get("platform_id", "UNKNOWN")
            router_platforms[pid] = payload
        except Exception:
            pass


threading.Thread(target=_router_udp_listener, daemon=True).start()


TOPOLOGY = {
    "title": "UAV/UGV 전술통신 파이프라인",
    "subtitle": "UAV/UGV 텔레메트리가 Tactical Router를 거쳐 Mission Control과 Collector로 분배됩니다.",
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
            "role": "UAV/UGV 텔레메트리 fan-out",
            "status": "implemented",
            "ip": "uav_net/ops_net",
            "group": "network",
        },
        {
            "id": "mission",
            "name": "Mission Control",
            "label": "dah-mission-control",
            "role": "C2 상태 통합/API 제공",
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
            "target": "router",
            "protocol": "JSON telemetry / UDP",
            "port": "14560",
            "flow": "UAV 위치, 고도, 속도, ISR 상태",
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
            "source": "router",
            "target": "mission",
            "protocol": "JSON telemetry / UDP",
            "port": "14540",
            "flow": "C2 Mission Control 상태 통합",
            "status": "implemented",
        },
        {
            "source": "router",
            "target": "collector",
            "protocol": "JSON telemetry / UDP",
            "port": "14541",
            "flow": "전술 텔레메트리 로그 저장",
            "status": "implemented",
        },
    ],
    "notes": [
        "기본 파이프라인은 UAV/UGV -> Tactical Router -> Mission Control/Collector 입니다.",
        "Recon/Executor/Defense는 별도의 직접 공격/방어 실습 레이어로 유지됩니다.",
        "Mission Control API는 http://localhost:8082 로 확인할 수 있습니다.",
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


@app.get("/")
def index():
    return render_template("index.html", topology=TOPOLOGY)


@app.get("/api/topology")
def topology():
    return jsonify(TOPOLOGY)




@app.get("/api/live")
def live():
    dashboard = fetch_json("/api/dashboard")
    if dashboard is None:
        # Mission Control 불가 시 Router 직수신 데이터로 fallback
        return jsonify({
            "status": "degraded",
            "message": "mission-control unavailable (router direct)",
            "platforms": list(router_platforms.values()),
            "events": [],
        })
    # Mission Control 데이터 + Router 직수신 병합 (router 우선)
    mc_platforms = {p["platform_id"]: p for p in dashboard.get("platforms", [])}
    merged = {**mc_platforms, **router_platforms}
    return jsonify({
        "status": "ok",
        **dashboard,
        "platforms": list(merged.values()),
        "router_direct": len(router_platforms),
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
