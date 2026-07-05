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
│ dah-uav  172.31.50.10                                           │
│                                                                 │
│  ArduPlane SITL (TCP:5760 내부)                                   │
│       ↕ pymavlink                                               │
│  sitl_runner.py                                                 │
│  - HEARTBEAT (SYS_ID=1, MAV_TYPE_FIXED_WING) 1Hz               │
│  - SYS_STATUS (battery_remaining, drop_rate_comm)               │
│  - GLOBAL_POSITION_INT (lat, lon, alt, vx, vy, hdg)            │
│  - MISSION_ITEM_REACHED (wp_seq)                                │
│       ↓ MAVLink 2.0 / UDP 172.31.50.30:14550                    │
└─────────────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────────┐
│ dah-companion  172.31.50.30                                     │
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

#### Recon Mirror 경로

정찰 Phase 1이 실제 MAVLink-like 프레임을 수동 청취할 수 있도록, Companion Computer는 수신한 MAVLink 원본 바이트를 별도 mirror 대상으로 복제한다.

```text
dah-uav
  ↓ MAVLink / UDP :14550
dah-companion
  ├─→ dah-gcs :14555              정상 Telemetry JSON 경로
  └─→ dah-recon 172.31.50.40:14550  Passive Recon mirror 경로
```

이 mirror는 기존 C2/Telemetry 경로를 변경하지 않는다. `dah-recon`이 실행 중일 때만 Phase 1 수집에 사용되며, 정찰 컨테이너가 꺼져 있어도 Companion/GCS 흐름은 계속 동작한다.

관련 환경 변수:

| 변수 | 기본/설정값 | 의미 |
|---|---|---|
| `RECON_MIRROR_ENABLED` | `true` | MAVLink 원본 바이트 mirror 활성화 |
| `RECON_MIRROR_HOST` | `172.31.50.40` | Passive Recon 고정 IP |
| `RECON_MIRROR_PORT` | `14550` | Recon 수동 청취 포트 |

#### 명령 경로 (GCS → UAV)

명령 경로는 두 가지가 병렬 존재한다.

**① Dashboard 직접 명령** (운용자 C2 버튼 / 주 경로)

```
Dashboard /api/command  POST {"cmd": "RTB"}
    ↓ pymavlink MAVLink 2.0
    COMMAND_LONG (SYS_ID=255, target_system=1)
    ↓ UDP 172.31.50.10:14551
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
    ↓ MAVLink COMMAND_LONG / UDP 172.31.50.10:14551
dah-uav
```

#### GCS Heartbeat (연결 유지)

```
dashboard/app.py  _gcs_heartbeat_sender()  1Hz
    HEARTBEAT (SYS_ID=255, MAV_TYPE_GCS, MAV_STATE_ACTIVE)
    ↓ UDP 172.31.50.10:14551
dah-uav  gcs_heartbeat_watchdog()
    - elapsed < 5s → 정상 MISSION 유지
    - elapsed ≥ 5s → Fail-safe LOITER 전환  ← 공격 목표 지점
```

#### 안전 후속 시뮬레이션

현재 후속공격 단계는 실제 MAVLink 명령 주입이 아니라 로컬 Docker 테스트베드의 안전 이벤트로만 동작한다.

```text
FollowUpAttackAgent
  ├─ EW_LINK_DEGRADATION_SIM       → Router 링크저하 이벤트 + Dashboard FAILSAFE_LAND 오버레이
  └─ PROTOCOL_FRAME_INTEGRITY_SIM  → 합성 프레임 검증 실패 + Dashboard INTEGRITY_ALERT
```

`FAILSAFE_INDUCTION` 실행이 성공하면 Dashboard는 안전한 로컬 시뮬레이션으로 UAV 상태를 `FAILSAFE_LAND`로 전환한다.
이때 실제 SITL/MAVLink 제어 명령을 보내는 것이 아니라 `/api/live` 응답에서 UAV의 `lat/lon`을 고정하고 `speed=0`, `mission=FAILSAFE_STOPPED`로 표시하며, 고도를 점진적으로 낮춰 `FAILSAFE_LANDED` 상태까지 보여준다.

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

### 정찰/후속 시뮬레이션 통신

