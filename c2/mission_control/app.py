"""
Upper C2 / BMS (Battle Management System) Simulator
역할:
  - Tactical Router 경유 전술 상황 데이터 수신 (포트 14545)
  - 작전 상황 판단 / 자동 임무 결정
  - 작전 명령 하달 → Router (포트 14546) → GCS → CC → UAV
  ※ UAV/UGV 직접 명령 없음 — GCS 경유 Command로 변환됨
"""
import json
import socket
import threading
import time
from collections import deque
from flask import Flask, jsonify, request

# ── 포트 설정 ─────────────────────────────────────────────────────────────
LISTEN_PORT     = 14545           # Router → Upper C2 전술 데이터
ROUTER_HOST     = "tactical-router"
ROUTER_CMD_PORT = 14546           # Upper C2 → Router 명령 하달

app = Flask(__name__)

platforms: dict = {}
events = deque(maxlen=80)
_cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_airborne: dict = {}   # platform_id → 이륙 완료 여부 (alt > 300m 도달 후 True)


def add_event(level: str, source: str, message: str,
              event_type: str = "info", target: str = "", status: str = ""):
    entry = {
        "time":    time.strftime("%H:%M:%S"),
        "level":   level,
        "type":    event_type,
        "source":  source,
        "message": message,
    }
    if target: entry["target"] = target
    if status: entry["status"] = status
    events.appendleft(entry)


def issue_order(command: str, target: str, reason: str):
    """Upper C2 작전 명령 → Tactical Router 경유 GCS → CC → UAV"""
    order = {
        "command":   command,
        "target":    target,
        "source":    "Upper C2/BMS",
        "reason":    reason,
        "timestamp": time.time(),
    }
    try:
        _cmd_sock.sendto(json.dumps(order).encode(), (ROUTER_HOST, ROUTER_CMD_PORT))
        add_event("warn", "Upper C2/BMS", command,
                  event_type="command", target=target, status="SENT")
        print(f"[C2] 명령 하달 [{command}] → Router:{ROUTER_CMD_PORT}  사유={reason}")
    except Exception as e:
        print(f"[C2] 명령 전송 실패: {e}")


def evaluate_situation(payload: dict):
    """전술 상황 자동 평가 — 위험 조건 시 명령 자동 생성"""
    pid  = payload.get("platform_id", "UNKNOWN")
    fuel = payload.get("fuel")
    alt  = payload.get("alt")

    # 이륙 완료 추적 (300m 이상 도달 시 airborne 확정)
    if alt is not None and alt > 300:
        _airborne[pid] = True

    # fuel=-1 은 연료 미지원 sentinel 값 → 무시
    if fuel is not None and fuel >= 0 and fuel < 15:
        issue_order("RTB", pid, f"연료 임계치 미만 ({fuel}%)")
    # 이륙 완료 후 저고도 진입 시에만 RTB (이륙 중 오발 방지)
    elif alt is not None and alt < 50 and _airborne.get(pid):
        issue_order("RTB", pid, f"저고도 경보 ({alt}m)")


def udp_listener():
    """Router 경유 전술 데이터 수신"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", LISTEN_PORT))
    print(f"[C2] Upper C2/BMS UDP {LISTEN_PORT} 수신 시작")

    while True:
        data, _ = sock.recvfrom(8192)
        try:
            payload = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError:
            add_event("warn", "C2", "invalid payload dropped")
            continue

        pid = payload.get("platform_id", "UNKNOWN")
        platforms[pid] = payload
        add_event(
            "info", pid,
            f"{payload.get('platform_type')} telemetry seq={payload.get('seq')} "
            f"LQ={payload.get('ticn', {}).get('link_quality', '?')}"
        )
        evaluate_situation(payload)


# ── HTTP API ───────────────────────────────────────────────────────────────

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
        "events":    list(events)[:20],
        "links": {
            "ticn":   "NORMAL",
            "satcom": "DEGRADED",
            "tdl":    "ACTIVE",
        },
    })


@app.post("/api/order")
def manual_order():
    """
    운용자 수동 작전 명령 입력
    body: {"target": "UAV-001", "command": "RTB" | "LAND", "reason": "..."}
    """
    body = request.get_json(silent=True)
    if not body or "command" not in body:
        return jsonify({"status": "error", "message": "command 필드 필요"}), 400

    issue_order(
        command=body["command"],
        target=body.get("target", "ALL"),
        reason=body.get("reason", "Manual order via C2 API"),
    )
    return jsonify({"status": "issued", "command": body["command"]})


if __name__ == "__main__":
    threading.Thread(target=udp_listener, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)
