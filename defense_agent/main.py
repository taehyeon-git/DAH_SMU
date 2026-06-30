import json
import os
import socket
import threading
import time
import urllib.request

os.environ['MAVLINK20'] = '1'
from pymavlink import mavutil
from detector import detect
from responder import respond

LISTEN_HOST    = '0.0.0.0'
LISTEN_PORT    = 14551
ALLOWED_GCS_ID = 255
CHECK_INTERVAL = 0.5

DASHBOARD_URL  = f"http://{os.getenv('DASHBOARD_HOST', 'dah-dashboard')}:8080"
ROUTER_URL     = f"http://{os.getenv('ROUTER_HOST', 'dah-tactical-router')}:8080"
JAM_LOSS_THRESHOLD = 50   # loss_pct 이상이면 재밍 의심

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))

alerts   = []
last_seq = {}
_evt_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _send(source, message, level="info", detail="", status=""):
    evt = {
        "platform_type": "AGENT",
        "agent_type":    "DEF",
        "platform_id":   "DEF-001",
        "source":        source,
        "message":       message,
        "detail":        detail,
        "level":         level,
        "status":        status,
        "time":          time.strftime("%H:%M:%S"),
    }
    try:
        _evt_sock.sendto(json.dumps(evt).encode(), (DASHBOARD_HOST, DASHBOARD_PORT))
    except Exception:
        pass


def monitor():
    mav = mavutil.mavlink_connection(f'udpin:{LISTEN_HOST}:{LISTEN_PORT}')
    print(f"[DEFENSE] 감시 시작 → 포트 {LISTEN_PORT}")
    _send("MONITOR", "패킷 감시 시작", detail=f"UDP {LISTEN_PORT} 포트")

    while True:
        msg = mav.recv_match(blocking=True)
        if msg is None:
            continue

        msg_type = msg.get_type()
        src_id   = msg.get_srcSystem()
        seq      = msg._header.seq

        if msg_type == 'COMMAND_LONG':
            cmd = msg.command
            print(f"[DEFENSE] COMMAND_LONG 감지 | SYS_ID={src_id} | 명령={cmd} | SEQ={seq}")

            if src_id != ALLOWED_GCS_ID:
                alerts.append({
                    'type':   'UNKNOWN_SRC',
                    'src_id': src_id,
                    'cmd':    cmd,
                    'seq':    seq,
                })
                print(f"[DEFENSE] ⚠️  비정상 출처 → SYS_ID={src_id}")
                _send("MONITOR",
                      f"비정상 COMMAND_LONG 탐지",
                      level="warn",
                      detail=f"SYS_ID={src_id} (허용={ALLOWED_GCS_ID}) cmd={cmd} SEQ={seq}",
                      status="ALERT")
            else:
                _send("MONITOR",
                      f"정상 명령 수신",
                      detail=f"SYS_ID={src_id} cmd={cmd} SEQ={seq}")

        if src_id in last_seq:
            if seq <= last_seq[src_id]:
                alerts.append({
                    'type':   'REPLAY',
                    'src_id': src_id,
                    'seq':    seq,
                })
                print(f"[DEFENSE] ⚠️  Replay Attack 의심 → SEQ={seq} (이전={last_seq[src_id]})")
                _send("MONITOR",
                      f"Replay Attack 의심",
                      level="warn",
                      detail=f"SYS_ID={src_id} SEQ={seq} ≤ 이전={last_seq[src_id]}",
                      status="ALERT")

        last_seq[src_id] = seq