| 에이전트 | 통신 방식 | 공격 대상 |
|---------|---------|---------|
| `recon.py` | MAVLink 도청 (UDP :14550 수신) | UAV 텔레메트리 |
| `EW_LINK_DEGRADATION_SIM` | UDP JSON lab event | Router TICN 링크 |
| `PROTOCOL_FRAME_INTEGRITY_SIM` | 합성 프레임 alert JSON | Dashboard `/api/agent-event` |

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
Dashboard Live API: http://localhost:9000/api/live
```

### 실행 Profile 구분

기본 테스트베드는 profile 없이 실행한다. 이 명령은 UAV/UGV, GCS, Router, Dashboard만 올리며 공격 에이전트를 자동 실행하지 않는다.

```powershell
docker compose up -d --build
```

정찰은 기본적으로 `ReconAgent`가 실행한다. `ReconAgent`는 내부적으로 `dah-recon` 서비스를 실행해 passive mirror 수집을 수행한 뒤, 결과를 표준 `IntelDocument`로 정규화한다.

```powershell
python -m attack_agent.kill_chain --stage recon
```

이미 생성된 정찰 JSON만 다시 정규화하고 싶을 때는 아래 옵션을 사용한다.

```powershell
python -m attack_agent.kill_chain --stage recon --skip-recon-collection
```

## Recon-driven 공격 체인

정찰 결과를 사람이 읽는 JSON에서 끝내지 않고, `ReconAgent -> InitialAccessAgent -> FollowUpAttackAgent` 3단계로 연결한다.

```text
1. ReconAgent
   - 실행: passive MAVLink mirror 수집, Dashboard/Failsafe API 사전 정찰, 후속 에이전트 후보 매핑
   - 입력/중간 산출물: output/intel_handoff.json, output/passive_mavlink_intel.json
   - 출력: output/stage_1_recon.json
   - 역할: 모든 정찰 이벤트 실행 후 산출물을 표준 IntelDocument로 정규화

2. InitialAccessAgent
   - 입력: output/stage_1_recon.json
   - 출력: output/stage_2_initial_access.json, output/stage_2_attack_graph.json
   - 역할: API surface, 자산, 경로, GCS 모델, 후속공격 후보 생성

3. FollowUpAttackAgent
   - 입력: output/stage_2_initial_access.json
   - 출력: output/stage_3_attack_plan.json, output/stage_3_execution_report.json
   - 역할: AttackPlan 생성 후 dry-run 또는 명시적 안전 시뮬레이션 실행
```

전체 체인은 아래 문서에 정리되어 있다.

```text
attack_agent/README_CHAIN.md
```

기본은 dry-run이며, 실제 Docker lab 이벤트 실행은 `--execute`와 `ENABLE_LAB_ATTACKS=true`가 모두 있어야 한다.
체인 실행 시 PowerShell/Windows 호스트에서는 Docker 서비스명이 `localhost` 공개 포트로 자동 매핑되고, Docker 컨테이너 내부에서는 `dah-dashboard`, `dah-tactical-router` 같은 내부 DNS 이름이 그대로 사용된다.

### 3단계 Kill Chain 실행

ReconAgent가 정찰 수집 컨테이너 실행과 정규화를 한 번에 수행한다.

```powershell
python -m attack_agent.kill_chain --stage recon
```

정찰 시간을 줄이고 싶으면 아래처럼 조정한다.

```powershell
python -m attack_agent.kill_chain --stage recon --recon-duration-s 10 --recon-revalidate-s 5
```

정찰 결과를 기반으로 초기침투 분석과 attack graph를 생성한다.

```powershell
python -m attack_agent.kill_chain --stage initial-access
```

초기침투 분석 결과를 기반으로 후속공격 계획을 dry-run으로 확인한다.

```powershell
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --max-steps 1
```

명시적으로 안전 시뮬레이션 이벤트를 실행한다.

```powershell
$env:ENABLE_LAB_ATTACKS="true"
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --execute --max-steps 1
```

한 번에 전체 체인을 dry-run으로 돌릴 수도 있다.

```powershell
python -m attack_agent.kill_chain --stage all --objective PROTOCOL_INTEGRITY_TEST --max-steps 1
```

### 계획 생성과 실제 이벤트 전송 차이

체인 실행은 크게 세 단계로 나뉜다.

| 실행 방식 | 이벤트 전송 | 설명 |
|---|---:|---|
| 기본 follow-up 실행 | X | 전체 체인을 점검하지만 Dashboard/C2로 이벤트를 보내지 않음 |
| `ENABLE_LAB_ATTACKS=true` + `--execute` | O | Docker 내부 테스트베드로 안전 시뮬레이션 이벤트 전송 |

실제 이벤트 전송 예시는 아래와 같다.

```powershell
cd C:\Users\taehy\OneDrive\문서\UAS\DAH_SMU
docker compose up -d --build

$env:ENABLE_LAB_ATTACKS="true"
python -m attack_agent.kill_chain --stage follow-up --objective FAILSAFE_INDUCTION --execute --max-steps 1
```

합성 저수준 프레임 무결성 테스트를 C2 보고 경로로 보내려면 아래처럼 실행한다.

```powershell
$env:ENABLE_LAB_ATTACKS="true"
python -m attack_agent.kill_chain --stage follow-up --objective PROTOCOL_INTEGRITY_TEST --execute --max-steps 1
```

여기서 전송되는 이벤트는 실제 MAVLink/RF/UDP 공격 트래픽이 아니라, 로컬 Docker 테스트베드 안에서만 처리되는 안전한 시뮬레이션 이벤트다.  
`FAILSAFE_INDUCTION`은 대시보드 로컬 상태머신을 통해 UAV를 `FAILSAFE_LAND`로 표시하고, 현재 위치 고정 + 속도 0 + 고도 하강 오버레이를 적용한다.
상세한 실행 순서, 출력 파일, 지원 모듈, 합성 프레임 변조 모드는 `attack_agent/README_CHAIN.md`를 참고한다.
