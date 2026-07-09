"""
Virtual Tactical Router / TIPS
역할:
  - GCS 전술망 연동 데이터 수신 (포트 14560)
  - TMMR / TICN 시뮬레이션 적용
  - Upper C2/BMS로 상황 데이터 전달 (포트 14545)
  - Upper C2/BMS 명령 수신 (포트 14546) → GCS로 전달 (포트 14562)
  - JAM 이벤트 수신 (포트 14590)
  - HTTP 상태 API (포트 8080)
※ MAVLink / ROS2 직접 해석 없음 — GCS가 변환한 전술망 데이터만 처리
"""
import json
import math
import os
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from ticn import TMMRNode, TICNNetwork, SharedState

# 지연 주입 상태 (delay attack)
_delay_state = {"delay_ms": 0, "expires_at": 0.0}
_delay_lock  = threading.Lock()


def get_inject_delay_ms() -> int:
    with _delay_lock:
        if time.time() < _delay_state["expires_at"]:
            return _delay_state["delay_ms"]
        return 0


# ── 포트 설정 ─────────────────────────────────────────────────────────────
GCS_LISTEN_PORT   = int(os.getenv("GCS_LISTEN_PORT",   "14560"))  # GCS → Router (UAV 전술 릴레이)
UGV_LISTEN_PORT   = int(os.getenv("UGV_LISTEN_PORT",   "14660"))  # UGV → Router (직접)
C2_CMD_IN_PORT    = int(os.getenv("C2_CMD_IN_PORT",    "14546"))  # Upper C2 → Router (명령)
JAM_LISTEN_PORT   = int(os.getenv("JAM_LISTEN_PORT",   "14590"))  # JAM 이벤트
STATUS_PORT       = int(os.getenv("STATUS_PORT",       "8080"))

UPPER_C2_HOST     = os.getenv("UPPER_C2_HOST",    "mission-control")
UPPER_C2_PORT     = int(os.getenv("UPPER_C2_PORT",    "14545"))  # Router → Upper C2

GCS_HOST          = os.getenv("GCS_HOST",          "dah-gcs")
GCS_CMD_PORT      = int(os.getenv("GCS_CMD_PORT",      "14562"))  # Router → GCS (명령 하달)

DASHBOARD_HOST    = os.getenv("DASHBOARD_HOST",    "dah-dashboard")
DASHBOARD_PORT    = int(os.getenv("DASHBOARD_PORT",    "14571"))  # Router → Dashboard (UGV fan-out)

ROUTER_LAT = float(os.getenv("ROUTER_LAT", "37.85"))
ROUTER_LON = float(os.getenv("ROUTER_LON", "126.85"))


# ── HTTP 상태 API ──────────────────────────────────────────────────────────

def make_http_handler(shared: SharedState, tmmr_nodes: dict, ticn: TICNNetwork):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def _send(self, code: int, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):
            if self.path in ("/api/ticn", "/api/ticn/status"):
                body = json.dumps({
                    "tmmr": {pid: n.to_dict() for pid, n in tmmr_nodes.items()},
                    "ticn": ticn.status(),
                    "jammed_channels": shared.jammed_remaining(),
                    "recent_events":   shared.recent_events(15),
                }).encode()
                self._send(200, body)
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length) or b'{}')

            if self.path == "/api/ticn/jam":
                ch, dur = body.get("channel", "VHF"), float(body.get("duration", 30))
                shared.jam(ch, dur)
                self._send(200, json.dumps({"ok": True, "channel": ch, "duration": dur}).encode())
            elif self.path == "/api/ticn/clear":
                ch = body.get("channel", "VHF")
                shared.clear_jam(ch)
                self._send(200, json.dumps({"ok": True, "channel": ch}).encode())
            elif self.path == "/api/ticn/delay":
                delay_ms = int(body.get("delay_ms", 0))
                duration = float(body.get("duration", 0))
                with _delay_lock:
                    _delay_state["delay_ms"]  = delay_ms
                    _delay_state["expires_at"] = time.time() + duration if delay_ms > 0 else 0.0
                print(f"[TICN]  DELAY 주입: {delay_ms}ms  {duration}s")
                self._send(200, json.dumps({"ok": True, "delay_ms": delay_ms, "duration": duration}).encode())
            else:
                self._send(404, b'{"error":"not found"}')

    return Handler


# ── 유틸 ──────────────────────────────────────────────────────────────────

def bind_udp(port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", port))
    return s


def haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat/2)**2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon/2)**2)
    return R * 2 * math.asin(math.sqrt(a))


def jam_udp_listener(shared: SharedState):
    sock = bind_udp(JAM_LISTEN_PORT)
    print(f"[TICN]  JAM 수신 대기  :{JAM_LISTEN_PORT}")
    while True:
        try:
            data, _ = sock.recvfrom(1024)
            msg = json.loads(data.decode())
            shared.jam(msg.get("channel", "VHF"), float(msg.get("duration", 30)))
        except Exception as e:
            print(f"[TICN]  JAM 파싱 오류: {e}")


def c2_cmd_listener(out_sock: socket.socket):
    """Upper C2/BMS 명령 수신 → GCS로 전달 (TMMR/TICN 역방향 경로)"""
    sock = bind_udp(C2_CMD_IN_PORT)
    print(f"[ROUTER] Upper C2 명령 수신 대기  :{C2_CMD_IN_PORT}")
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            cmd = json.loads(data.decode())
            cmd["via"] = f"Upper C2 → TICN → TMMR → Router → GCS"
            cmd["router_forwarded_at"] = time.time()
            out_sock.sendto(json.dumps(cmd).encode(), (GCS_HOST, GCS_CMD_PORT))
            print(f"[ROUTER] C2 명령 [{cmd.get('command')}] → GCS:{GCS_CMD_PORT}")
        except Exception as e:
            print(f"[ROUTER] C2 명령 처리 오류: {e}")


