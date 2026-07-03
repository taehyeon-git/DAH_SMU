# DAH 2026 UAV/UGV 위성 네트워크 기반 클라우드 가상 전장

---

## 프로젝트 개요

DAH - **UAV/UGV 전술 무인체계 통신 구조 시뮬레이션**입니다.

현재 대시보드는 UAV/UGV, GCS/Mission Control, Tactical Router, TICN-like Network 간의 Telemetry/Command 흐름과 링크 상태를 전장 시뮬레이션 형태로 시각화하며, AI 공격·방어 이벤트에 따른 상태 변화를 실시간으로 표시합니다.

## 아키텍처

```text
┌──────────────────────────── UAV / UGV Asset Layer ─────────────────────────────┐
│                                                                                │
│  ┌────────────────── UAV Simulator ──────────────────┐ ┌──── UGV Simulator ──┐ │
│  │ Autopilot / Flight Controller                      │ │ Vehicle Controller  ││
│  │ - Flight Control Logic                             │ │ - Mobility Control  ││
│  │ - Mission Command Execute                          │ │ - Command Execute   ││
│  │                                                    │ │                     ││
│  │ Companion Computer                                 │ │ Onboard / Mission   ││
│  │ - MAVLink-like Telemetry / Command                 │ │ Computer            ││
│  │ - Payload Status                                   │ │ - ROS2/MQTT-like    ││
│  │ - GCS Communication                                │ │   Telemetry         ││
│  │ - Command Receive / Forward                        │ │ - Sensor Status     ││
│  │                                                    │ │ - GCS Communication ││
│  └──────────────────────┬─────────────────────────────┘ └─────────┬──────────┘ │
└─────────────────────────┼─────────────────────────────────────────┼────────────┘
                          │ C2 Data Link                            │ C2 Data Link
                          │ Telemetry / Report ↓                    │ Telemetry / Report ↓
                          │ Command / Tasking ↑                     │ Command / Tasking ↑
                          ▼                                         ▼

┌───────────────────────────────────────────────────────────────────────────────┐
│ GCS / Ground Gateway / Mission Control Server                                 │
│ - UAV / UGV Telemetry 수신 및 해석                                             │
│ - 임무 상태 판단                                                               │
│ - 수동 조작 / Command 생성                                                     │
│ - Upper C2/BMS 명령 → UAV/UGV Command 변환                                    │
│ - 전술망 메시지 변환: 위치 / 상태 / 임무 / 표적 / 영상 메타데이터              │
└───────────────┬──────────────────────┬──────────────────────┬────────────────┘
                │                      │                      │
                ▼                      ▼                      ▼
   ┌────────────────────┐  ┌─────────────────────┐  ┌──────────────────────┐
   │ Dashboard          │  │ Telemetry           │  │ AI Defense Agent     │
   │ - 상태/지도 시각화  │  │ Collector / LogDB  │  │ - 실시간 상태 분석    │
   │ - 임무 표시         │  │ - Telemetry Log     │  │ - Command 무결성 검증│
   │ - 경고 표시         │  │ - Command Log       │  │ - 이상징후 탐지      │
   │ - 공격/방어 결과    │  │ - Network/Attack Log│  │ - 대응 정책 결정     │
   └────────────────────┘  └─────────────────────┘  └──────────┬───────────┘
                                                                │
                                                                ▼
                                                   Alert / Block / Quarantine
                                                   Re-route / Fallback / Review

               ▲
               │ 통제된 공격 이벤트 주입
┌──────────────┴──────────────────────────────────────────────────────────────┐
│ AI Attack Agent                                                             │
│ - Docker 가상 네트워크 내부 자동 공격 이벤트 생성                            │
│ - Telemetry 위조 / Command 변조 / GPS 이상 좌표 주입                         │
│ - 통신 지연 / 손실 / 차단 / 변조 이벤트                                      │
│ - AI Defense Agent 탐지 성능 검증                                            │
│ ※ 폐쇄형 UAV/UGV 도메인 가상 환경 내부에서만 동작                            │
└─────────────────────────────────────────────────────────────────────────────┘

                          │
                          │ 전술망 연동 데이터
                          │ Report / Situation Data ↓
                          │ Command / Tasking ↑
                          ▼

┌───────────────────────────────────────────────────────────────────────────────┐
│ Virtual Tactical Router / TIPS                                                │
│ - Docker Network 기반 가상 전술 라우터                                         │
│ - GCS / 전술망 간 IP 패킷 라우팅                                               │
│ - 지연 / 손실 / 차단 / 변조 이벤트 적용 지점                                   │
│ - QoS / 우선순위 처리 모사                                                     │
│ - GCS가 변환한 전술망 데이터 중계                                              │
│ ※ MAVLink / ROS2 직접 해석 없음                                               │
└────────────────────────┬──────────────────────────────────────────────────────┘
                         │ Report / Situation Data ↓
                         │ Command / Tasking ↑
                         ▼

┌───────────────────────────────────────────────────────────────────────────────┐
│ TMMR / 전투무선체계 (CNRS-series)                                              │
│ - 전술 무선 노드                                                               │
│ - 음성 / 데이터 송수신                                                         │
│ - TICN 접속 구간                                                               │
│ - 전술 무선 링크 모사                                                          │
└────────────────────────┬──────────────────────────────────────────────────────┘
                         │ Report / Situation Data ↓
                         │ Command / Tasking ↑
                         ▼

┌───────────────────────────────────────────────────────────────────────────────┐
│ TICN-like Tactical Network                                                    │
│ - 전술정보통신망 모사                                                          │
│ - 전술 데이터망                                                               │
│ - C4ISR / 지휘통제망 연동 흐름 모사                                            │
│ - 현장 전술 노드와 상위 지휘체계 연결                                          │
└────────────────────────┬──────────────────────────────────────────────────────┘
                         │ Report / Situation Data ↓
                         │ Command / Tasking ↑
                         ▼

┌───────────────────────────────────────────────────────────────────────────────┐
│ Upper C2 / BMS Simulator                                                      │
│ - 작전 상황 공유                                                              │
│ - 표적 / 좌표 공유                                                            │
│ - 감시 구역 지정                                                              │
│ - 임무 변경 지시                                                              │
│ - 상급부대 명령 하달                                                          │
│ ※ UAV/UGV 직접 명령 없음 — GCS 경유하여 Command로 변환                        │
└───────────────────────────────────────────────────────────────────────────────┘
```

