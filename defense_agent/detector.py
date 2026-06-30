from pymavlink import mavutil

# ─────────────────────────────────────────
# 탐지 규칙 설정
# ─────────────────────────────────────────
ALLOWED_GCS_ID  = 255    # 정상 GCS SYS_ID
ALLOWED_CMDS    = [      # 허용된 명령 목록
    mavutil.mavlink.MAV_CMD_NAV_WAYPOINT,   # 경유지 이동
    mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,    # 이륙
    mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,  # 복귀
]

# 탐지 결과 저장소 (responder.py가 읽어감)
threats = []


def detect(alerts):
    """
    monitor.py가 수집한 alerts 분석
    실제 위협인지 판단 후 threats에 저장
    """
    for alert in alerts:

        # ── 규칙 1: 허용되지 않은 SYS_ID에서 명령 온 경우
        if alert['type'] == 'UNKNOWN_SRC':
            print(f"[DETECTOR] 위협 탐지 — 비정상 출처")
            print(f"[DETECTOR] SYS_ID={alert['src_id']} | 명령={alert['cmd']}")

            threat = {
                'reason' : '허용되지 않은 SYS_ID에서 COMMAND_LONG 수신',
                'src_id' : alert['src_id'],
                'cmd'    : alert['cmd']
            }
            threats.append(threat)

        # ── 규칙 2: 허용되지 않은 명령 종류
        elif alert['type'] == 'UNKNOWN_SRC':
            if alert['cmd'] not in ALLOWED_CMDS:
                print(f"[DETECTOR] 위협 탐지 — 허용되지 않은 명령")
                print(f"[DETECTOR] 명령={alert['cmd']}")

                threat = {
                    'reason' : '허용되지 않은 COMMAND_LONG 명령',
                    'src_id' : alert['src_id'],
                    'cmd'    : alert['cmd']
                }
                threats.append(threat)

        # ── 규칙 3: Replay Attack
        elif alert['type'] == 'REPLAY':
            print(f"[DETECTOR] 위협 탐지 — Replay Attack")
            print(f"[DETECTOR] SYS_ID={alert['src_id']} | SEQ={alert['seq']}")

            threat = {
                'reason' : 'Replay Attack 탐지 (SEQ 역전)',
                'src_id' : alert['src_id'],
                'seq'    : alert['seq']
            }
            threats.append(threat)

    return threats


if __name__ == '__main__':
    # 단독 실행 시 테스트용 alert로 탐지 확인
    test_alerts = [
        {'type': 'UNKNOWN_SRC', 'src_id': 99, 'cmd': mavutil.mavlink.MAV_CMD_NAV_LAND, 'seq': 10},
        {'type': 'REPLAY',      'src_id': 99, 'seq': 5}
    ]
    result = detect(test_alerts)
    print(f"\n[DETECTOR] 탐지된 위협 총 {len(result)}건")
    for t in result:
        print(f"  → {t['reason']}")