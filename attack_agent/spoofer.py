"""
GPS SPOOFER Agent — GPS 좌표 위조 시뮬레이터
GCS에 가짜 위치 데이터를 주입하여 대시보드/지도에서 UAV가 허가구역 외로 이탈하는 것처럼 보이게 함
"""
import json
import math
import os
import socket
import time
import urllib.request

GCS_HOST       = os.getenv("GCS_HOST",       "dah-gcs")
GCS_PORT       = int(os.getenv("GCS_PORT",   "14555"))   # Companion → GCS 포트 위조
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "dah-dashboard")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "14571"))
DASHBOARD_URL  = f"http://{DASHBOARD_HOST}:8080"

# 스푸핑 목표: 정찰 경로 북쪽 허가구역 외 (북한 접경 방향)
SPOOF_TARGET_LAT = 38.50
SPOOF_TARGET_LON = 126.60
SPOOF_STEP       = 0.004   # 도/스텝 — 0.25s 주기로 ~440m 이동 → 탐지 가능한 비정상 속도
SPOOF_INTERVAL   = 0.25    # 초 — 정상 CC 텔레메트리보다 빠르게 주입

_gcs_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
_evt_sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def _send_event(source, message, level="warn", detail="", status=""):
    evt = {
        "platform_type": "AGENT",
        "agent_type":    "ATK",
        "platform_id":   "ATK-SPOOFER",
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


def get_uav_position():
    """현재 UAV 실제 위치 파악 (dashboard API)"""
    try:
        with urllib.request.urlopen(f"{DASHBOARD_URL}/api/live", timeout=2) as r:
            data = json.loads(r.read())
        for p in data.get("platforms", []):
            if p.get("platform_id") == "UAV-001":
                lat = p.get("lat")
                lon = p.get("lon")
                alt = p.get("alt", 3500)
                if lat and lon:
                    return lat, lon, alt
    except Exception:
        pass
    return 37.9, 126.85, 3500   # 위치 파악 실패 시 기본값


def main():
    print("[SPOOFER] GPS 좌표 위조 에이전트 시작")
    _send_event("SPOOFER", "GPS 스푸핑 에이전트 시작",
                level="warn",
                detail=f"대상: GCS {GCS_HOST}:{GCS_PORT}  "
                       f"목표좌표: {SPOOF_TARGET_LAT}N {SPOOF_TARGET_LON}E")

    time.sleep(3)

    lat, lon, alt = get_uav_position()
    print(f"[SPOOFER] UAV 현재 위치 파악: lat={lat:.5f} lon={lon:.5f}")
    _send_event("SPOOFER",
                f"UAV 위치 파악 완료 — 스푸핑 시작",
                level="warn",
                detail=f"시작: lat={lat:.4f} lon={lon:.4f}  목표: {SPOOF_TARGET_LAT}N {SPOOF_TARGET_LON}E",
                status="INJECTED")

    seq   = 9000
    count = 0

    while True:
        # 목표 방향 단위 벡터
        dlat = SPOOF_TARGET_LAT - lat
        dlon = SPOOF_TARGET_LON - lon
        dist = math.sqrt(dlat ** 2 + dlon ** 2)

        if dist > 0.01:
            step = min(SPOOF_STEP, dist)
            lat += dlat / dist * step
            lon += dlon / dist * step
        else:
            print("[SPOOFER] 목표 좌표 도달 — 위치 유지")

        # Companion Computer와 동일한 포맷으로 위장하되, 시뮬레이터 내부 탐지 태그만 추가
        payload = {
            "platform_id":   "UAV-001",
            "platform_type": "UAV",
            "message_type":  "telemetry",
            "source":        "companion_computer/MAVLink",
            "attack_type":   "GPS_SPOOF",
            "seq":           seq,
            "sys_id":        1,
            "mode":          192,
            "fuel":          78,
            "lat":           round(lat, 7),
            "lon":           round(lon, 7),
            "alt":           alt,
            "speed":         round(SPOOF_STEP / SPOOF_INTERVAL * 111000 / 3.6, 1),
            "hdg":           355.0,
            "timestamp":     time.time(),
        }

        try:
            _gcs_sock.sendto(json.dumps(payload).encode(), (GCS_HOST, GCS_PORT))
        except Exception as e:
            print(f"[SPOOFER] GCS 주입 실패: {e}")

        count += 1
        if count % 10 == 0:
            print(f"[SPOOFER] ⚡ {count}회 주입 — lat={lat:.4f} lon={lon:.4f}")
            _send_event("SPOOFER",
                        f"GPS 좌표 위조 {count}회 — lat={lat:.4f} lon={lon:.4f}",
                        detail=f"목표 {SPOOF_TARGET_LAT}N {SPOOF_TARGET_LON}E 방향 이동 중",
                        status="INJECTED")

        seq += 3
        time.sleep(SPOOF_INTERVAL)


if __name__ == "__main__":
    main()
