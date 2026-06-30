"""
JAMMER Agent — TMMR 광대역 전파 재밍 시뮬레이터
VHF + UHF + HF 채널에 반복적으로 재밍 신호 주입 → 링크 품질 저하/두절 유도
"""
import json
import os
import socket
import time

ROUTER_HOST    = os.getenv("ROUTER_HOST",    "dah-tactical-router")
JAM_PORT       = int(os.getenv("JAM_PORT",   "14590"))
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))

JAM_CHANNELS  = ["VHF", "UHF", "HF"]  # 주 채널 + 백업 채널까지 광대역 재밍
JAM_DURATION  = 14                    # 1회 재밍 지속 시간 (초)
JAM_INTERVAL  = 6                     # 재주입 간격 (초)

_jam_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_evt_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _send_event(source, message, level="warn", detail="", status=""):
    evt = {
        "platform_type": "AGENT",
        "agent_type":    "ATK",
        "platform_id":   "ATK-JAMMER",
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


def jam_channel(channel: str, duration: float):
    pkt = json.dumps({"channel": channel, "duration": duration}).encode()
    _jam_sock.sendto(pkt, (ROUTER_HOST, JAM_PORT))
    print(f"[JAMMER] ⚡ JAM 주입 → 채널={channel}  {duration}s  @{ROUTER_HOST}:{JAM_PORT}")


def main():
    print("[JAMMER] 전파 재밍 에이전트 시작")
    _send_event("JAMMER", "전파 재밍 에이전트 시작",
                level="info",
                detail=f"대상 채널: {', '.join(JAM_CHANNELS)}  Router={ROUTER_HOST}:{JAM_PORT}")

    time.sleep(3)

    count = 1
    while True:
        print(f"[JAMMER] ── 재밍 {count}회차 시작 ──────────────────────────")
        _send_event("JAMMER",
                    f"재밍 {count}회차 — VHF/UHF/HF 광대역 전파 방해 시작",
                    detail=f"채널: {', '.join(JAM_CHANNELS)}  지속={JAM_DURATION}s",
                    status="INJECTED")

        for ch in JAM_CHANNELS:
            jam_channel(ch, JAM_DURATION)

        time.sleep(JAM_INTERVAL)
        count += 1
        time.sleep(1)


if __name__ == "__main__":
    main()