def jam_monitor():
    """TICN loss_pct 감시 — 재밍 탐지 시 주파수 전환으로 대응"""
    jam_active = False
    while True:
        time.sleep(3)
        try:
            with urllib.request.urlopen(f"{DASHBOARD_URL}/api/live", timeout=2) as r:
                data = json.loads(r.read())
            pmap = {p["platform_id"]: p for p in data.get("platforms", [])}
            uav  = pmap.get("UAV-001", {})
            loss = uav.get("ticn", {}).get("loss_pct", 0) or 0

            if loss >= JAM_LOSS_THRESHOLD and not jam_active:
                jam_active = True
                print(f"[DEFENSE] ⚠️  TICN 재밍 탐지 loss={loss}% — 주파수 전환 대응")
                _send("JAM-DETECTOR",
                      f"TMMR 재밍 탐지 — loss_pct={loss}%",
                      level="warn",
                      detail=f"VHF/UHF 채널 전파 방해 의심 → 주파수 전환 명령 전송",
                      status="ALERT")
                # Router에 채널 잼 해제 명령 (FREQ-HOP 시뮬레이션)
                for ch in ["VHF", "UHF"]:
                    try:
                        req = urllib.request.Request(
                            f"{ROUTER_URL}/api/ticn/clear",
                            data=json.dumps({"channel": ch}).encode(),
                            headers={"Content-Type": "application/json"},
                            method="POST"
                        )
                        urllib.request.urlopen(req, timeout=2)
                        print(f"[DEFENSE] ✅ FREQ-HOP 명령 전송 → 채널={ch} 재밍 해제")
                    except Exception as e:
                        print(f"[DEFENSE] FREQ-HOP 실패: {e}")
                _send("JAM-RESPONDER",
                      "FREQ-HOP 명령 전송 — VHF/UHF 재밍 해제",
                      level="info",
                      detail="Router TICN 채널 초기화 → 백업 파형으로 전환",
                      status="BLOCKED")

            elif loss < JAM_LOSS_THRESHOLD and jam_active:
                jam_active = False
                print(f"[DEFENSE] ✅ TICN 링크 복구 loss={loss}%")
                _send("JAM-DETECTOR",
                      f"TICN 링크 복구 — loss_pct={loss}%",
                      level="info",
                      detail="재밍 해제 확인 — UAV 임무 복귀 예상",
                      status="OK")
        except Exception:
            pass


def spoof_monitor():
    """GPS 스푸핑 탐지 — GCS가 플래그한 gps_spoofed 감시"""
    spoof_active = False
    while True:
        time.sleep(3)
        try:
            with urllib.request.urlopen(f"{DASHBOARD_URL}/api/live", timeout=2) as r:
                data = json.loads(r.read())
            pmap = {p["platform_id"]: p for p in data.get("platforms", [])}
            uav  = pmap.get("UAV-001", {})

            if uav.get("gps_spoofed") and not spoof_active:
                spoof_active = True
                speed = uav.get("implied_speed_kmh", "?")
                lat   = uav.get("lat", 0)
                lon   = uav.get("lon", 0)
                print(f"[DEFENSE] ⚠️  GPS 스푸핑 탐지 — 속도={speed}km/h lat={lat:.4f} lon={lon:.4f}")
                _send("GPS-DETECTOR",
                      f"GPS 좌표 위조 탐지 — 비정상 속도 {speed}km/h",
                      level="warn",
                      detail=f"위치: lat={lat:.4f} lon={lon:.4f}  "
                             f"물리적 불가능한 이동 속도 감지",
                      status="ALERT")
                _send("GPS-RESPONDER",
                      "안티스푸핑 대응 — 관성항법(INS) 모드 전환",
                      level="info",
                      detail="GPS 수신 잠금 / IMU 보정 기반 위치 추정 활성화",
                      status="BLOCKED")

            elif not uav.get("gps_spoofed") and spoof_active:
                spoof_active = False
                print("[DEFENSE] ✅ GPS 신호 정상화 — 위조 신호 해제")
                _send("GPS-DETECTOR",
                      "GPS 신호 정상화 — 위조 신호 해제 확인",
                      level="info",
                      detail="GPS 수신 복귀 / INS 보정 완료",
                      status="OK")
        except Exception:
            pass


def defense_loop():
    idle_ticks = 0
    while True:
        time.sleep(CHECK_INTERVAL)

        if not alerts:
            idle_ticks += 1
            if idle_ticks >= 20:  # 0.5s × 20 = 10초마다 상태 보고
                idle_ticks = 0
                _send("MONITOR",
                      "감시 중 — 이상 없음",
                      level="info",
                      detail=f"UDP {LISTEN_PORT} 포트 정상 감시 중",
                      status="OK")
            continue

        idle_ticks = 0
        current_alerts = alerts.copy()
        alerts.clear()

        threats = detect(current_alerts)
        if threats:
            print(f"[DEFENSE] 위협 {len(threats)}건 탐지 → 대응 시작")
            _send("DETECTOR",
                  f"위협 {len(threats)}건 탐지",
                  level="warn",
                  detail="; ".join(t.get('reason', '') for t in threats),
                  status="THREAT")
            respond(threats, _send)


def main():
    print(f"[DEFENSE] 방어 에이전트 시작")
    _send("DEFENSE", "방어 에이전트 시작",
          level="info",
          detail=f"monitor + detector + responder 통합 실행")

    threading.Thread(target=monitor,       daemon=True).start()
    threading.Thread(target=jam_monitor,   daemon=True).start()
    threading.Thread(target=spoof_monitor, daemon=True).start()
    defense_loop()


if __name__ == '__main__':
    main()