## 구현 범위

## 구현 범위

본 프로젝트는 실제 군 통신망이나 장비를 구현하는 것이 아니라, Docker 기반 폐쇄형 UAV/UGV 가상 환경에서 Telemetry/Command 흐름과 AI 공격·방어 구조를 검증하는 통신 시뮬레이션이다.

UAV/UGV는 상태 생성, 임무 수행, Telemetry 전송, Command 수신 기능을 모사하며, GCS/Mission Control은 이를 수신·해석해 Dashboard, LogDB, AI Defense Agent로 분기한다.

AI Attack Agent는 통제된 공격 이벤트를 생성하고, AI Defense Agent는 Telemetry, Command Flow, Network Event, Mission State를 분석해 이상징후를 탐지한다.

본 시나리오의 핵심은 Heartbeat 이상, Link Quality 저하, Telemetry Gap 등 통신 상태 이상을 통해 UAV/UGV의 Fail-safe 전환 가능성을 검증하는 것이다.

## 네트워크 구성

본 프로젝트는 Docker 내부 네트워크에서 UAV/UGV, GCS, Tactical Router, Collector, Dashboard 간 Telemetry/Command 흐름을 구성한다.  
외부에서는 Dashboard와 주요 API만 접근하도록 구성한다.

### 내부 통신 포트

| 구간 | 포트 | 프로토콜 | 역할 |
|---|---:|---|---|
| UAV → Companion | `14550` | UDP / MAVLink | UAV Telemetry 전송 |
| Companion → UAV | `14551` | UDP / MAVLink | UAV Command 전달 |
| GCS → Companion | `14552` | UDP / JSON | GCS Command 전달 |
| Companion → GCS | `14555` | UDP / JSON | Telemetry JSON 변환 후 전달 |
| GCS → Router | `14560` | UDP / JSON | 전술망 연동 데이터 전달 |
| Router → GCS | `14562` | UDP / JSON | 상위 C2 명령 전달 |
| Router ↔ Upper C2/BMS | `14545 / 14546` | UDP / JSON | 전술 상황 데이터 및 작전 명령 송수신 |
| UGV ↔ Router | `14660 / 14661` | UDP / JSON | UGV Telemetry 및 Command 송수신 |
| Attack Event → Router | `14590` | UDP / JSON | 통제된 공격 이벤트 입력 |
| GCS → Collector | `14541` | UDP / JSON | Telemetry / Command / Event Log 저장 |
| GCS/Router → Dashboard | `14571` | UDP / JSON | 실시간 상태 시각화 데이터 전달 |