# ── 메인 ──────────────────────────────────────────────────────────────────

def main():
    shared      = SharedState()
    tmmr_nodes: dict[str, TMMRNode] = {}
    ticn        = TICNNetwork()

    http_srv = HTTPServer(("0.0.0.0", STATUS_PORT), make_http_handler(shared, tmmr_nodes, ticn))
    threading.Thread(target=http_srv.serve_forever, daemon=True).start()
    print(f"[TICN]  HTTP API  :{STATUS_PORT}  →  /api/ticn/status")

    threading.Thread(target=jam_udp_listener, args=(shared,), daemon=True).start()

    gcs_sock = bind_udp(GCS_LISTEN_PORT)
    ugv_sock = bind_udp(UGV_LISTEN_PORT)
    out_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    threading.Thread(target=c2_cmd_listener, args=(out_sock,), daemon=True).start()

    print("[ROUTER] ── Virtual Tactical Router / TIPS 시작 ──────────────────")
    print(f"         GCS(UAV) 수신  :{GCS_LISTEN_PORT}  UGV 수신  :{UGV_LISTEN_PORT}")
    print(f"         Upper C2  → {UPPER_C2_HOST}:{UPPER_C2_PORT}")
    print(f"         C2 명령   :{C2_CMD_IN_PORT}  → GCS:{GCS_CMD_PORT}")

    while True:
        readable, _, _ = select.select([gcs_sock, ugv_sock], [], [], 1)
        for sock in readable:
            data, addr = sock.recvfrom(8192)
            try:
                payload = json.loads(data.decode())
            except json.JSONDecodeError:
                continue

            payload["router_received_at"] = time.time()
            pid = payload.get("platform_id", "UNKNOWN")

            # TMMR 노드 초기화
            if pid not in tmmr_nodes:
                tmmr_nodes[pid] = TMMRNode(pid)
            tmmr = tmmr_nodes[pid]

            lat     = payload.get("lat") or 0
            lon     = payload.get("lon") or 0
            alt     = payload.get("alt") or 0
            dist_km = haversine(ROUTER_LAT, ROUTER_LON, lat, lon) if (lat and lon) else 0.0

            # TMMR 레이어 시뮬레이션
            jammed = shared.active_jammed()
            tmmr.adapt_waveform_for_distance(dist_km, jammed, lambda ev: (shared.log(ev), ticn.log(ev)))
            tmmr.update_rssi(dist_km, alt, jammed)
            tmmr.auto_hop(jammed, lambda ev: (shared.log(ev), ticn.log(ev)))
            tmmr.adjust_tx_power(dist_km)

            # TICN 레이어 시뮬레이션
            ticn.update_link(pid, dist_km, tmmr)
            result = ticn.route(payload, tmmr)

            if result is None:
                lq = ticn.links.get(pid)
                drop_event = {
                    "platform_type": "NETWORK",
                    "platform_id": "TICN-LINK",
                    "target_platform_id": pid,
                    "source": "TICN",
                    "message": f"{pid} 통신 두절 — 패킷 드롭",
                    "detail": f"LQ={lq.quality if lq else '?'} loss={lq.loss_pct if lq else '?'}% blackout={tmmr.blackout}",
                    "level": "warn",
                    "status": "OFFLINE",
                    "time": time.strftime("%H:%M:%S"),
                    "tmmr": tmmr.to_dict(),
                    "ticn": {
                        "link_quality": lq.quality if lq else 0,
                        "loss_pct": lq.loss_pct if lq else 100.0,
                        "dist_km": lq.dist_km if lq else 0.0,
                    },
                }
                try:
                    out_sock.sendto(json.dumps(drop_event).encode(), (DASHBOARD_HOST, DASHBOARD_PORT))
                except Exception:
                    pass
                print(f"[TICN]  DROP  {pid}  LQ={lq.quality if lq else '?'}  jam={tmmr.jam_detected}")
                continue

            # 지연 주입 적용
            inject_ms = get_inject_delay_ms()
            if inject_ms > 0:
                time.sleep(inject_ms / 1000.0)
                result["cmd_latency_ms"] = inject_ms
                print(f"[TICN]  DELAY 적용 {inject_ms}ms → {pid}")

            # Upper C2/BMS로 전달
            try:
                out_sock.sendto(json.dumps(result).encode(), (UPPER_C2_HOST, UPPER_C2_PORT))
            except Exception as e:
                print(f"[ROUTER] Upper C2 전송 실패: {e}")

            # 모든 플랫폼 — TICN 처리 결과(tmmr/ticn 포함)를 Dashboard로 fan-out
            try:
                out_sock.sendto(json.dumps(result).encode(), (DASHBOARD_HOST, DASHBOARD_PORT))
            except Exception as e:
                print(f"[ROUTER] Dashboard 전송 실패: {e}")

            t = result.get("tmmr", {})
            n = result.get("ticn", {})
            print(
                f"[TICN]  {pid}  wf={t.get('waveform')}  "
                f"RSSI={t.get('rssi_dbm')}dBm  TX={t.get('tx_power_pct')}%  "
                f"LQ={n.get('link_quality')}  loss={n.get('loss_pct')}%  "
                f"dist={n.get('dist_km')}km  → Upper C2"
            )


if __name__ == "__main__":
    main()