### 외부 접속 정보

| 서비스 | URL | 역할 |
|---|---|---|
| Dashboard / API Gateway | `http://localhost:9000` | 메인 대시보드 |
| GCS API | `http://localhost:9000/gcs/` | GCS 상태 및 명령 API |
| Upper C2/BMS API | `http://localhost:9000/c2/` | 상위 C2/BMS 상태 API |
| Tactical Router API | `http://localhost:9000/router/` | 가상 전술 라우터 상태 API |
| Router Direct API | `http://localhost:8084` | Router 직접 상태 확인 |

---

## 통신 구현 방식

### MAVLink (UAV ↔ GCS) 통신 구조

실제 [pymavlink](https://github.com/ArduPilot/pymavlink) 라이브러리 기반 **MAVLink 2.0** 프로토콜.  
UAV(`uav/sitl_runner.py`)는 실제 **ArduPilot SITL** 바이너리를 구동하여 항공 펌웨어의 비행 물리·Fail-safe·웨이포인트 로직을 그대로 사용한다.

#### 텔레메트리 경로 (UAV → GCS)

```
┌─────────────────────────────────────────────────────────────────┐
│ dah-uav  172.20.0.10                                            │
│                                                                 │
│  ArduPlane SITL (TCP:5760 내부)                                   │
│       ↕ pymavlink                                               │
│  sitl_runner.py                                                 │
│  - HEARTBEAT (SYS_ID=1, MAV_TYPE_FIXED_WING) 1Hz               │
│  - SYS_STATUS (battery_remaining, drop_rate_comm)               │
│  - GLOBAL_POSITION_INT (lat, lon, alt, vx, vy, hdg)            │
│  - MISSION_ITEM_REACHED (wp_seq)                                │
│       ↓ MAVLink 2.0 / UDP broadcast 172.20.0.255:14550          │
└─────────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────────┐
│ dah-companion  172.20.0.30                                      │
│  companion_computer/app.py                                      │
│  - udpin:0.0.0.0:14550 으로 MAVLink 수신                        │
│  - HEARTBEAT  → state["mode"] 갱신                              │
│  - SYS_STATUS → state["fuel"] 갱신                              │
│  - GLOBAL_POSITION_INT → state["lat","lon","alt","speed"] 갱신  │
│    → JSON 변환 후 GCS UDP 전송                                   │
│       ↓ JSON / UDP :14555                                        │
└─────────────────────────────────────────────────────────────────┘
                          ↓
              dah-gcs  (JSON 수신 후 fan-out)
              ├→ Dashboard  :14571
              ├→ Collector  :14541
              └→ Router     :14560
```

#### 명령 경로 (GCS → UAV)

명령 경로는 두 가지가 병렬 존재한다.

**① Dashboard 직접 명령** (운용자 C2 버튼 / 주 경로)

```
Dashboard /api/command  POST {"cmd": "RTB"}
    ↓ pymavlink MAVLink 2.0
    COMMAND_LONG (SYS_ID=255, target_system=1)
    ↓ UDP 172.20.0.10:14551
dah-uav  sitl_runner.py  → SITL TCP:5760 브리지
    → ArduPlane SITL 실행
```

지원 명령:

| cmd | MAVLink 명령 |
|-----|-------------|
| `HOLD` | `MAV_CMD_NAV_LOITER_UNLIM` |
| `PAUSE` | `MAV_CMD_DO_PAUSE_CONTINUE (param1=0)` |
| `RESUME` | `MAV_CMD_DO_PAUSE_CONTINUE (param1=1)` |
| `MONITOR` | `MAV_CMD_DO_CHANGE_SPEED` |
| `RTB` | `MAV_CMD_NAV_RETURN_TO_LAUNCH` |

**② Upper C2/BMS 명령** (상위 지휘체계 경로)

```
Upper C2/BMS
    ↓ JSON / UDP :14546
tactical-router  →  dah-gcs :14562  →  dah-companion :14552
    ↓ MAVLink COMMAND_LONG / UDP 172.20.0.10:14551
dah-uav
```

#### GCS Heartbeat (연결 유지)

```
dashboard/app.py  _gcs_heartbeat_sender()  1Hz
    HEARTBEAT (SYS_ID=255, MAV_TYPE_GCS, MAV_STATE_ACTIVE)
    ↓ UDP 172.20.0.10:14551
dah-uav  gcs_heartbeat_watchdog()
    - elapsed < 5s → 정상 MISSION 유지
    - elapsed ≥ 5s → Fail-safe LOITER 전환  ← 공격 목표 지점
```

#### 공격 에이전트 직접 주입

```
executor.py / failsafe_inducer.py  (SYS_ID=99, GCS 위장 시 SYS_ID=255)
    COMMAND_LONG (MAV_CMD_NAV_LAND 등)
    ↓ UDP 172.20.0.10:14551
dah-uav  listen_for_commands()
    - SYS_ID 검증 없음 → 공격 명령 그대로 실행  ← MAVLink 취약점
```

### Companion Computer (MAVLink → JSON 변환)

`companion_computer/app.py`가 MAVLink 바이너리 패킷을 수신하고 JSON으로 변환해 GCS UDP로 전달한다.  
역방향으로 GCS JSON 명령을 받아 MAVLink `COMMAND_LONG`으로 변환 후 UAV로 전달한다.

### TMMR / TICN 시뮬레이션 (tactical_router)

`tactical_router/router.py`와 `tactical_router/ticn.py`가 실제 TMMR/TICN 장비 동작을 소프트웨어로 모사한다.

| 계층 | 구현 방식 |
|------|---------|
| TMMR RF 링크 | Haversine 거리 공식으로 RSSI 계산, 재밍 시 신호 저하 |
| TICN 패킷 라우팅 | LQ(Link Quality) 기반 패킷 드롭 확률 계산 |
| 주파수 호핑 | 재밍 채널 감지 시 VHF→UHF→K-WNW 자동 전환 |
| 지연 주입 | `/api/ticn/delay` API로 `cmd_latency_ms` 삽입 |
| 재밍 주입 | `/api/ticn/jam` API로 `loss_pct` 강제 상승 |

### GCS → Dashboard / Collector / Router Fan-out

GCS는 수신한 텔레메트리를 UDP로 세 곳에 동시 전달한다.

```
dah-gcs
  ├─→ dah-dashboard    :14571  (실시간 지도·로그 표시)
  ├─→ telemetry-collector :14541 (로그 저장)
  └─→ tactical-router  :14560  (TMMR/TICN 시뮬레이션 후 Upper C2로 전달)
```

### 공격 에이전트 통신

| 에이전트 | 통신 방식 | 공격 대상 |
|---------|---------|---------|
| `recon.py` | MAVLink 도청 (UDP :14550 수신) | UAV 텔레메트리 |
| `executor.py` | pymavlink `COMMAND_LONG` 주입 (SYS_ID=99 위장) | UAV :14551 |
| `jammer.py` | HTTP POST `/api/ticn/jam` | Router TICN 링크 |
| `failsafe_inducer.py` | MAVLink `HEARTBEAT` 전송 + HTTP POST | UAV + Router |
| `heartbeat_spoofer.py` | pymavlink `HEARTBEAT` (SYS_ID=255 GCS 위장) | UAV :14551 |

### 방어 에이전트 통신

`defense_agent/main.py`는 3개 스레드를 병렬 실행한다.

- `monitor()`: UDP :14551 MAVLink 감시 — 비정상 SYS_ID·Replay Attack 탐지
- `jam_monitor()`: HTTP GET `/api/live` 3초 주기 폴링 — `loss_pct` 임계값(50%) 초과 시 FREQ-HOP 명령
- `spoof_monitor()`: HTTP GET `/api/live` 3초 주기 폴링 — `gps_spoofed` 플래그 감지 시 INS 전환 명령

---

## 실행

```powershell
docker compose up -d --build dah-dashboard
```

```text
Dashboard: http://localhost:9000
Mission Control API: http://localhost:9000/api/dashboard
```
